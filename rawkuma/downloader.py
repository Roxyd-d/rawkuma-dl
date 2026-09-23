"""图片下载：爬虫进程内直接并发下载（不再依赖第三方下载器）。

下载图片时按章节解析图片 URL，在进程内用 httpx 流式并发拉取，
失败自动重试；完成后按配置转格式并登记到本地库。
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .client import SiteClient, SiteError
from .parser import ChapterRef, MangaInfo

DONE_STATUS = {"complete", "error", "removed"}


@dataclass
class ChapterResult:
    chapter: ChapterRef
    ok: bool = False
    pages: int = 0
    files: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    message: str = ""


def _download_images_http(
    site: SiteClient,
    tasks: list[tuple[str, str]],
    ch_dir: Path,
    *,
    referer: str,
    max_concurrent: int = 4,
    retries: int = 3,
    timeout: float = 120.0,
) -> list[str]:
    """进程内并发下载图片，返回失败的文件名列表。

    tasks: [(图片 URL, 下载用文件名)]；下载进度以 . 表示成功、F 表示失败。
    使用爬虫自带的 httpx 客户端（带登录 Cookie），不经过第三方下载器。
    """
    ch_dir.mkdir(parents=True, exist_ok=True)
    total = len(tasks)
    done = 0
    lock = threading.Lock()
    failed: list[str] = []

    def fetch(item: tuple[str, str]) -> bool:
        url, name = item
        target = ch_dir / name
        hdrs = {"Referer": referer} if referer else None
        for attempt in range(retries):
            try:
                with site.client.stream(
                    "GET", url, headers=hdrs, timeout=timeout
                ) as resp:
                    if resp.status_code != 200:
                        raise SiteError(f"HTTP {resp.status_code}")
                    with open(target, "wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
                if target.stat().st_size <= 0:
                    raise SiteError("空文件")
                return True
            except Exception:  # noqa: BLE001 - 单张失败重试
                target.unlink(missing_ok=True)
                if attempt < retries - 1:
                    time.sleep(0.5)
        return False

    with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
        futures = {ex.submit(fetch, item): item[1] for item in tasks}
        for fut in as_completed(futures):
            name = futures[fut]
            ok = fut.result()
            with lock:
                done += 1
                if not ok:
                    failed.append(name)
                print("." if ok else "F", end="", flush=True)
                if done % 40 == 0 or done == total:
                    print(f" {done}/{total}", flush=True)
    print()
    return failed


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


def image_format_of(config: dict[str, Any]) -> str:
    """解析图片格式转换配置：original=不转换；png=转 PNG；jpg=转 JPG。

    兼容旧版 convert_to_png 布尔配置（True->png，False->original）。
    """
    if "convert_to_png" in config and "image_format" not in config:
        return "png" if config["convert_to_png"] else "original"
    fmt = str(config.get("image_format", "png")).lower()
    return fmt if fmt in ("png", "jpg") else "original"


def _legacy_name(url: str, index: int, fmt: str) -> str:
    """旧版本命名：3 位补零（000.jpg / 000.png），用于自动迁移已下载章节。"""
    name = image_filename(url, index, 3)
    if fmt != "original":
        name = Path(name).with_suffix(f".{fmt}").name
    return name


def _convert_format(chapter_dir: Path, names: list[str], fmt: str) -> None:
    """把指定图片文件统一转成 fmt（png/jpg），删除原文件。"""
    from PIL import Image

    for name in names:
        f = chapter_dir / name
        if f.suffix.lower() in IMAGE_EXTS and f.suffix.lower() != f".{fmt}":
            target = f.with_suffix(f".{fmt}")
            with Image.open(f) as im:
                im.convert("RGB").save(target, fmt.upper())
            f.unlink()


def _plan_tasks(
    images: list[str],
    ch_dir: Path,
    pad: int,
    fmt: str,
    prefix: str | None = None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """规划下载任务。

    - prefix 为空：nested 布局，文件名按序号（0.jpg），并自动把旧 000 命名迁移为新命名；
    - prefix 非空：flat 布局，文件名带章节前缀（Chapter1_0.jpg）；
    - fmt：目标格式（original 不转换，png/jpg 统一转换）。

    返回 (tasks, present)：tasks=[(图片URL, 下载用文件名)]；
    present=已存在或已迁移的最终文件名（用于登记入库）。
    """
    tasks: list[tuple[str, str]] = []
    present: list[str] = []
    target_ext = f".{fmt}" if fmt in ("png", "jpg") else None
    for i, url in enumerate(images):
        task_name = (
            f"{prefix}_{i}{_ext_from_url(url)}"
            if prefix
            else image_filename(url, i, pad)
        )
        final_name = task_name
        if target_ext and Path(task_name).suffix.lower() != target_ext:
            final_name = Path(task_name).with_suffix(target_ext).name  # 交付时转格式
        target = ch_dir / final_name
        if target.exists():
            present.append(final_name)
            continue
        if prefix is None:
            legacy = ch_dir / _legacy_name(url, i, fmt)
            if legacy.exists():
                legacy.rename(target)  # 旧 000 命名 -> 新命名，避免重新下载
                present.append(final_name)
                continue
        tasks.append((url, task_name))
    return tasks, present


def download_chapter(
    site: SiteClient,
    manga: MangaInfo,
    rec: dict[str, Any],
    chapter: ChapterRef,
    config: dict[str, Any],
    dl_dir: Path | None = None,
) -> ChapterResult:
    """下载单个章节：解析图片 → 进程内并发下载 → 校验 → 转格式 → 登记入库。

    dl_dir 指定实际下载目录（如 update 子目录）；缺省用漫画根目录。
    """
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

    manga_dir = Path(dl_dir) if dl_dir is not None else Path(rec["dir"])
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
    fmt = image_format_of(config)

    tasks, present = _plan_tasks(images, ch_dir, pad, fmt, prefix)
    if not tasks:
        result.ok = True
        result.pages = len(images)
        result.files = sorted(set(present))
        result.message = "全部图片已存在，跳过"
        return result

    failed = _download_images_http(
        site,
        tasks,
        ch_dir,
        referer=chapter.url,
        max_concurrent=int(dl_cfg.get("max_concurrent", 4)),
        retries=int(dl_cfg.get("retries", 3)),
        timeout=float(dl_cfg.get("request_timeout", 120)),
    )
    if failed:
        result.failed = failed
        result.message = f"有 {len(failed)} 张图片下载失败: {', '.join(failed[:5])}"
        return result

    # 全部成功：本次下载的文件名（转换前）
    downloaded = [name for _, name in tasks]
    if fmt != "original":
        _convert_format(ch_dir, downloaded, fmt)

    result.files = sorted(set(present + downloaded))
    if fmt != "original":
        target_ext = f".{fmt}"
        result.files = sorted(
            Path(n).with_suffix(target_ext).name
            if Path(n).suffix.lower() != target_ext
            else n
            for n in result.files
        )
    result.ok = True
    result.pages = len(images)
    result.message = "完成"
    return result


def _parse_images(site: SiteClient, html: str, page_url: str) -> list[str]:
    from .parser import parse_chapter_images

    return parse_chapter_images(html, page_url)
