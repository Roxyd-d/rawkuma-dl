"""配置加载：config.json 不存在时自动生成默认配置。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # 目标站点
    "base_url": "https://rawkuma.net",
    # 请求使用的浏览器 UA（尽量贴近真实浏览器，降低被拦概率）
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    # 登录后保存的 Cookie 文件（main.py login 生成）
    "cookies_file": "cookies.json",
    # 本地漫画库元数据文件（下载记录 + 更新检查依据）
    "library_file": "library.json",
    # 漫画下载根目录
    "download_root": "downloads",
    # 第三方下载器（Rayburst，底层 Aria2）RPC 配置
    "downloader": {
        # 后端协议：aria2 = aria2 兼容 JSON-RPC（Rayburst/Motrix 桌面端均支持）
        "backend": "aria2",
        # Rayburst 默认监听 127.0.0.1:16800/jsonrpc；可在 Rayburst 设置里核对
        "rpc_url": "http://127.0.0.1:16800/jsonrpc",
        # RPC 密钥；Rayburst/Motrix 默认 "token"，如设置为空则不传 token
        "secret": "token",
        # 轮询下载状态间隔（秒）
        "poll_interval": 1.0,
        # 单个章节整体下载超时（秒）
        "task_timeout": 600,
        # 每个任务的最大连接数
        "connections_per_server": 4,
    },
    # 章节目录命名：site=站点标签(Chapter 1) / cn=第N话
    "chapter_dir_style": "site",
    # 下载目录布局：nested=二级文件夹（<漫画>/Chapter 1/0.png）；
    #              flat=扁平（<漫画>/Chapter1_0.png，图片文件名带章节前缀）
    "chapter_layout": "flat",
    # 图片文件名补零位数：1 -> 0.jpg, 1.jpg, ..., 10.jpg（从 0 顺序编号，不补零）
    "image_pad_digits": 1,
    # 下载完成后是否转成 PNG（源图通常是 jpg；True 时输出 0.png 并删除源文件）
    "convert_to_png": False,
}


def load_config(path: Path | str | None = None) -> dict[str, Any]:
    """加载配置；文件缺失时写入默认配置并返回。"""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not cfg_path.exists():
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(
            json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return json.loads(json.dumps(DEFAULT_CONFIG))

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))

    def _merge(base: dict, override: dict) -> dict:
        out = dict(base)
        for k, v in override.items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k] = _merge(out[k], v)
            else:
                out[k] = v
        return out

    return _merge(json.loads(json.dumps(DEFAULT_CONFIG)), raw)


def resolve_path(root: Path, p: str | Path) -> Path:
    """把配置里的相对路径解析为相对项目根目录的绝对路径。"""
    path = Path(p)
    return path if path.is_absolute() else root / path
