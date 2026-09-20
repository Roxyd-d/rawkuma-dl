#!/usr/bin/env python3
"""Rawkuma 漫画下载器命令行入口。

用法示例:
    uv run main.py -url https://rawkuma.net/manga/<slug>/
    uv run main.py bookmark
    uv run main.py update
    uv run main.py login
"""

from rawkuma.cli import main

if __name__ == "__main__":
    main()
