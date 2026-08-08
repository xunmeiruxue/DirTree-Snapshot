# DirTree Snapshot

A lightweight Windows CLI tool that recursively scans a directory and produces a
portable, human-readable snapshot of every file and folder inside it. Optionally
records file sizes and SHA-256 hashes so you can later verify that nothing was
lost or corrupted. Snapshots can be compared side by side or verified against a
live directory to confirm that every file is present and unchanged.

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
- `--dirs-only` mode to record just the directory structure.
- Generates standalone interactive HTML reports with search, expand/collapse,
  and print support.
- Also supports plain-text snapshots and canonical JSON snapshots for scripting
  and machine processing.
- **Snapshot comparison**: compares two snapshot files (HTML, text, or JSON) and
  detects added, removed, and changed files, with optional case-sensitive path
  matching.
- Comparison reports in HTML or text format, with optional unchanged items.
- **Live verification**: verify a saved snapshot against a current directory to
  see which files are missing, added, or changed in real time.
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
Choose action (1/2/3/4, default 1): 1
Directory to scan: D:\Backup\ProjectA
Calculate SHA-256 hashes? (y/N): y
Use saved hash cache? (Y/n):
Output format (html/text/json, default html):
```

You can type the path manually or drag a folder onto the terminal; surrounding
quotes are stripped automatically. If the window shows `Python 3 was not found`,
install Python 3.9 or later.

### Generate a snapshot

```text
Scanning: D:\Backup\ProjectA
Output format: html
SHA-256: enabled
Hash cache: ~/.dsh/dirtree-cache.sqlite3
Snapshot time: 2026-08-08 09:29:15 +0800
Output file: D:\Tools\ProjectA-tree-20260808-092915.html
Snapshot written: D:\Tools\ProjectA-tree-20260808-092915.html
Found 12 directories, 86 files, and 0 links.
```

If no output path is given, the snapshot is written to the current directory as
`<folder-name>-tree-<YYYYMMDD-HHMMSS>.html` (or `.txt` / `.json` depending on
the format). If a file with the same name already exists, a `-2`, `-3`, …
suffix is appended automatically.

### Compare two snapshots

```text
Choose action (1/2/3/4, default 1): 2
Left snapshot file: D:\Tools\ProjectA-tree-20260808-092915.html
Right snapshot file: E:\Backup\ProjectA-tree-20260809-080000.html
Output file (Enter for automatic name):
Include unchanged items? (y/N):
```

The comparison report highlights files that were **added**, **removed**, or
**changed** (size or hash mismatch) between the two snapshots.

### Verify a snapshot against a live directory

```text
Choose action (1/2/3/4, default 1): 3
Snapshot file: D:\Tools\ProjectA-tree-20260808-092915.html
Directory to verify: D:\Backup\ProjectA
Report file (Enter for automatic name):
Hash current files? (Y/n/auto):
Include unchanged items? (y/N):
```

The verification report shows which files from the snapshot are **missing**,
which files in the directory are **new**, and which files have **changed** (size
or hash mismatch) since the snapshot was taken.

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

Calculate hashes without using the cache:

```powershell
dirtree.cmd "D:\Backup\ProjectA" --hash --no-cache
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

Output a text comparison report:

```powershell
dirtree.cmd compare LEFT.html RIGHT.html --format text
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

### All options

**Snapshot:**

| Option | Description |
| --- | --- |
| `directory` | Directory to scan; prompts when omitted |
| `-o, --output FILE` | Output file path |
| `--format {html,text,json}` | Output format; inferred from extension, otherwise HTML |
| `-d, --dirs-only` | Record directories and links only; omit files |
| `--hash` | Include SHA-256 hashes and show hashing progress |
| `--no-cache` | Calculate hashes without reading or updating the cache |
| `--version` | Print the current version |

**Compare:**

| Option | Description |
| --- | --- |
| `left` | Source or earlier snapshot file |
| `right` | Backup or later snapshot file |
| `-o, --output FILE` | Report output file |
| `--format {html,text}` | Report format; inferred from `.txt`, otherwise HTML |
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
`--hash`. The parent directory of `--output` must already exist.

## Snapshot formats

### HTML (default)

A standalone, interactive HTML page with:

- Searchable file/folder tree (type to filter, Esc to clear).
- Expand-all / collapse-all buttons.
- File sizes and optional SHA-256 hashes displayed inline.
- Summary metrics (directories, files, links, errors) in the header.
- Snapshot timestamp in the header.
- Print-friendly layout (all nodes auto-expand when printing).

### Text

```text
# DirTree Snapshot v2
# Created: 2026-08-08T09:29:15+08:00
# Mode: files-and-directories
# Details: size-bytes,sha256
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
entries with path, kind, size, and optional SHA-256.

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
