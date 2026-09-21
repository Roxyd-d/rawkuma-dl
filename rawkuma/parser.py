"""HTML 解析：漫画页、章节页、收藏夹页。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import unescape as html_unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

MANGA_RE = re.compile(r"https://rawkuma\.net/manga/(?P<slug>[^/]+)/?$")
CHAPTER_RE = re.compile(
    r"https://rawkuma\.net/manga/(?P<slug>[^/]+)/"
    r"chapter-(?P<label>.+?)\.(?P<id>\d+)/"
)
NUM_RE = re.compile(r"(\d+)(?:\.(\d+))?")
IMG_RE = re.compile(r"https://rcdn\.kyut\.dev/\S+\.(?:jpg|jpeg|png|webp|gif|avif)")
# 收藏夹列表由 htmx 异步加载，接口地址内嵌在收藏夹页 HTML 中
BOOKMARK_AJAX_RE = re.compile(
    r"https://rawkuma\.net/wp-admin/admin-ajax\.php\?[^\"']*action=get_bookmarks"
)


def parse_bookmark_api_url(html: str) -> str | None:
    """从收藏夹页提取 get_bookmarks 接口地址（含 nonce/user_id）。

    该列表不在初始 HTML 里，需直接请求接口获取：
    admin-ajax.php?nonce=...&user_id=...&action=get_bookmarks&type=all[&page=N]
    """
    m = BOOKMARK_AJAX_RE.search(html)
    if not m:
        return None
    url = html_unescape(m.group(0))
    # 去掉可能残留的 type=/page= 参数，由调用方统一追加
    return re.sub(r"[&?](?:type|page)=[^&]*", "", url)


@dataclass
class ChapterRef:
    id: str          # 站点章节数字 ID（用于更新比对）
    label: str       # 章节标签，如 "6.3"
    url: str         # 章节页地址
    title: str = ""  # 展示名，如 "Chapter 6.3"

    @property
    def display(self) -> str:
        return self.title or f"Chapter {self.label}"

    @property
    def order_key(self) -> tuple:
        """数值排序键： (6, 3) 表示 6.3 话。"""
        m = NUM_RE.match(self.label)
        if m:
            return (int(m.group(1)), int(m.group(2) or 0))
        return (10**9, 0)


@dataclass
class MangaInfo:
    title: str
    slug: str
    url: str
    chapters: list[ChapterRef] = field(default_factory=list)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def parse_manga_page(html: str, page_url: str) -> MangaInfo:
    """解析漫画详情页，返回标题、slug、按章节顺序排序的章节列表。"""
    soup = BeautifulSoup(html, "html.parser")

    # 标题：取第一个非 "Last Updates" 的 h1
    title = ""
    for h1 in soup.find_all("h1"):
        t = _clean(h1.get_text())
        if t and t.lower() != "last updates":
            title = t
            break
    if not title:
        m = re.search(r"<title>(.*?)</title>", html, re.S)
        if m:
            title = _clean(re.sub(r"\s*[–—-]\s*Rawkuma.*$", "", m.group(1)))

    slug = ""
    for a in soup.find_all("a", href=True):
        m = MANGA_RE.match(a["href"])
        if m:
            slug = m.group("slug")
            break
    if not slug:
        m = re.search(r"/manga/([^/]+)/?", page_url)
        slug = m.group(1) if m else ""

    chapters: dict[str, ChapterRef] = {}
    for a in soup.find_all("a", href=True):
        m = CHAPTER_RE.match(a["href"].strip())
        if not m:
            continue
        ref = ChapterRef(
            id=m.group("id"),
            label=m.group("label"),
            url=a["href"].strip(),
            title=_clean(a.get_text()),
        )
        if ref.title.lower().startswith("chapter"):
            # 链接文本含日期/浏览量，展示名统一由 URL 标签生成
            ref.title = ""
        chapters[ref.id] = ref

    ordered = sorted(chapters.values(), key=lambda c: c.order_key)
    return MangaInfo(
        title=title,
        slug=slug,
        url=page_url,
        chapters=ordered,
    )


def parse_chapter_images(html: str, page_url: str) -> list[str]:
    """解析章节页，返回阅读区内图片 URL 列表（按出现顺序）。"""
    soup = BeautifulSoup(html, "html.parser")
    section = soup.find("section", attrs={"data-image-data": True})
    urls: list[str] = []
    if section is not None:
        for img in section.find_all("img", src=True):
            src = img["src"].strip()
            if src.startswith("http"):
                urls.append(urljoin(page_url, src))
        if urls:
            return urls
    # 兜底：页面里所有 CDN 图片
    for img in soup.find_all("img", src=True):
        src = img["src"].strip()
        if IMG_RE.match(src):
            urls.append(urljoin(page_url, src))
    return urls


def parse_bookmarks(html: str, base_url: str) -> list[tuple[str, str]]:
    """解析收藏夹页，返回 [(漫画名, 详情页 URL)]，保持页面顺序、按 URL 去重。"""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"].strip())
        m = MANGA_RE.match(href)
        if not m:
            continue
        if href in seen:
            continue
        title = _manga_card_title(a, href)
        if not title:
            continue
        seen[href] = title
    return [(title, href) for href, title in seen.items()]


# 卡片里的角标/类型小图标 alt，不能当作漫画名
BADGE_ALTS = {
    "manga", "manhwa", "manhua", "comic", "logo", "icon", "svg",
    "bookmark", "reading", "history", "image", "img",
}


def _manga_card_title(a, href: str) -> str:
    """从收藏夹卡片里提取漫画名（优先标题元素，其次封面 alt，最后链接文本）。"""
    # 1) 卡片内标题类元素（line-clamp 标题）
    for sel in ("[class*='line-clamp']", "h2", "h3", "h4"):
        node = a.select_one(sel)
        if node:
            t = _clean(node.get_text())
            if t:
                return t
    # 2) 封面图 alt：排除角标小图标，取最长的候选（封面 alt 即完整漫画名）
    best = ""
    for img in a.find_all("img", alt=True):
        t = _clean(img["alt"])
        if not t or t.lower() in BADGE_ALTS or len(t) < 5:
            continue
        if len(t) > len(best):
            best = t
    if best:
        return best
    # 3) 链接整体文本（去掉 "Start Reading" 等干扰）
    t = _clean(a.get_text(" "))
    for junk in ("Start Reading", "Read Now"):
        t = t.replace(junk, "")
    t = _clean(t)
    return t
