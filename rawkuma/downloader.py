"""第三方下载器对接：Rayburst（底层 Aria2）aria2 兼容 JSON-RPC。

下载器只需保持在线并开启 RPC（Rayburst 默认监听 127.0.0.1:16800/jsonrpc，
密钥默认 token，可在配置里修改）。爬虫把每个图片作为独立任务交给下载器，
由下载器负责实际传输，完成后爬虫校验文件并登记到本地库。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .client import SiteClient, SiteError
from .parser import ChapterRef, MangaInfo

DONE_STATUS = {"complete", "error", "removed"}


class DownloaderError(Exception):
    """下载器（Rayburst RPC）错误。"""


@dataclass
class ChapterResult:
    chapter: ChapterRef
    ok: bool = False
    pages: int = 0
    files: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    message: str = ""


class Aria2Rpc:
    """aria2 JSON-RPC 客户端。"""

    def __init__(self, rpc_url: str, secret: str = "", timeout: float = 10.0):
        self.rpc_url = rpc_url
        self.secret = secret
        self._id = 0
        self._http = httpx.Client(timeout=timeout)

    def _token_params(self, params: list) -> list:
        if self.secret:
            return [f"token:{self.secret}", *params]
        return params

    def call(self, method: str, *params: Any) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": self._token_params(list(params)),
        }
        try:
            resp = self._http.post(self.rpc_url, json=payload)
        except httpx.HTTPError as exc:
            raise DownloaderError(
                "无法连接下载器（Rayburst），请确认其已启动且 RPC 已开启。"
                f"（{self.rpc_url}）\n可在 config.json 的 downloader 段调整地址/密钥。"
            ) from exc
        if resp.status_code != 200:
            raise DownloaderError(f"下载器 RPC 返回 HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise DownloaderError(
                f"下载器 RPC 错误: {data['error'].get('message', data['error'])}"
            )
        return data.get("result")

    def add_uri(
        self,
        uri: str,
        dir_path: str,
        out_name: str,
        referer: str | None = None,
        user_agent: str | None = None,
        connections: int = 4,
    ) -> str:
        options: dict[str, str] = {
            "dir": dir_path,
            "out": out_name,
            "max-connection-per-server": str(connections),
            "split": str(connections),
            "allow-overwrite": "true",
            "auto-file-renaming": "false",
            "retry-wait": "3",
            "max-tries": "5",
        }
        if referer:
            options["referer"] = referer
        if user_agent:
            options["user-agent"] = user_agent
        gid = self.call("aria2.addUri", [uri], options)
        return str(gid)

    def tell_status(self, gid: str) -> dict:
        return self.call(
            "aria2.tellStatus",
            gid,
            [
                "gid",
                "status",
                "totalLength",
                "completedLength",
                "files",
                "errorMessage",
            ],
        )

    def wait(
        self, gids: list[str], timeout: float, poll: float = 1.0
    ) -> dict[str, dict]:
        """轮询至全部任务结束，返回 {gid: status}。"""
        deadline = time.time() + timeout
        remaining = set(gids)
        result: dict[str, dict] = {}
        while remaining and time.time() < deadline:
            for gid in list(remaining):
                st = self.tell_status(gid)
                if st.get("status") in DONE_STATUS:
                    result[gid] = st
                    remaining.discard(gid)
            if remaining:
                time.sleep(poll)
        for gid in remaining:
            result[gid] = {"status": "timeout", "gid": gid}
        return result

    def close(self) -> None:
        self._http.close()


def chapter_dir_name(label: str, style: str) -> str:
    """章节目录名：site -> "Chapter 6.3"；cn -> "第6.3话"。"""
    if style == "cn":
        return f"第{label}话"
    if label.lower().startswith("chapter"):
        return label
    return f"Chapter {label}"


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def flat_file_prefix(label: str, style: str) -> str:
    """扁平布局的文件名前缀：Chapter 6.3 -> Chapter6.3；第6.3话 -> 第6.3话。"""
    return _sanitize(chapter_dir_name(label, style)).replace(" ", "")


def _sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")


def _ext_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.lower()
    if not ext or len(ext) > 5:
        ext = ".jpg"
    return ext


def image_filename(url: str, index: int, pad_digits: int) -> str:
    """图片下载文件名：0.jpg / 1.png（按序号命名，补零位数由配置决定，默认 1 即不补零）。"""
    return f"{index:0{pad_digits}d}{_ext_from_url(url)}"


def _legacy_name(url: str, index: int, convert: bool) -> str:
    """旧版本命名：3 位补零（000.jpg / 000.png），用于自动迁移已下载章节。"""
    name = image_filename(url, index, 3)
    if convert and Path(name).suffix.lower() != ".png":
        name = Path(name).with_suffix(".png").name
    return name


def _convert_to_png(chapter_dir: Path, names: list[str]) -> None:
    """把指定图片文件转成 PNG（同名 .png），删除原文件。"""
    from PIL import Image

    for name in names:
        f = chapter_dir / name
        if f.suffix.lower() in (".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            target = f.with_suffix(".png")
            with Image.open(f) as im:
                im.convert("RGB").save(target, "PNG")
            f.unlink()


def _plan_tasks(
    images: list[str],
    ch_dir: Path,
    pad: int,
    convert: bool,
    prefix: str | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """规划下载任务。

    - prefix 为空：nested 布局，文件名按序号（0.jpg），并自动把旧 000 命名迁移为新命名；
    - prefix 非空：flat 布局，文件名带章节前缀（Chapter1_0.jpg）。

    返回 (tasks, present)：tasks=[(图片URL, 下载用文件名)]；
    present=已存在或已迁移的最终文件名（用于登记入库）。
    """
    tasks: list[tuple[str, str]] = []
    present: list[str] = []
    for i, url in enumerate(images):
        task_name = (
            f"{prefix}_{i}{_ext_from_url(url)}"
            if prefix
            else image_filename(url, i, pad)
        )
        final_name = task_name
        if convert and Path(task_name).suffix.lower() != ".png":
            final_name = Path(task_name).with_suffix(".png").name  # 交付时转 png
        target = ch_dir / final_name
        if target.exists():
            present.append(final_name)
            continue
        if prefix is None:
            legacy = ch_dir / _legacy_name(url, i, convert)
            if legacy.exists():
                legacy.rename(target)  # 旧 000 命名 -> 新命名，避免重新下载
                present.append(final_name)
                continue
        tasks.append((url, task_name))
    return tasks, present


def download_chapter(
    site: SiteClient,
    rpc: Aria2Rpc,
    manga: MangaInfo,
    rec: dict[str, Any],
    chapter: ChapterRef,
    config: dict[str, Any],
) -> ChapterResult:
    """下载单个章节：解析图片 → 交给下载器 → 校验文件 → 登记入库。"""
    result = ChapterResult(chapter=chapter)
    try:
        resp = site.get(chapter.url, referer=manga.url)
    except SiteError as exc:
        result.message = f"章节页请求失败: {exc}"
        return result

    images = _parse_images(site, resp.text, chapter.url)
    if not images:
        result.message = "未解析到章节图片（页面结构可能变化）"
        return result

    manga_dir = Path(rec["dir"])
    layout = config.get("chapter_layout", "nested")
    prefix: str | None = None
    if layout == "flat":
        # 扁平布局：不建二级文件夹，文件名带章节前缀
        prefix = flat_file_prefix(chapter.label, config["chapter_dir_style"])
        ch_dir = manga_dir
        ch_dir.mkdir(parents=True, exist_ok=True)
    else:
        ch_dir = manga_dir / _sanitize(
            chapter_dir_name(chapter.label, config["chapter_dir_style"])
        )
        ch_dir.mkdir(parents=True, exist_ok=True)

    dl_cfg = config["downloader"]
    pad = int(config["image_pad_digits"])
    convert = bool(config["convert_to_png"])

    tasks, present = _plan_tasks(images, ch_dir, pad, convert, prefix)
    if not tasks:
        result.ok = True
        result.pages = len(images)
        result.files = sorted(set(present))
        result.message = "全部图片已存在，跳过"
        return result

    gids: dict[str, str] = {}
    for url, task_name in tasks:
        gid = rpc.add_uri(
            url,
            str(ch_dir),
            task_name,
            referer=chapter.url,
            user_agent=config["user_agent"],
            connections=int(dl_cfg.get("connections_per_server", 4)),
        )
        gids[gid] = task_name

    statuses = rpc.wait(
        list(gids.keys()),
        timeout=float(dl_cfg.get("task_timeout", 600)),
        poll=float(dl_cfg.get("poll_interval", 1.0)),
    )

    failed: list[str] = []
    for gid, task_name in gids.items():
        st = statuses.get(gid, {})
        if st.get("status") != "complete":
            failed.append(task_name)
        elif not (ch_dir / task_name).exists():
            # 任务显示完成但文件缺失
            paths = [f.get("path", "") for f in st.get("files", [])]
            if not any(Path(p).name == task_name for p in paths if p):
                failed.append(task_name)

    if failed:
        result.failed = failed
        result.message = f"有 {len(failed)} 张图片下载失败: {', '.join(failed[:5])}"
        return result

    # 本次下载成功且落盘的文件名（转换前）
    downloaded: list[str] = []
    for gid, task_name in gids.items():
        st = statuses.get(gid, {})
        if st.get("status") == "complete" and (ch_dir / task_name).exists():
            downloaded.append(task_name)

    if convert:
        _convert_to_png(ch_dir, downloaded)

    result.files = sorted(set(present + downloaded))
    if convert:
        result.files = sorted(
            Path(n).with_suffix(".png").name if Path(n).suffix.lower() != ".png" else n
            for n in result.files
        )
    result.ok = True
    result.pages = len(images)
    result.message = "完成"
    return result


def _parse_images(site: SiteClient, html: str, page_url: str) -> list[str]:
    from .parser import parse_chapter_images

    return parse_chapter_images(html, page_url)


def build_rpc(config: dict[str, Any]) -> Aria2Rpc:
    dl_cfg = config["downloader"]
    return Aria2Rpc(
        rpc_url=dl_cfg.get("rpc_url", "http://127.0.0.1:16800/jsonrpc"),
        secret=dl_cfg.get("secret", "token") or "",
    )
