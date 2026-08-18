# DirTree Snapshot

[中文文档](README_zh.md) | **English**

A lightweight Windows CLI tool that recursively scans a directory and produces a
portable, human-readable snapshot of every file and folder inside it. Optionally
records file sizes, SHA-256 hashes, and file metadata so you can later verify
that nothing was lost or corrupted. Snapshots can be compared side by side or
verified against a live directory to confirm that every file is present and
unchanged.

## Why this tool exists

When you reinstall your operating system or migrate data to a new drive, you
often copy thousands of files in a hurry. Afterwards it is nearly impossible to
tell — just by eyeballing the destination folder — whether every file made it
across intact. DirTree Snapshot was built to solve exactly this problem.

**Typical workflow:**

1. Before backing up, scan the source directory and save a snapshot (optionally
   with SHA-256 hashes).
2. Copy your files to the backup location using any method you like.
3. Scan the backup directory and save a second snapshot.
4. Compare the two snapshots, or verify the backup directly against a snapshot,
   to confirm every file is present and unchanged.

It is equally useful for **routine backups of important files**: keep a snapshot
alongside each backup so you can audit it weeks or months later without
re-scanning the original.

## Features

- Recursively records every readable folder and file, including empty folders.
- Folders listed first; case-insensitive, stable sort order.
- File sizes in bytes for every file entry.
- Optional SHA-256 content hashing with live progress bar.
- **Persistent hash cache**: previously computed hashes are stored in a local
  SQLite database and reused when file size and mtime are unchanged, so
  repeated scans of the same directory are fast.
- **Resume interrupted scans**: `--resume` reuses cached hashes to skip files
  that were already hashed before the interruption.
- **File metadata**: `--metadata` records modified time, metadata change time,
  permissions mode, and read-only status for every entry.
- **Exclude and include rules**: `--exclude PATTERN` and `--include PATTERN`
  filter files and directories by name or glob pattern; may be repeated.
- **Reusable scan configuration**: `--config FILE` loads scan settings from a
  JSON file; CLI arguments override config values.
- **Fast scan mode**: `--fast` skips the hash workload pre-scan for large
  directories.
- `--dirs-only` mode to record just the directory structure.
- Generates standalone interactive HTML reports with search, expand/collapse,
  filters, dark mode, and print support.
- Also supports plain-text snapshots and canonical JSON snapshots for scripting
  and machine processing.
- **Snapshot comparison**: compares two snapshot files (HTML, text, or JSON)
  and detects added, removed, changed, and renamed files (by hash), with
  optional case-sensitive path matching.
- Comparison reports in HTML, text, or JSON format, with optional unchanged
  items.
- **Live verification**: verify a saved snapshot against a current directory to
  see which files are missing, added, or changed in real time.
- **Enhanced HTML reports**: type/size/extension filters, total size metric,
  copy-to-clipboard for paths and SHA-256 hashes, and dark/light theme toggle.
- Each snapshot carries a timestamp; output filenames include the timestamp by
  default to avoid accidental overwrites.
- Symlinks and Windows junctions are detected and marked
  `[link-not-followed]`; link targets are never traversed.
- Text output is UTF-8 with BOM, so Windows Notepad displays non-ASCII filenames
  correctly.
- Atomic write: the snapshot is built in a temporary file and then replaces the
  destination, so an interrupted run never leaves a half-written file.
- Standalone: pure Python standard library, zero third-party dependencies.

## Requirements

- Windows 10 or Windows 11
- Python 3.9 or later

Check your Python installation:

```powershell
py -3 --version
```

## Quick start

Double-click `dirtree.cmd`, or run from a CMD / PowerShell window:

```bat
dirtree.cmd
```

When launched without arguments the program opens an interactive menu:

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

You can type the path manually or drag a folder onto the terminal; surrounding
quotes are stripped automatically. If the window shows `Python 3 was not found`,
install Python 3.9 or later.

## Usage

### Snapshot command

Scan a specific directory (defaults to HTML output):

```powershell
dirtree.cmd "D:\Backup\ProjectA"
```

Specify an output file:

```powershell
dirtree.cmd "D:\Backup\ProjectA" -o "D:\TreeLists\ProjectA-tree.html"
```

Output plain text or JSON:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --format text
dirtree.cmd "D:\Backup\ProjectA" --format json
dirtree.cmd "D:\Backup\ProjectA" -o "D:\TreeLists\ProjectA-tree.txt"
```

Record only folders, skip files:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --dirs-only
```

Include SHA-256 hashes (with progress bar and cache):

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash
```

Include file metadata (timestamps, permissions, read-only):

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash --metadata
```

Exclude files or directories:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --exclude .git --exclude node_modules --exclude "*.tmp"
```

Include only specific file types:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --include "*.py" --include "*.json"
```

Use a scan configuration file:

```powershell
dirtree.cmd --config dirtree.example.json
```

Fast scan for large directories:

```powershell
dirtree.cmd "D:\Backup\LargeProject" --hash --fast
```

Resume an interrupted scan:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash --resume
```

### Compare command

Compare two snapshot files (HTML, text, or JSON):

```powershell
dirtree.cmd compare LEFT.html RIGHT.html
dirtree.cmd compare LEFT.json RIGHT.json
```

Specify an output report:

```powershell
dirtree.cmd compare LEFT.html RIGHT.html -o "D:\Reports\comparison.html"
```

Output a text or JSON comparison report:

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --format text
dirtree.cmd compare LEFT.html RIGHT.html --format json -o comparison.json
dirtree.cmd compare LEFT.txt RIGHT.txt -o comparison.txt
```

Include unchanged items in the report:

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --include-unchanged
```

Case-sensitive path comparison:

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --case-sensitive
```

### Verify command

Verify a saved snapshot against a live directory:

```powershell
dirtree.cmd verify "D:\Tools\ProjectA-tree-20260808-092915.html" "D:\Backup\ProjectA"
```

Force SHA-256 hashing of live files:

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --hash
```

Compare paths and sizes only, without hashing:

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --no-hash
```

Include unchanged items in the verification report:

```powershell
dirtree.cmd verify SNAPSHOT.html "D:\Backup\ProjectA" --include-unchanged
```

### Hash cache management

Show cache info:

```powershell
dirtree.cmd cache info
```

Clear all cache entries:

```powershell
dirtree.cmd cache clear
```

Prune entries unused for more than N days (default 30):

```powershell
dirtree.cmd cache prune
dirtree.cmd cache prune --days 7
```

### Scan configuration file

Reuse scan settings from a JSON file:

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

CLI arguments override config values. Relative paths in the config are resolved
relative to the config file's directory.

## All options

**Snapshot:**

| Option | Description |
| --- | --- |
| `directory` | Directory to scan; prompts when omitted |
| `--config FILE` | Reuse scan settings from a JSON configuration file |
| `-o, --output FILE` | Output file path |
| `--format {html,text,json}` | Output format; inferred from extension, otherwise HTML |
| `-d, --dirs-only` | Record directories and links only; omit files |
| `--hash` | Include SHA-256 hashes and show hashing progress |
| `--metadata` | Include timestamps, permissions, mode, and read-only metadata |
| `--fast` | Skip the hash workload pre-scan for large directories |
| `--resume` | Resume an interrupted scan by reusing cached hashes |
| `--no-cache` | Calculate hashes without reading or updating the cache |
| `--exclude PATTERN` | Exclude matching files or directories; may be repeated |
| `--include PATTERN` | Include matching files or links; may be repeated |
| `--version` | Print the current version |

**Compare:**

| Option | Description |
| --- | --- |
| `left` | Source or earlier snapshot file |
| `right` | Backup or later snapshot file |
| `-o, --output FILE` | Report output file |
| `--format {html,text,json}` | Report format; inferred from extension, otherwise HTML |
| `--include-unchanged` | Show unchanged items in the report |
| `--case-sensitive` | Compare paths with case sensitivity |

**Verify:**

| Option | Description |
| --- | --- |
| `snapshot` | Saved snapshot file (HTML, text, or JSON) |
| `directory` | Current directory to verify |
| `-o, --output FILE` | Verification report output file |
| `--format {html,text}` | Report format; inferred from `.txt`, otherwise HTML |
| `--hash` | Force SHA-256 hashing of live files |
| `--no-hash` | Compare paths, types, and sizes without hashing |
| `--no-cache` | Do not use or update the local hash cache |
| `--include-unchanged` | Include unchanged entries in the report |
| `--case-sensitive` | Compare paths with case sensitivity |

**Cache:**

| Option | Description |
| --- | --- |
| `action` | `info`, `clear`, or `prune` |
| `--days N` | Stale age for prune (default: 30) |
| `--file PATH` | Override cache database path |

`--hash` and `--dirs-only` are mutually exclusive. `--no-cache` requires
`--hash`. `--resume` implies `--hash` and uses the cache. The parent
directory of `--output` must already exist.

## Snapshot formats

### HTML (default)

A standalone, interactive HTML page with:

- Searchable file/folder tree (type to filter by path or SHA-256, Esc to clear).
- Type filter (all/files/directories/links).
- File size filter (minimum and maximum KiB).
- File extension filter (comma-separated, e.g. `.py,.json`).
- Expand-all / collapse-all buttons.
- Dark/light theme toggle (remembered via localStorage).
- File sizes and optional SHA-256 hashes displayed inline.
- Copy-to-clipboard for relative paths and SHA-256 hashes.
- Total file size metric in the header.
- Summary metrics (directories, files, links, errors) in the header.
- Snapshot timestamp in the header.
- Print-friendly layout (all nodes auto-expand when printing).

### Text

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

Canonical machine-readable format with a structured schema, including tool
version, timestamp, mode, hash algorithm, statistics, and a flat list of
entries with path, kind, size, optional SHA-256, and optional metadata.

## Error handling

If the scanner encounters a permission error, a missing file, or a directory
that disappears mid-scan, it records the entry as `[unreadable]` and continues.
A warning with the specific error is printed to stderr, and the program exits
with code 1 to signal that the snapshot is incomplete.

- **0** — snapshot, comparison, or verification completed successfully.
- **1** — write failure, snapshot completed with read errors, or verification
  found differences.
- **2** — invalid directory or file argument.
- **3** — launcher working-directory error.

## Install as a global command

The project includes `pyproject.toml` and can be installed into the current
Python environment:

```powershell
py -3 -m pip install .
dirtree "D:\Backup\ProjectA"
dirtree compare LEFT.html RIGHT.html
dirtree verify SNAPSHOT.html "D:\Backup\ProjectA"
dirtree cache info
```

## Optional: build a standalone EXE

The tool itself has no third-party dependencies. For distribution to Windows
machines without Python, use PyInstaller:

```powershell
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --onefile --name dirtree dirtree.py
.\dist\dirtree.exe
```

The executable is placed in `dist\dirtree.exe`. Keep the `dirtree_assets`
folder alongside the executable so HTML templates load correctly.

## Development and testing

The project uses only the Python standard library. Run the test suite:

```powershell
py -3 -m unittest discover -s tests -v
```

## License

MIT License — see `LICENSE`.
