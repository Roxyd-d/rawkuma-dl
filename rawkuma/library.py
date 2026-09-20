"""本地漫画库：JSON 持久化、更新比对。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .parser import MangaInfo


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Library:
    """library.json 的读写封装。

    结构:
    {
      "version": 1,
      "download_root": "downloads",
      "manga": [
        {
          "title": ..., "slug": ..., "url": ..., "dir": ...,
          "chapters": [{"id","label","title","url","dir","pages","files","downloaded_at"}],
          "updated_at": ...
        }
      ]
    }
    """

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {"version": 1, "download_root": "", "manga": []}

    # ---------- 查询 ----------
    def manga_list(self) -> list[dict[str, Any]]:
        return self.data.get("manga", [])

    def find(self, manga: MangaInfo) -> dict[str, Any] | None:
        for rec in self.manga_list():
            if rec.get("slug") == manga.slug or rec.get("url") == manga.url:
                return rec
        return None

    def chapter_ids(self, manga: MangaInfo) -> set[str]:
        rec = self.find(manga)
        if not rec:
            return set()
        return {c["id"] for c in rec.get("chapters", [])}

    # ---------- 写入 ----------
    def upsert_manga(self, manga: MangaInfo, manga_dir: Path) -> dict[str, Any]:
        rec = self.find(manga)
        if rec is None:
            rec = {
                "title": manga.title,
                "slug": manga.slug,
                "url": manga.url,
                "dir": str(manga_dir),
                "chapters": [],
                "updated_at": _now(),
            }
            self.data["manga"].append(rec)
        else:
            rec["title"] = manga.title
            rec["url"] = manga.url
            rec["dir"] = str(manga_dir)
        return rec

    def record_chapter(
        self,
        rec: dict[str, Any],
        chapter_id: str,
        label: str,
        title: str,
        chapter_url: str,
        chapter_dir: Path,
        pages: int,
        files: list[str],
    ) -> None:
        entry = {
            "id": chapter_id,
            "label": label,
            "title": title,
            "url": chapter_url,
            "dir": str(chapter_dir),
            "pages": pages,
            "files": files,
            "downloaded_at": _now(),
        }
        for i, c in enumerate(rec["chapters"]):
            if c["id"] == chapter_id:
                rec["chapters"][i] = entry
                break
        else:
            rec["chapters"].append(entry)
        rec["updated_at"] = _now()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.path)
