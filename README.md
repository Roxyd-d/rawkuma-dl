# Rawkuma 漫画下载器

对 [rawkuma.net](https://rawkuma.net) 的漫画进行命令行爬取，图片在爬虫进程内直接并发下载
（不依赖任何第三方下载器），并维护一份本地 JSON 库用于增量下载与更新检查。

## 特性

- **命令行运行，无需图形界面**；唯一需要浏览器的一步是登录
- **进程内直下**：图片由 Python 直接并发下载（默认 4 并发），无需启动 Rayburst/aria2 等下载器
- 支持两种下载入口：
    - `-url <漫画页链接>`：直接按链接下载
    - `bookmark`：读取收藏夹，按索引选择下载
- 自动增量：已下载过的章节跳过，漫画更新后只补新章节
- `update` 检查更新，发现新章节时询问是否下载（`--download` 直接自动下载）
- 下载记录以 JSON 保存在本地（`library.json`）
- 支持 nested / flat 两种目录布局，旧项目可一键转换为扁平结构

## 环境要求

- [uv](https://docs.astral.sh/uv/)（Python 3.12 由 uv 自动管理）

## 安装

```bash
# 1. 安装依赖（首次）
uv sync

# 2. 安装登录用的浏览器内核（首次）
uv run playwright install msedge
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

# 检查更新（新章节先下载到漫画目录的 update/ 子文件夹）
uv run main.py update
# 0 Saikyou De Modori ...
# 有更新
# Chapter 27.1 Chapter 27.2 Chapter 27.3
# 是否下载新章节？(y/N): y

# 不想交互：--download 直接自动下载新章节
uv run main.py update --download

# 下载完成后询问是否把 update/ 内容合并到正式目录
uv run main.py update --merge
# ...
# 是否合并 update 内容到正式目录？(y/N): y
```

## 命令参考

| 命令 | 说明 |
|---|---|
| `uv run main.py login` | 打开浏览器登录，保存 Cookie 到 `cookies.json` |
| `uv run main.py -url <链接>` | 下载指定漫画（自动跳过已下载章节） |
| `uv run main.py bookmark` | 打印收藏夹列表并交互选择序号下载；`--index N` 可直接指定 |
| `uv run main.py update` | 逐部检查更新，发现更新后询问是否下载；新章节下载到漫画目录的 `update/` 子文件夹；`--download` 直接自动下载，`--merge` 下载后询问是否合并到正式目录 |
| `uv run main.py list` | 查看本地库 |
| `uv run main.py merge` | 把漫画目录下 `update/` 子文件夹合并到正式目录（交互选择漫画；`--index N` 可直接指定） |
| `uv run main.py convert` | 交互选择漫画并转换目录结构；`--mode` 可选 `flat` / `nested`（默认 `flat`），`--index N` 可直接指定 |

可选参数（配合 `-url` / `bookmark`）：

- `--chapters 1,3-5`：只下载指定章节（按站点章节号）
- `--latest`：只下载最新章节
- `--limit N`：本次最多下载 N 个章节（适合分批下载）
- `--root <目录>`：覆盖下载根目录

## 下载目录结构

默认 **nested（二级文件夹）**：

```
downloads/
└── Saikyou De Modori Chuunen Boukensha wa, Imasara Inochi Nante Kaketakunai/
    ├── Chapter 1/
    │   ├── 0.jpg
    │   ├── 1.jpg
    │   └── ...
    ├── Chapter 2.1/
    │   └── ...
    └── ...
```

也可切换为 **flat（扁平，不建二级文件夹，文件名带章节前缀）**：

```
downloads/
└── Saikyou De Modori Chuunen Boukensha wa, Imasara Inochi Nante Kaketakunai/
    ├── Chapter1_0.jpg
    ├── Chapter1_1.jpg
    ├── ...
    ├── Chapter7.1_0.jpg
    └── ...
```

说明：

- 章节目录名默认使用站点标签（`Chapter 1`、`Chapter 6.3`）；如希望用中文「第N话」，
  将 `config.json` 的 `chapter_dir_style` 改为 `"cn"`
- 图片文件从 `0` 开始顺序编号（`0`、`1`、`2`…`10`…），扩展名保留源图格式（站点为 jpg）；
  如需补零，可将 `config.json` 的 `image_pad_digits` 设为 `3`（`000`、`001`…）；
  如需统一为 `.png`，把 `config.json` 的 `image_format` 设为 `"png"`（下载后自动转换）
- 目录布局由 `config.json` 的 `chapter_layout` 控制：`"nested"`（二级文件夹）或 `"flat"`（扁平）
- 旧版本下载的 `000`、`001`… 命名文件会在下次运行时自动改名为新命名，无需重新下载
- **目录结构转换**：`uv run main.py convert` 会先列出本地库漫画，输入序号后转换该漫画的目录结构。
  默认 `--mode flat`：二级文件夹重命名为扁平（`Chapter 1/0.jpg` → `Chapter1_0.jpg`）；
  `--mode nested` 反向（`Chapter1_0.jpg` → `Chapter 1/0.jpg`）。转换同步更新 `library.json`，
  完成后自动把 `chapter_layout` 设为与模式一致，后续新下载沿用该结构；
  也可用 `uv run main.py convert --index 0 --mode flat` 直接指定漫画与方向

## 配置（config.json，首次运行自动生成）

```json
{
    "base_url": "https://rawkuma.net",
    "cookies_file": "cookies.json",
    "library_file": "library.json",
    "download_root": "downloads",
    "downloader": {
        "backend": "internal",
        "max_concurrent": 4,
        "retries": 3,
        "request_timeout": 120
    },
    "chapter_dir_style": "site",
    "chapter_layout": "nested",
    "image_pad_digits": 1,
    "image_format": "png"
}
```

- `downloader`：进程内下载参数。`max_concurrent` 为并发下载的图片数；`retries` 为单张图片
  失败重试次数；`request_timeout` 为单张图片请求超时（秒）。无需任何第三方下载器
- `image_format`：`original` 不转换（保留源图 jpg）；`png` 统一转 PNG；`jpg` 统一转 JPG
  （旧版 `convert_to_png` 配置会自动迁移）
- `download_root`：下载根目录（相对项目根目录）
- `chapter_layout`：`"nested"`（默认，二级文件夹）或 `"flat"`（扁平，文件名带章节前缀）

## 更新检查原理

每次成功下载章节后，章节 ID、页面数、文件清单都会写入 `library.json`。
`update` 时重新抓取漫画页的最新章节列表，与本地记录比对，只报告（并可下载）新增章节，
因此本地漫画目录可安全改名/移动，判断依据始终是站点章节 ID。

更新下载的**新章节默认进入漫画目录下的 `update/` 子文件夹**（结构与正式布局一致），不会
直接改动正式目录；确认无误后运行 `uv run main.py merge`（或更新时加 `--merge` 下载完成后
询问），把 `update/` 内容合并到正式位置并同步更新 `library.json`。
合并可重复执行且幂等：目标文件已存在时会跳过，不覆盖已有内容。

## 常见问题

- **某章有图片下载失败**：会自动重试 `retries` 次；仍失败的章节会列出文件名，重跑 `-url` 或
  `update` 即可补下（已下载的自动跳过）
- **提示「该页面需要登录」**：Cookie 缺失或过期，重新执行 `uv run main.py login`
- **收藏夹为空**：确认已登录，且收藏夹里确有内容
- **下载速度慢**：调大 `config.json` 的 `downloader.max_concurrent`（如 8），并注意不要过大
  以免被站点限流