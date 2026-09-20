"""站点 HTTP 客户端：携带登录 Cookie、模拟浏览器请求。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from .config import resolve_path

# 未登录时访问需登录页面会被重定向到的路径片段
AUTH_PATH = "/auth/"


class SiteError(Exception):
    """站点请求或解析错误。"""


class LoginRequired(SiteError):
    """访问需要登录的页面但当前未登录。"""


def load_cookies(path: Path) -> list[dict[str, Any]]:
    """读取 httpx 兼容的 Cookie 列表（由 main.py login 生成）。"""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("cookies", [])
    return [c for c in data if c.get("name") and c.get("value") is not None]


def save_cookies(path: Path, cookies: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "cookies": cookies}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class SiteClient:
    """对 rawkuma.net 的请求封装。"""

    def __init__(self, config: dict[str, Any], cookies_path: Path | None = None):
        self.config = config
        self.base_url = config["base_url"].rstrip("/")
        self.cookies_path = cookies_path or resolve_path(
            Path(__file__).resolve().parent.parent, config["cookies_file"]
        )
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {
                "User-Agent": self.config["user_agent"],
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9,ja;q=0.8,zh-CN;q=0.7",
            }
            client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                follow_redirects=True,
                timeout=30.0,
            )
            for c in load_cookies(self.cookies_path):
                client.cookies.set(
                    c["name"],
                    c["value"],
                    domain=c.get("domain") or self.base_url,
                    path=c.get("path") or "/",
                )
            self._client = client
        return self._client

    def get(
        self,
        url: str,
        *,
        expect_login: bool = False,
        referer: str | None = None,
    ) -> httpx.Response:
        """GET 请求；expect_login=True 时检测是否被重定向到登录页。"""
        if referer:
            resp = self.client.get(url, headers={"Referer": referer})
        else:
            resp = self.client.get(url)
        if resp.status_code >= 400:
            raise SiteError(f"请求失败 {resp.status_code}: {url}")
        if expect_login and AUTH_PATH in resp.url.path:
            raise LoginRequired(
                "该页面需要登录。请先运行: uv run main.py login"
            )
        return resp

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SiteClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
