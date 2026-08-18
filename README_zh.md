# DirTree Snapshot

**中文文档** | [English](README.md)

一个轻量级的 Windows CLI 工具，递归扫描指定目录，生成一份可移植、可读的文件和文件夹快照。可选择记录文件大小、SHA-256 哈希值和文件元数据，方便日后验证文件是否丢失或损坏。快照可以并排对比，也可以直接与实时目录校验，确认每个文件都在且未被修改。

## 为什么需要这个工具

重装系统或将数据迁移到新硬盘时，你往往需要快速复制大量文件。事后仅凭肉眼查看目标文件夹，几乎无法判断每个文件是否都完整地复制过去了。DirTree Snapshot 正是为了解决这一问题而生。

**典型工作流：**

1. 备份前，扫描源目录并保存一份快照（可选附带 SHA-256 哈希值）。
2. 用任意方式将文件复制到备份位置。
3. 扫描备份目录，保存第二份快照。
4. 对比两份快照，或直接用快照校验备份目录，确认每个文件都在且未被修改。

日常备份重要文件时同样适用：在每次备份旁边保留一份快照，几周或几个月后无需重新扫描原始文件即可审计备份内容。

## 功能

- 递归记录所有可读取的文件夹和文件，包括空文件夹。
- 文件夹优先排列；名称不区分大小写，排序稳定。
- 每个文件条目包含以字节为单位的文件大小。
- 可选的 SHA-256 内容哈希，带实时进度条。
- **持久化哈希缓存**：已计算的哈希值存储在本地 SQLite 数据库中，当文件大小和修改时间未变时自动复用，重复扫描同一目录时速度大幅提升。
- **中断恢复**：`--resume` 复用已缓存的哈希值，跳过中断前已处理的文件。
- **文件元数据**：`--metadata` 记录修改时间、元数据变更时间、权限模式和只读状态。
- **排除与包含规则**：`--exclude PATTERN` 和 `--include PATTERN` 按名称或通配符筛选文件和目录；可重复使用。
- **可复用扫描配置**：`--config FILE` 从 JSON 文件加载扫描设置；命令行参数优先于配置文件。
- **快速扫描模式**：`--fast` 跳过大型目录的哈希工作量预扫描。
- `--dirs-only` 模式：仅记录目录结构。
- 生成独立的交互式 HTML 报告，支持搜索、展开/折叠、筛选、深色模式和打印。
- 同时支持纯文本快照和规范 JSON 快照，便于脚本处理和机器读取。
- **快照对比**：对比两份快照文件（HTML、文本或 JSON），检测新增、缺失、已更改和已重命名（按哈希匹配）的文件，支持可选的路径大小写敏感匹配。
- 对比报告支持 HTML、文本或 JSON 格式，可选包含未更改项。
- **实时校验**：将保存的快照与当前目录进行校验，实时查看哪些文件缺失、新增或已更改。
- **增强 HTML 报告**：类型/大小/扩展名筛选、总大小统计、路径和 SHA-256 复制按钮、深色/浅色主题切换。
- 每份快照自带时间戳；输出文件名默认包含时间戳，避免意外覆盖。
- 自动检测符号链接和 Windows junction，标记为 `[link-not-followed]`，不进入链接目标。
- 文本输出为带 BOM 的 UTF-8，Windows 记事本可直接显示非 ASCII 文件名。
- 原子写入：快照先写入临时文件，完成后替换目标文件，中断不会留下半份文件。
- 纯 Python 标准库实现，无任何第三方依赖。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.9 或更高版本

确认 Python 安装：

```powershell
py -3 --version
```

## 快速开始

双击 `dirtree.cmd`，或在 CMD / PowerShell 窗口中运行：

```bat
dirtree.cmd
```

无参数启动时，程序打开一个交互式菜单：

```text
DirTree Snapshot

[1] Generate snapshot
[2] Compare two snapshots
[3] Verify snapshot against directory
[4] Manage hash cache
[5] Generate snapshot from config
Choose action (1/2/3/4/5, default 1): 1
Directory to scan: D:\Backup\ProjectA
Calculate SHA-256 hashes? (y/N): y
Include file metadata? (y/N):
Fast scan for large directories? (y/N):
Resume interrupted scan? (y/N):
Use saved hash cache? (Y/n):
Output format (html/text/json, default html):
```

你可以手动输入路径，也可以将文件夹拖到终端窗口中；路径两侧的引号会被自动去除。
如果窗口显示 `Python 3 was not found`，请安装 Python 3.9 或更高版本。

## 命令用法

### 快照命令

扫描指定目录（默认输出 HTML）：

```powershell
dirtree.cmd "D:\Backup\ProjectA"
```

指定输出文件：

```powershell
dirtree.cmd "D:\Backup\ProjectA" -o "D:\TreeLists\ProjectA-tree.html"
```

输出纯文本或 JSON：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --format text
dirtree.cmd "D:\Backup\ProjectA" --format json
dirtree.cmd "D:\Backup\ProjectA" -o "D:\TreeLists\ProjectA-tree.txt"
```

仅记录文件夹，不记录文件：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --dirs-only
```

包含 SHA-256 哈希值（带进度条和缓存）：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash
```

包含文件元数据（时间戳、权限、只读状态）：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash --metadata
```

排除文件或目录：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --exclude .git --exclude node_modules --exclude "*.tmp"
```

仅包含特定文件类型：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --include "*.py" --include "*.json"
```

使用扫描配置文件：

```powershell
dirtree.cmd --config dirtree.example.json
```

大型目录快速扫描：

```powershell
dirtree.cmd "D:\Backup\LargeProject" --hash --fast
```

恢复中断的扫描：

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash --resume
```

### 对比命令

对比两份快照文件（HTML、文本或 JSON）：

```powershell
dirtree.cmd compare LEFT.html RIGHT.html
dirtree.cmd compare LEFT.json RIGHT.json
```

指定输出报告：

```powershell
dirtree.cmd compare LEFT.html RIGHT.html -o "D:\Reports\comparison.html"
```

输出文本或 JSON 对比报告：

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --format text
dirtree.cmd compare LEFT.html RIGHT.html --format json -o comparison.json
dirtree.cmd compare LEFT.txt RIGHT.txt -o comparison.txt
```

在报告中包含未更改项：

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --include-unchanged
```

路径大小写敏感对比：

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --case-sensitive
```

### 校验命令

将保存的快照与实时目录进行校验：

```powershell
dirtree.cmd verify "D:\Tools\ProjectA-tree-20260808-092915.html" "D:\Backup\ProjectA"
```

强制对实时文件计算 SHA-256：

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --hash
```

仅对比路径和大小，不计算哈希：

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --no-hash
```

在校验报告中包含未更改项：

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --include-unchanged
```

### 哈希缓存管理

查看缓存信息：

```powershell
dirtree.cmd cache info
```

清除所有缓存条目：

```powershell
dirtree.cmd cache clear
```

清理超过 N 天未使用的条目（默认 30）：

```powershell
dirtree.cmd cache prune
dirtree.cmd cache prune --days 7
```

### 扫描配置文件

从 JSON 文件复用扫描设置：

```json
{
  "directory": ".",
  "output": "./project-tree.json",
  "format": "json",
  "hash": true,
  "metadata": true,
  "fast": true,
  "exclude": [".git", "node_modules", "*.tmp"],
  "include": []
}
```

命令行参数优先于配置文件。配置中的相对路径相对于配置文件所在目录解析。

## 全部参数

**快照：**

| 参数 | 说明 |
| --- | --- |
| `directory` | 要扫描的目录；省略时进入交互模式 |
| `--config FILE` | 从 JSON 配置文件复用扫描设置 |
| `-o, --output FILE` | 输出文件路径 |
| `--format {html,text,json}` | 输出格式；从扩展名推断，否则默认 HTML |
| `-d, --dirs-only` | 只记录目录和链接，不记录普通文件 |
| `--hash` | 包含 SHA-256 哈希值并显示哈希进度 |
| `--metadata` | 包含时间戳、权限、模式和只读状态 |
| `--fast` | 跳过大型目录的哈希工作量预扫描 |
| `--resume` | 恢复中断的扫描，复用已缓存的哈希值 |
| `--no-cache` | 计算哈希时不读取或更新缓存 |
| `--exclude PATTERN` | 排除匹配的文件或目录；可重复使用 |
| `--include PATTERN` | 仅包含匹配的文件或链接；可重复使用 |
| `--version` | 显示当前版本 |

**对比：**

| 参数 | 说明 |
| --- | --- |
| `left` | 源端或较早的快照文件 |
| `right` | 备份端或较晚的快照文件 |
| `-o, --output FILE` | 报告输出文件 |
| `--format {html,text,json}` | 报告格式；从扩展名推断，否则默认 HTML |
| `--include-unchanged` | 在报告中显示未更改项 |
| `--case-sensitive` | 路径大小写敏感对比 |

**校验：**

| 参数 | 说明 |
| --- | --- |
| `snapshot` | 保存的快照文件（HTML、文本或 JSON） |
| `directory` | 要校验的当前目录 |
| `-o, --output FILE` | 校验报告输出文件 |
| `--format {html,text}` | 报告格式；从 `.txt` 推断，否则默认 HTML |
| `--hash` | 强制对实时文件计算 SHA-256 |
| `--no-hash` | 仅对比路径、类型和大小，不计算哈希 |
| `--no-cache` | 不使用或更新本地哈希缓存 |
| `--include-unchanged` | 在报告中包含未更改项 |
| `--case-sensitive` | 路径大小写敏感对比 |

**缓存：**

| 参数 | 说明 |
| --- | --- |
| `action` | `info`、`clear` 或 `prune` |
| `--days N` | 清理阈值天数（默认 30） |
| `--file PATH` | 指定缓存数据库路径 |

`--hash` 和 `--dirs-only` 互斥。`--no-cache` 需要 `--hash`。
`--resume` 隐含 `--hash` 并使用缓存。`--output` 的父目录必须已存在。

## 快照格式

### HTML（默认）

一个独立的交互式 HTML 页面，包含：

- 可搜索的文件/目录树（输入即过滤，支持按路径或 SHA-256 搜索，Esc 清除）。
- 类型筛选（全部/文件/目录/链接）。
- 文件大小筛选（最小和最大 KiB）。
- 文件扩展名筛选（逗号分隔，如 `.py,.json`）。
- 全部展开 / 全部折叠按钮。
- 深色/浅色主题切换（通过 localStorage 记住）。
- 内联显示文件大小和可选的 SHA-256 哈希值。
- 相对路径和 SHA-256 哈希值可复制到剪贴板。
- 页头显示文件总大小。
- 页头汇总目录、文件、链接和错误数。
- 页头显示快照时间戳。
- 打印友好的布局（打印时自动展开所有节点）。

### 文本

```text
# DirTree Snapshot v2
# Created: 2026-08-08T09:29:15+08:00
# Mode: files-and-directories
# Details: size-bytes,metadata,sha256
# Paths: relative-to-root
.
|-- docs/
|   |-- images/
|   |   `-- logo.png [size=12345 B, sha256=9f2a…e7c1]
|   `-- guide.txt [size=678 B, sha256=3b8d…4a20]
|-- src/
|   `-- main.py [size=4567 B, sha256=1c5f…9a3b]
`-- README.md [size=4856 B, sha256=7e2b…0d4f]
# Summary: directories=3 files=4 links=0 errors=0
```

### JSON

规范化的机器可读格式，具有结构化 schema，包含工具版本、时间戳、模式、哈希算法、统计数据和一个扁平的条目列表（路径、类型、大小、可选 SHA-256 和可选元数据）。

## 错误处理

扫描过程中遇到权限错误、文件消失或目录读取失败时，对应条目标记为 `[unreadable]`，程序继续处理其他内容。具体错误信息输出到 stderr，程序以退出码 1 表示快照不完整。

- **0** — 快照、对比或校验成功完成。
- **1** — 写入失败、快照生成但存在读取错误，或校验发现差异。
- **2** — 目录或文件参数无效。
- **3** — 启动器工作目录错误。

## 安装为全局命令

项目包含 `pyproject.toml`，可安装为当前 Python 环境中的 `dirtree` 命令：

```powershell
py -3 -m pip install .
dirtree "D:\Backup\ProjectA"
dirtree compare LEFT.html RIGHT.html
dirtree verify SNAPSHOT.html "D:\Backup\ProjectA"
dirtree cache info
```

## 可选：打包为 EXE

工具本身无第三方依赖。需要分发给未安装 Python 的 Windows 电脑时，可使用 PyInstaller：

```powershell
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --name dirtree dirtree.py
.\dist\dirtree.exe
```

生成的程序位于 `dist\dirtree.exe`。请将 `dirtree_assets` 文件夹放在可执行文件旁边，以便 HTML 模板正确加载。

## 开发与测试

项目仅使用 Python 标准库。运行测试：

```powershell
py -3 -m unittest discover -s tests -v
```

## License

MIT License，详见 `LICENSE`。
