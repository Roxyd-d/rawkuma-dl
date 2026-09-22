"""命令行入口逻辑。"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

from .client import LoginRequired, SiteClient
from .config import PROJECT_ROOT, load_config, resolve_path
from .downloader import build_rpc, download_chapter, _sanitize
from .library import Library
from .login import run_login
from .parser import (
    ChapterRef,
    MangaInfo,
    parse_bookmark_api_url,
    parse_bookmarks,
    parse_manga_page,
)

BOOKMARK_URL_PATH = "/bookmark/"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="main.py",
        description="Rawkuma 漫画下载器（命令行；登录使用浏览器）",
    )
    p.add_argument(
        "-url",
        "--url",
        metavar="MANGA_URL",
        help="漫画详情页链接，下载该漫画（如 -url https://rawkuma.net/manga/<slug>/）",
    )
    p.add_argument(
        "command",
        nargs="?",
        choices=["login", "bookmark", "update", "list", "convert"],
        help="子命令: login=浏览器登录; bookmark=按收藏夹索引下载; "
        "update=检查更新; list=查看本地库; "
        "convert=交互选择漫画并转换为扁平结构（--index N 可直接指定）",
    )
    p.add_argument(
        "--chapters",
        help="只下载指定章节，如 1,3-5（与 -url / bookmark 配合）",
    )
    p.add_argument(
        "--latest",
        action="store_true",
        help="只下载最新章节（与 -url / bookmark 配合）",
    )
    p.add_argument(
        "--download",
        action="store_true",
        help="update 时自动下载新章节（跳过询问）",
    )
    p.add_argument(
        "--merge",
        action="store_true",
        help="update 下载完成后询问是否把 update 文件夹合并到正式目录",
    )
    p.add_argument(
        "--index",
        type=int,
        help="bookmark / convert 时直接指定序号（跳过交互选择）",
    )
    p.add_argument(
        "--mode",
        choices=["flat", "nested"],
        default="flat",
        help="convert 的转换模式：flat=旧结构(二级文件夹)转扁平（默认）；"
        "nested=扁平转回二级文件夹",
    )
    p.add_argument(
        "--limit",
        type=int,
        help="本次最多下载的章节数（按章节目录顺序），适合分批下载",
    )
    p.add_argument(
        "--root",
        help="覆盖下载根目录",
    )
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    config = load_config()
    root = PROJECT_ROOT
    if args.root:
        config["download_root"] = args.root
    cookies_path = resolve_path(root, config["cookies_file"])
    lib_path = resolve_path(root, config["library_file"])
    lib = Library(lib_path)

    if args.command == "login":
        ok = run_login(config, cookies_path)
        sys.exit(0 if ok else 1)

    if args.command == "list":
        list_library(lib)
        return

    if args.command == "convert":
        convert_flow(lib, config, index=args.index, mode=args.mode)
        return

    try:
        with SiteClient(config, cookies_path) as site:
            if args.url:
                download_manga(
                    site,
                    lib,
                    config,
                    manga_url=args.url,
                    chapters_spec=args.chapters,
                    latest=args.latest,
                    limit=args.limit,
                )
            elif args.command == "bookmark":
                bookmark_flow(
                    site,
                    lib,
                    config,
                    index=args.index,
                    chapters_spec=args.chapters,
                    latest=args.latest,
                    limit=args.limit,
                )
            elif args.command == "update":
                update_flow(
                    site,
                    lib,
                    config,
                    auto_download=args.download,
                    merge=args.merge,
                )
            else:
                build_parser().print_help()
    except LoginRequired as exc:
        print(f"[错误] {exc}")
        sys.exit(2)
    except Exception as exc:  # noqa: BLE001 - 顶层兜底
        print(f"[错误] {exc}")
        sys.exit(1)


# --------------------------------------------------------------------------- #
# 下载流程
# --------------------------------------------------------------------------- #
def download_manga(
    site: SiteClient,
    lib: Library,
    config: dict[str, Any],
    manga_url: str,
    *,
    chapters_spec: str | None = None,
    latest: bool = False,
    targets: list[ChapterRef] | None = None,
    limit: int | None = None,
    subdir: str | None = None,
) -> None:
    resp = site.get(manga_url)
    manga = parse_manga_page(resp.text, str(resp.url))

    root = resolve_path(PROJECT_ROOT, config["download_root"])
    manga_dir = root / _sanitize(manga.title)
    rec = lib.upsert_manga(manga, manga_dir)
    lib.data["download_root"] = str(root)
    lib.save()

    if targets is None:
        targets = _select_targets(
            manga, lib, chapters_spec=chapters_spec, latest=latest
        )
    if limit and limit > 0:
        targets = targets[:limit]

    if not targets:
        print(f"{manga.title}: 无需下载（已是最新）")
        return

    # update 流程可指定子目录（如 update/），新章节先下载到该处，稍后合并
    dl_dir = manga_dir / subdir if subdir else manga_dir
    print(f"{manga.title}: 开始下载 {len(targets)} 个章节 -> {dl_dir}")
    rpc = build_rpc(config)
    try:
        for i, chapter in enumerate(targets, 1):
            print(f"  [{i}/{len(targets)}] {chapter.display} ...", end="", flush=True)
            result = download_chapter(
                site, rpc, manga, rec, chapter, config, dl_dir=dl_dir
            )
            if result.ok:
                lib.record_chapter(
                    rec,
                    chapter_id=chapter.id,
                    label=chapter.label,
                    title=chapter.display,
                    chapter_url=chapter.url,
                    chapter_dir=_chapter_dir(dl_dir, chapter, config),
                    pages=result.pages,
                    files=result.files,
                )
                lib.save()
                print(f" 完成（{result.pages} 页）")
            else:
                print(f"  失败: {result.message}")
    finally:
        rpc.close()


def _chapter_dir(manga_dir: Path, chapter: ChapterRef, config: dict[str, Any]) -> Path:
    if config.get("chapter_layout") == "flat":
        return manga_dir  # 扁平布局：图片直接放在漫画根目录
    from .downloader import chapter_dir_name

    return manga_dir / _sanitize(
        chapter_dir_name(chapter.label, config["chapter_dir_style"])
    )


def convert_flow(
    lib: Library,
    config: dict[str, Any],
    index: int | None = None,
    mode: str = "flat",
) -> None:
    """转换本地漫画的目录结构。

    mode="flat"：旧版二级文件夹（<漫画>/Chapter 1/0.png）→ 扁平（<漫画>/Chapter1_0.png）；
    mode="nested"：扁平 → 二级文件夹。

    默认像 bookmark 一样：列出本地库漫画并交互选择序号；--index N 可直接指定。
    转换依据 library.json 中的章节记录定位目录；重命名后更新记录。
    成功转换后自动把 config.json 的 chapter_layout 设为与 mode 一致。
    """
    import json

    from .config import DEFAULT_CONFIG_PATH
    from .downloader import IMAGE_EXTS, flat_file_prefix

    if mode not in ("flat", "nested"):
        print(f"未知转换模式: {mode}（可选 flat / nested）")
        return

    records = lib.manga_list()
    if not records:
        print("本地库为空，无需转换。")
        return
    if index is None:
        for i, rec in enumerate(records):
            print(f"{i} {rec.get('title', '?')}")
        try:
            raw = input("输入序号转换: ").strip()
            index = int(raw)
        except (ValueError, EOFError):
            print("输入无效。")
            return
    if index < 0 or index >= len(records):
        print(f"序号越界（0-{len(records) - 1}）。")
        return
    records = [records[index]]

    def _num_key(name: str) -> tuple[int, str]:
        m = re.match(r"(\d+)", name)
        return (int(m.group(1)) if m else 0, name)

    def _flatten(rec: dict[str, Any], manga_dir: Path) -> int:
        """二级文件夹 -> 扁平：Chapter 1/0.png -> Chapter1_0.png"""
        total = 0
        for ch in rec.get("chapters", []):
            old_dir = Path(ch.get("dir", ""))
            if old_dir == manga_dir or not old_dir.is_dir():
                continue  # 已是扁平结构，或目录缺失
            prefix = flat_file_prefix(
                ch.get("label", ""), config.get("chapter_dir_style", "site")
            )
            files = sorted(
                (
                    f
                    for f in old_dir.iterdir()
                    if f.is_file() and f.suffix.lower() in IMAGE_EXTS
                ),
                key=lambda f: _num_key(f.stem),
            )
            renamed: list[str] = []
            for i, f in enumerate(files):
                target = manga_dir / f"{prefix}_{i}{f.suffix.lower()}"
                if target.exists():
                    print(f"  {target.name} 已存在，跳过（不覆盖）")
                    continue
                f.rename(target)
                renamed.append(target.name)
            try:
                old_dir.rmdir()  # 删除空目录；含非图片文件时保留
            except OSError:
                pass
            if renamed:
                ch["dir"] = str(manga_dir)
                ch["files"] = renamed
                total += len(renamed)
                print(
                    f"  {ch.get('label', '?')}: {len(renamed)} 个文件 -> {prefix}_0 … {prefix}_{len(renamed) - 1}"
                )
        return total

    def _nest(rec: dict[str, Any], manga_dir: Path) -> int:
        """扁平 -> 二级文件夹：Chapter1_0.png -> Chapter 1/0.png"""
        from .downloader import _sanitize, chapter_dir_name

        total = 0
        pad = int(config.get("image_pad_digits", 1))
        for ch in rec.get("chapters", []):
            label = ch.get("label", "")
            prefix = flat_file_prefix(label, config.get("chapter_dir_style", "site"))
            ch_dir = manga_dir / _sanitize(
                chapter_dir_name(label, config.get("chapter_dir_style", "site"))
            )
            files = sorted(
                (
                    f
                    for f in manga_dir.iterdir()
                    if f.is_file()
                    and f.name.startswith(prefix + "_")
                    and f.suffix.lower() in IMAGE_EXTS
                ),
                key=lambda f: _num_key(f.name[len(prefix) + 1 :]),  # 按前缀后的序号排序
            )
            if not files:
                continue  # 该章节已是嵌套结构或没有匹配文件
            ch_dir.mkdir(parents=True, exist_ok=True)
            renamed: list[str] = []
            for i, f in enumerate(files):
                target = ch_dir / f"{i:0{pad}d}{f.suffix.lower()}"
                if target.exists():
                    print(f"  {target.name} 已存在，跳过（不覆盖）")
                    continue
                f.rename(target)
                renamed.append(target.name)
            if renamed:
                ch["dir"] = str(ch_dir)
                ch["files"] = renamed
                total += len(renamed)
                print(
                    f"  {label}: {len(renamed)} 个文件 -> {ch_dir.name}/{renamed[0]} …"
                )
        return total

    style = config.get("chapter_dir_style", "site")
    total = 0
    for rec in records:
        manga_dir = Path(rec["dir"])
        if not manga_dir.is_dir():
            print(f"跳过 {rec.get('title', '?')}：目录不存在 {manga_dir}")
            continue
        desc = "旧结构→扁平" if mode == "flat" else "扁平→旧结构"
        print(f"转换: {rec.get('title', '?')}（{desc}）")
        if mode == "flat":
            total += _flatten(rec, manga_dir)
        else:
            total += _nest(rec, manga_dir)
    lib.save()

    if total:
        p = Path(DEFAULT_CONFIG_PATH)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if data.get("chapter_layout") != mode:
                data["chapter_layout"] = mode
                p.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"已将 config.json 的 chapter_layout 设为 {mode}（后续新下载同样使用该结构）"
                )
        print(f"完成，共转换 {total} 个文件。")
    else:
        print("没有可转换的章节（可能已是目标结构）。")


def _select_targets(
    manga: MangaInfo,
    lib: Library,
    *,
    chapters_spec: str | None,
    latest: bool,
) -> list[ChapterRef]:
    local = lib.chapter_ids(manga)
    if latest:
        latest_ch = sorted(manga.chapters, key=lambda c: c.order_key)[-1:]
        return latest_ch
    if chapters_spec:
        keys = _parse_chapter_spec(chapters_spec)
        return [c for c in manga.chapters if c.order_key[0] in keys]
    return [c for c in manga.chapters if c.id not in local]


def _parse_chapter_spec(spec: str) -> set[int]:
    keys: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            a, b = token.split("-", 1)
            keys.update(range(int(a), int(b) + 1))
        else:
            keys.add(int(token))
    return keys


# --------------------------------------------------------------------------- #
# bookmark 流程
# --------------------------------------------------------------------------- #
def bookmark_flow(
    site: SiteClient,
    lib: Library,
    config: dict[str, Any],
    *,
    index: int | None,
    chapters_spec: str | None,
    latest: bool,
    limit: int | None = None,
) -> None:
    base = config["base_url"].rstrip("/")
    resp = site.get(base + BOOKMARK_URL_PATH, expect_login=True)
    bookmarks = _fetch_bookmarks(site, resp.text, base)
    if not bookmarks:
        print(
            "收藏夹为空；若已收藏内容，Cookie 可能已过期，请重新运行 uv run main.py login。"
        )
        return

    # 收藏夹片段里的标题可能被截断/取到角标，从详情页补全权威完整标题
    if len(bookmarks) > 1:
        print("获取完整标题 ...", flush=True)
    bookmarks = _enrich_titles(site, bookmarks)

    print(f"共 {len(bookmarks)} 部收藏：")
    for i, (title, url) in enumerate(bookmarks):
        print(f"{i} {title}")

    if index is None:
        try:
            raw = input("输入序号下载: ").strip()
            index = int(raw)
        except (ValueError, EOFError):
            print("输入无效。")
            return
    if index < 0 or index >= len(bookmarks):
        print(f"序号越界（0-{len(bookmarks) - 1}）。")
        return

    title, url = bookmarks[index]
    print(f"选择: {title}")
    download_manga(
        site,
        lib,
        config,
        url,
        chapters_spec=chapters_spec,
        latest=latest,
        limit=limit,
    )


def _fetch_bookmarks(
    site: SiteClient, page_html: str, base: str
) -> list[tuple[str, str]]:
    """收藏夹列表由 htmx 异步加载：先提取接口地址，再分页拉取并解析。

    接口: admin-ajax.php?nonce=...&user_id=...&action=get_bookmarks&type=all[&page=N]
    """
    api_url = parse_bookmark_api_url(page_html)
    if not api_url:
        # 老结构兜底：直接解析页面内链接
        return parse_bookmarks(page_html, base)

    bookmarks: list[tuple[str, str]] = []
    seen: set[str] = set()
    sep = "&" if "?" in api_url else "?"
    for page in range(1, 101):  # 安全上限，正常会在空页提前退出
        url = f"{api_url}{sep}type=all"
        if page > 1:
            url += f"&page={page}"
        frag = site.get(url)
        items = parse_bookmarks(frag.text, base)
        if not items:
            break
        fresh = [(t, u) for t, u in items if u not in seen]
        bookmarks.extend(fresh)
        seen.update(u for _, u in fresh)
        if len(fresh) < len(items):  # 本页全是重复 -> 已到末尾
            break
    return bookmarks


def _enrich_titles(
    site: SiteClient,
    bookmarks: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """从每部漫画的详情页抓取权威完整标题，失败时保留原标题。"""
    from concurrent.futures import ThreadPoolExecutor

    def fetch(item: tuple[str, str]) -> tuple[str, str]:
        _, url = item
        try:
            r = site.get(url)
            manga = parse_manga_page(r.text, str(r.url))
            if manga.title:
                return (manga.title, url)
        except Exception:  # noqa: BLE001 - 单条失败不影响整体
            pass
        return item

    with ThreadPoolExecutor(max_workers=5) as ex:
        return list(ex.map(fetch, bookmarks))


# --------------------------------------------------------------------------- #
# update 流程
# --------------------------------------------------------------------------- #
def update_flow(
    site: SiteClient,
    lib: Library,
    config: dict[str, Any],
    *,
    auto_download: bool,
    merge: bool = False,
) -> None:
    records = lib.manga_list()
    if not records:
        print("本地库为空，先通过 -url 或 bookmark 下载漫画。")
        return

    pending: list[tuple[dict[str, Any], list[ChapterRef]]] = []
    for i, rec in enumerate(records):
        print(f"{i} {rec.get('title', '?')}")
        try:
            resp = site.get(rec["url"])
            manga = parse_manga_page(resp.text, str(resp.url))
            local = {c["id"] for c in rec.get("chapters", [])}
            new_chapters = [c for c in manga.chapters if c.id not in local]
        except Exception as exc:  # noqa: BLE001
            print(f"检查失败: {exc}")
            continue
        if not new_chapters:
            print("无更新")
        else:
            print("有更新")
            print(" ".join(c.display for c in new_chapters))
            pending.append((rec, new_chapters))

    if not pending:
        return

    # 有更新时询问是否下载；--download 已指定则直接自动下载
    if not auto_download:
        print()
        print(f"{len(pending)} 部漫画存在更新：")
        for rec, chapters in pending:
            print(f"  {rec['title']}（{len(chapters)} 个新章节）")
        try:
            raw = input("是否下载新章节？(y/N): ").strip().lower()
        except EOFError:
            raw = "n"
        if raw not in ("y", "yes"):
            print("跳过。")
            return

    # 新章节默认下载到漫画目录下的 update/ 子目录，之后可合并
    print()
    for rec, chapters in pending:
        print(f"下载 {rec['title']} 的新章节 -> update/ ...")
        download_manga(
            site,
            lib,
            config,
            rec["url"],
            targets=chapters,
            subdir="update",
        )

    if not merge:
        return

    # --merge：下载完成后询问是否合并到正式目录
    print()
    try:
        raw = input("是否合并 update 内容到正式目录？(y/N): ").strip().lower()
    except EOFError:
        raw = "n"
    if raw not in ("y", "yes"):
        print("跳过合并（新章节保留在 update 文件夹）。")
        return
    for rec, chapters in pending:
        n = merge_updates(lib, rec)
        if n:
            print(f"合并 {rec['title']}: {n} 个文件")
        else:
            print(f"{rec['title']}: update 文件夹为空或不存在")
    lib.save()


def merge_updates(lib: Library, rec: dict[str, Any]) -> int:
    """把 <漫画>/update/ 下的内容合并到正式目录，并更新 library 记录。

    - 子目录结构（nested）：update/Chapter 28.2/0.png -> Chapter 28.2/0.png
    - 扁平结构（flat）：update/Chapter28.2_0.png -> 漫画根目录
    返回移动的文件数。
    """
    manga_dir = Path(rec["dir"])
    upd = manga_dir / "update"
    if not upd.is_dir():
        return 0
    moved = 0

    # nested 风格：update 下的章节子目录
    for sub in sorted(p for p in upd.iterdir() if p.is_dir()):
        target_dir = manga_dir / sub.name
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in sorted(sub.iterdir()):
            if not f.is_file():
                continue
            t = target_dir / f.name
            if t.exists():
                print(f"  {t.name} 已存在，跳过")
                continue
            f.rename(t)
            moved += 1
        try:
            sub.rmdir()
        except OSError:
            pass

    # flat 风格：update 下直接是图片文件
    for f in sorted(upd.iterdir()):
        if not f.is_file():
            continue
        t = manga_dir / f.name
        if t.exists():
            print(f"  {t.name} 已存在，跳过")
            continue
        f.rename(t)
        moved += 1

    try:
        upd.rmdir()
    except OSError:
        pass

    # 更新 library：指向 update 的章节记录改回正式目录（文件名不变）
    for ch in rec.get("chapters", []):
        d = Path(ch.get("dir", ""))
        if d == upd:
            ch["dir"] = str(manga_dir)  # flat：记录的就是 update 根
        elif d.parent == upd:
            ch["dir"] = str(manga_dir / d.name)  # nested：update/<章节目录>
    return moved


# --------------------------------------------------------------------------- #
# list 流程
# --------------------------------------------------------------------------- #
def list_library(lib: Library) -> None:
    records = lib.manga_list()
    if not records:
        print("本地库为空。")
        return
    for i, rec in enumerate(records):
        chapters = rec.get("chapters", [])
        print(f"{i} {rec.get('title', '?')}  [{len(chapters)} 话]")
        print(f"   目录: {rec.get('dir', '?')}")
