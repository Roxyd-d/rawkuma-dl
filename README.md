# Rawkuma 漫画下载器

对 [rawkuma.net](https://rawkuma.net) 的漫画进行命令行爬取，图片下载交给第三方下载器
[Rayburst](https://rayburst.pages.dev)（开源下载管理器，底层为 Aria2），并维护一份本地
JSON 库用于增量下载与更新检查。

## 特性

- **命令行运行，无需图形界面**；唯一需要浏览器的一步是登录
- 支持两种下载入口：
  - `-url <漫画页链接>`：直接按链接下载
  - `bookmark`：读取收藏夹，按索引选择下载
- 自动增量：已下载过的章节跳过，漫画更新后只补新章节
- `update` 检查更新，可一键补下新章节
- 下载记录以 JSON 保存在本地（`library.json`）

## 环境要求

- [uv](https://docs.astral.sh/uv/)（Python 3.12 由 uv 自动管理）
- [Rayburst](https://rayburst.pages.dev) 桌面端（保持运行，用于实际下载图片）

## 安装

```bash
# 1. 安装依赖（首次）
uv sync

# 2. 安装登录用的浏览器内核（首次）
uv run playwright install msedge

# 3. 启动 Rayburst 桌面端，确认 RPC 已开启（默认 http://127.0.0.1:16800/jsonrpc，密钥 token）
#    如与默认不同，修改 config.json 的 downloader 段即可
```

## 快速开始

```bash
# 第一步：浏览器登录（会弹出浏览器窗口，登录后自动保存会话）
uv run main.py login

# 方式一：按链接下载整部漫画（已下载的章节自动跳过）
uv run main.py -url https://rawkuma.net/manga/saikyou-de-modori-chuunen-boukensha-wa-imasara-inochi-nante-kaketakunai/

# 方式二：按收藏夹索引下载
uv run main.py bookmark
# 0 Saikyou De Modori Chuunen Boukensha wa, Imasara Inochi Nante Kaketakunai
# 1 Hazure Skill "Soine" ga Kakuseishi, ...
# 输入序号下载: 0

# 检查更新
uv run main.py update
# 0 Saikyou De Modori ...
# 有更新
# Chapter 27.1 Chapter 27.2 Chapter 27.3
```

## 命令参考

| 命令 | 说明 |
|---|---|
| `uv run main.py login` | 打开浏览器登录，保存 Cookie 到 `cookies.json` |
| `uv run main.py -url <链接>` | 下载指定漫画（自动跳过已下载章节） |
| `uv run main.py bookmark` | 打印收藏夹列表并交互选择序号下载；`--index N` 可直接指定 |
| `uv run main.py update` | 逐部检查更新；`--download` 自动补下新章节 |
| `uv run main.py list` | 查看本地库 |

可选参数（配合 `-url` / `bookmark`）：

- `--chapters 1,3-5`：只下载指定章节（按站点章节号）
- `--latest`：只下载最新章节
- `--limit N`：本次最多下载 N 个章节（适合分批下载）
- `--root <目录>`：覆盖下载根目录

## 下载目录结构

```
downloads/
└── Saikyou De Modori Chuunen Boukensha wa, Imasara Inochi Nante Kaketakunai/
    ├── Chapter 1/
    │   ├── 000.jpg
    │   ├── 001.jpg
    │   └── ...
    ├── Chapter 2.1/
    │   └── ...
    └── ...
```

说明：

- 章节目录默认使用站点标签（`Chapter 1`、`Chapter 6.3`）；如希望用中文「第N话」，
  将 `config.json` 的 `chapter_dir_style` 改为 `"cn"`
- 图片文件按序号补零命名（`000`、`001`…），扩展名保留源图格式（站点为 jpg）；
  如需统一为 `.png`，把 `config.json` 的 `convert_to_png` 设为 `true`（下载后自动转换）

## 配置（config.json，首次运行自动生成）

```json
{
  "base_url": "https://rawkuma.net",
  "cookies_file": "cookies.json",
  "library_file": "library.json",
  "download_root": "downloads",
  "downloader": {
    "backend": "aria2",
    "rpc_url": "http://127.0.0.1:16800/jsonrpc",
    "secret": "token",
    "poll_interval": 1.0,
    "task_timeout": 600,
    "connections_per_server": 4
  },
  "chapter_dir_style": "site",
  "image_pad_digits": 3,
  "convert_to_png": false
}
```

- `downloader`：对接 Rayburst 的 aria2 兼容 JSON-RPC。若 Rayburst 的 RPC 端口/密钥不同，
  在应用设置里查看后修改这两项即可；`secret` 留空表示无密钥
- `download_root`：下载根目录（相对项目根目录）

## 更新检查原理

每次成功下载章节后，章节 ID、页面数、文件清单都会写入 `library.json`。
`update` 时重新抓取漫画页的最新章节列表，与本地记录比对，只报告（并可下载）新增章节，
因此本地漫画目录可安全改名/移动，判断依据始终是站点章节 ID。

## 常见问题

- **提示「无法连接下载器」**：Rayburst 未启动，或 RPC 地址/密钥与 `config.json` 不一致
- **提示「该页面需要登录」**：Cookie 缺失或过期，重新执行 `uv run main.py login`
- **收藏夹为空**：确认已登录，且收藏夹里确有内容