"""浏览器登录：启动真实浏览器让用户登录 rawkuma，保存会话 Cookie 供爬虫复用。

爬取过程无需图形界面；仅登录这一步需要浏览器。登录成功后 Cookie 写入
cookies.json，之后 bookmark / -url / update 均以命令行方式静默运行。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from .client import save_cookies

# 等待用户完成登录的最大时长（可用环境变量 RAWKUMA_LOGIN_TIMEOUT 覆盖，便于测试）
LOGIN_WAIT_SECONDS = int(os.environ.get("RAWKUMA_LOGIN_TIMEOUT", "600"))


def run_login(config: dict[str, Any], cookies_path: Path) -> bool:
    try:
        from playwright.sync_api import TimeoutError as PWTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("缺少 playwright 依赖，请先执行: uv sync")
        return False

    base_url = config["base_url"].rstrip("/")
    auth_url = f"{base_url}/auth/"

    with sync_playwright() as p:
        browser = None
        launch_kwargs: dict[str, Any] = {
            "headless": False,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        # 优先使用系统浏览器，其次 Playwright 自带 Chromium
        for channel in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(**launch_kwargs, channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            try:
                browser = p.chromium.launch(**launch_kwargs)
            except Exception as exc:
                print(
                    "无法启动浏览器。请先安装浏览器内核，任选其一：\n"
                    "  uv run playwright install chromium\n"
                    "  uv run playwright install msedge"
                )
                print(f"原始错误: {exc}")
                return False

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=config["user_agent"],
        )
        page = context.new_page()

        def _status() -> str:
            return page.url or ""

        try:
            page.goto(auth_url, timeout=60_000)
        except PWTimeoutError:
            print("打开登录页超时，请检查网络后重试。")
            browser.close()
            return False

        print(f"请在打开的浏览器窗口中完成登录：{auth_url}")
        print("（登录完成后本程序会自动检测并保存会话，无需关闭浏览器）")

        deadline = time.time() + LOGIN_WAIT_SECONDS
        logged_in = False
        while time.time() < deadline:
            time.sleep(2)
            url = _status()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=2_000)
            except Exception:
                pass
            # 登录成功标志：不再停留在 /auth/ 且收藏夹页可访问
            if "/auth/" not in url and url:
                try:
                    resp = context.request.get(f"{base_url}/bookmark/", timeout=15_000)
                    if resp.status == 200 and "/auth/" not in str(resp.url):
                        logged_in = True
                        break
                except Exception:
                    pass
            # 若页面已跳回站内且用户手动关闭了浏览器，也视为完成
            if url and "/auth/" not in url and page.is_closed():
                logged_in = True
                break

        if not logged_in:
            print("等待登录超时，未保存 Cookie。可重新运行 uv run main.py login。")
            browser.close()
            return False

        # 收集本站相关 Cookie（不采集其它站点/敏感信息）
        cookies = [
            c
            for c in context.cookies()
            if "rawkuma.net" in (c.get("domain") or "")
        ]
        save_cookies(cookies_path, cookies)
        print(f"登录成功，Cookie 已保存到 {cookies_path}（{len(cookies)} 条）")
        browser.close()
        return True
