#!/usr/bin/env python3
"""Create deterministic directory tree snapshots."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence, TextIO

VERSION = "0.3.0"
HASH_CHUNK_SIZE = 1024 * 1024
PROGRESS_REFRESH_SECONDS = 0.1
WarningHandler = Callable[[Path, str], None]


@dataclass(frozen=True)
class SnapshotOptions:
    directories_only: bool = False
    include_hash: bool = False
    output_format: str = "html"


@dataclass
class SnapshotStats:
    directories: int = 0
    files: int = 0
    links: int = 0
    errors: int = 0


@dataclass(frozen=True)
class _TreeEntry:
    path: Path
    name: str
    kind: str
    size: Optional[int] = None


@dataclass(frozen=True)
class _HashTotals:
    files: int = 0
    bytes: int = 0


@dataclass(frozen=True)
class _FileDetails:
    size: Optional[int]
    sha256: Optional[str] = None


class _HashProgress:
    def __init__(self, totals: _HashTotals, stream: TextIO, enabled: bool) -> None:
        self.totals = totals
        self.stream = stream
        self.enabled = enabled
        self.processed_files = 0
        self.processed_bytes = 0
        self._finished = False
        self._started = False
        self._last_render = 0.0
        self._line_width = 0

    def start(self) -> None:
        self._started = True
        if self.enabled:
            self._render(force=True)
        else:
            print(
                f"Hashing {self.totals.files} files ({self.totals.bytes} B)...",
                file=self.stream,
            )

    def advance_bytes(self, byte_count: int) -> None:
        self.processed_bytes += byte_count
        self._render()

    def finish_file(self, expected_size: Optional[int], bytes_read: int) -> None:
        if expected_size is not None and expected_size > bytes_read:
            self.processed_bytes += expected_size - bytes_read
        self.processed_files += 1
        self._render()

    def message(self, text: str) -> None:
        if self.enabled and self._started:
            self.stream.write("\r" + (" " * self._line_width) + "\r")
        self.stream.write(text + "\n")
        self.stream.flush()
        if self.enabled and self._started:
            self._line_width = 0
            self._render(force=True)

    def finish(self, completed: bool) -> None:
        if not self._started:
            return

        if completed:
            self._finished = True
            self.processed_files = max(self.processed_files, self.totals.files)
            self.processed_bytes = max(self.processed_bytes, self.totals.bytes)

        if self.enabled:
            self._render(force=True)
            self.stream.write("\n")
            self.stream.flush()
        else:
            print("Hashing complete." if completed else "Hashing stopped.", file=self.stream)
        self._started = False

    def _ratio(self) -> float:
        if self._finished:
            return 1.0
        if self.totals.bytes > 0:
            return min(self.processed_bytes / self.totals.bytes, 1.0)
        if self.totals.files > 0:
            return min(self.processed_files / self.totals.files, 1.0)
        return 1.0

    def _render(self, force: bool = False) -> None:
        if not self.enabled or not self._started:
            return

        now = time.monotonic()
        if not force and now - self._last_render < PROGRESS_REFRESH_SECONDS:
            return
        self._last_render = now

        ratio = self._ratio()
        bar_width = 24
        filled = min(int(ratio * bar_width), bar_width)
        bar = ("#" * filled) + ("-" * (bar_width - filled))
        shown_bytes = self.processed_bytes
        if self.totals.bytes > 0:
            shown_bytes = min(shown_bytes, self.totals.bytes)
        line = (
            f"Hashing [{bar}] {ratio * 100:6.2f}% "
            f"{self.processed_files}/{self.totals.files} files "
            f"{shown_bytes}/{self.totals.bytes} B"
        )
        padded_line = line.ljust(self._line_width)
        self._line_width = max(self._line_width, len(line))
        self.stream.write("\r" + padded_line)
        self.stream.flush()


_LAST_BRANCH = chr(96) + "-- "


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _display_name(name: str) -> str:
    return name.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")


def _is_link_or_reparse_point(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True

    if os.name != "nt":
        return False

    attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _classify_entry(entry: os.DirEntry[str]) -> str:
    if _is_link_or_reparse_point(entry):
        return "link"
    if entry.is_dir(follow_symlinks=False):
        return "directory"
    if entry.is_file(follow_symlinks=False):
        return "file"
    return "other"


def _report_error(
    stats: SnapshotStats,
    on_warning: Optional[WarningHandler],
    path: Path,
    message: str,
) -> None:
    stats.errors += 1
    if on_warning is not None:
        on_warning(path, message)


def _read_entries(
    directory: Path,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler],
) -> Optional[list[_TreeEntry]]:
    entries: list[_TreeEntry] = []

    try:
        with os.scandir(directory) as scanned_entries:
            for entry in scanned_entries:
                path = Path(entry.path)
                if _path_key(path) in excluded_paths:
                    continue

                try:
                    kind = _classify_entry(entry)
                except OSError as exc:
                    kind = "unreadable"
                    _report_error(stats, on_warning, path, str(exc))

                if options.directories_only and kind == "file":
                    continue

                size: Optional[int] = None
                if kind == "file":
                    try:
                        size = entry.stat(follow_symlinks=False).st_size
                    except OSError as exc:
                        _report_error(stats, on_warning, path, str(exc))

                entries.append(
                    _TreeEntry(path=path, name=entry.name, kind=kind, size=size)
                )
    except OSError as exc:
        _report_error(stats, on_warning, directory, str(exc))
        return None

    kind_order = {"directory": 0, "file": 1, "link": 2, "other": 3, "unreadable": 4}
    entries.sort(key=lambda item: (kind_order[item.kind], item.name.casefold(), item.name))
    return entries


def _measure_hash_work(root: Path, excluded_paths: set[str]) -> _HashTotals:
    total_files = 0
    total_bytes = 0
    directories = [root]

    while directories:
        directory = directories.pop()
        try:
            with os.scandir(directory) as scanned_entries:
                for entry in scanned_entries:
                    path = Path(entry.path)
                    if _path_key(path) in excluded_paths:
                        continue
                    try:
                        kind = _classify_entry(entry)
                    except OSError:
                        continue

                    if kind == "directory":
                        directories.append(path)
                    elif kind == "file":
                        total_files += 1
                        try:
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                        except OSError:
                            pass
        except OSError:
            continue

    return _HashTotals(files=total_files, bytes=total_bytes)


def _sha256(path: Path, on_chunk: Optional[Callable[[int], None]] = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        while True:
            chunk = file_handle.read(HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
            if on_chunk is not None:
                on_chunk(len(chunk))
    return digest.hexdigest()


def _collect_file_details(
    entry: _TreeEntry,
    options: SnapshotOptions,
    stats: SnapshotStats,
    on_warning: Optional[WarningHandler],
    hash_progress: Optional[_HashProgress],
) -> _FileDetails:
    sha256_value: Optional[str] = None

    if options.include_hash:
        bytes_read = 0

        def track_chunk(byte_count: int) -> None:
            nonlocal bytes_read
            bytes_read += byte_count
            if hash_progress is not None:
                hash_progress.advance_bytes(byte_count)

        try:
            sha256_value = _sha256(entry.path, track_chunk)
        except OSError as exc:
            sha256_value = "unreadable"
            _report_error(stats, on_warning, entry.path, str(exc))
        finally:
            if hash_progress is not None:
                hash_progress.finish_file(entry.size, bytes_read)

    return _FileDetails(size=entry.size, sha256=sha256_value)


def _format_text_file_details(details: _FileDetails, include_hash: bool) -> str:
    size_text = str(details.size) if details.size is not None else "?"
    values = [f"size={size_text} B"]
    if include_hash:
        values.append(f"sha256={details.sha256 or 'unreadable'}")
    return f" [{', '.join(values)}]"


def _iter_directory(
    directory: Path,
    prefix: str,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler],
    hash_progress: Optional[_HashProgress],
) -> Iterator[str]:
    entries = _read_entries(directory, options, stats, excluded_paths, on_warning)
    if entries is None:
        yield f"{prefix}{_LAST_BRANCH}[unreadable]"
        return

    for index, entry in enumerate(entries):
        is_last = index == len(entries) - 1
        connector = _LAST_BRANCH if is_last else "|-- "
        child_prefix = prefix + ("    " if is_last else "|   ")
        name = _display_name(entry.name)

        if entry.kind == "directory":
            stats.directories += 1
            yield f"{prefix}{connector}{name}/"
            yield from _iter_directory(
                entry.path,
                child_prefix,
                options,
                stats,
                excluded_paths,
                on_warning,
                hash_progress,
            )
        elif entry.kind == "file":
            stats.files += 1
            details = _collect_file_details(
                entry,
                options,
                stats,
                on_warning,
                hash_progress,
            )
            detail_text = _format_text_file_details(details, options.include_hash)
            yield f"{prefix}{connector}{name}{detail_text}"
        elif entry.kind == "link":
            stats.links += 1
            yield f"{prefix}{connector}{name} [link-not-followed]"
        elif entry.kind == "other":
            yield f"{prefix}{connector}{name} [special-file]"
        else:
            yield f"{prefix}{connector}{name} [unreadable]"


def iter_snapshot_lines(
    root: Path,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler] = None,
    hash_progress: Optional[_HashProgress] = None,
) -> Iterator[str]:
    """Yield snapshot lines while updating stats."""
    yield "# DirTree Snapshot v2"
    mode = "directories-only" if options.directories_only else "files-and-directories"
    details = "none" if options.directories_only else "size-bytes"
    if options.include_hash:
        details += ",sha256"
    yield f"# Mode: {mode}"
    yield f"# Details: {details}"
    yield "# Paths: relative-to-root"
    yield "."
    yield from _iter_directory(
        root,
        "",
        options,
        stats,
        excluded_paths,
        on_warning,
        hash_progress,
    )
    yield (
        "# Summary: "
        f"directories={stats.directories} files={stats.files} "
        f"links={stats.links} errors={stats.errors}"
    )


_HTML_STYLE = """
:root {
  color-scheme: light;
  --page: #f4f6f7;
  --surface: #ffffff;
  --surface-muted: #f8fafb;
  --text: #182329;
  --muted: #66757d;
  --line: #d8e0e4;
  --line-strong: #bcc8ce;
  --accent: #1769aa;
  --accent-soft: #e8f2f9;
  --folder: #b56f08;
  --file: #387483;
  --link: #7255a5;
  --success: #23734d;
  --danger: #b53a3a;
  --shadow: 0 12px 30px rgba(24, 35, 41, 0.08);
}

* {
  box-sizing: border-box;
}

html {
  background: var(--page);
}

body {
  margin: 0;
  min-width: 320px;
  background: var(--page);
  color: var(--text);
  font-family: Inter, "Segoe UI", "Microsoft YaHei UI", Arial, sans-serif;
  font-size: 14px;
  line-height: 1.5;
  letter-spacing: 0;
}

button,
input {
  font: inherit;
  letter-spacing: 0;
}

.icon-sprite {
  position: absolute;
  width: 0;
  height: 0;
  overflow: hidden;
}

.page-header {
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.header-inner,
.page-main,
.page-footer {
  width: min(1180px, calc(100% - 40px));
  margin: 0 auto;
}

.header-inner {
  display: flex;
  min-height: 132px;
  align-items: center;
  justify-content: space-between;
  gap: 36px;
  padding: 24px 0;
}

.product-name {
  margin: 0 0 6px;
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
}

h1 {
  max-width: 720px;
  margin: 0;
  overflow-wrap: anywhere;
  font-size: 28px;
  font-weight: 680;
  line-height: 1.2;
  letter-spacing: 0;
}

.snapshot-mode {
  margin: 8px 0 0;
  color: var(--muted);
  font-size: 13px;
}

.metrics {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: minmax(76px, auto);
  gap: 0;
  margin: 0;
}

.metric {
  min-width: 76px;
  padding: 4px 18px;
  border-left: 1px solid var(--line);
}

.metric dt {
  color: var(--muted);
  font-size: 12px;
}

.metric dd {
  margin: 2px 0 0;
  font-variant-numeric: tabular-nums;
  font-size: 20px;
  font-weight: 700;
}

.metric.metric-errors dd {
  color: var(--danger);
}

.page-main {
  padding: 28px 0 36px;
}

.tree-panel {
  overflow: hidden;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface);
  box-shadow: var(--shadow);
}

.toolbar {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--line);
  background: var(--surface-muted);
}

.search-field {
  position: relative;
  flex: 1 1 360px;
  min-width: 180px;
}

.search-icon {
  position: absolute;
  top: 50%;
  left: 12px;
  width: 17px;
  height: 17px;
  transform: translateY(-50%);
  color: var(--muted);
  pointer-events: none;
}

.search-field input {
  width: 100%;
  height: 38px;
  padding: 0 36px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  outline: none;
  background: var(--surface);
  color: var(--text);
}

.search-field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}

.toolbar-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 8px;
}

.toolbar button {
  height: 36px;
  padding: 0 13px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
}

.toolbar button:hover {
  border-color: var(--accent);
  color: var(--accent);
}

.toolbar button:focus-visible {
  outline: 3px solid var(--accent-soft);
  outline-offset: 1px;
}

.match-count {
  min-width: 52px;
  color: var(--muted);
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
}

.tree-scroll {
  min-height: 260px;
  max-height: calc(100vh - 260px);
  overflow: auto;
  padding: 14px 16px 22px;
}

.tree,
.tree ul {
  margin: 0;
  padding: 0;
  list-style: none;
}

.tree-children {
  margin-left: 15px !important;
  padding-left: 20px !important;
  border-left: 1px solid var(--line);
}

.tree-item {
  min-width: 0;
}

.node-row {
  display: grid;
  grid-template-columns: 16px 20px minmax(0, 1fr) auto;
  min-height: 34px;
  align-items: center;
  gap: 7px;
  padding: 4px 8px;
  border-radius: 5px;
}

.node-row:hover {
  background: var(--surface-muted);
}

summary.node-row {
  cursor: pointer;
  list-style: none;
}

summary.node-row::-webkit-details-marker {
  display: none;
}

.chevron,
.chevron-spacer {
  width: 16px;
  height: 16px;
}

.chevron {
  color: var(--muted);
  transition: transform 120ms ease;
}

details[open] > summary .chevron {
  transform: rotate(90deg);
}

.node-icon {
  width: 18px;
  height: 18px;
}

.folder-icon {
  color: var(--folder);
}

.file-icon {
  color: var(--file);
}

.link-icon {
  color: var(--link);
}

.alert-icon {
  color: var(--danger);
}

.node-name {
  min-width: 0;
  overflow-wrap: anywhere;
}

.root-name {
  font-weight: 700;
}

.node-size {
  padding-left: 18px;
  color: var(--muted);
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.node-meta {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 9px;
  margin: 0 8px 6px 51px;
  padding: 5px 8px;
  border-left: 2px solid #8cb99f;
  background: #f3f9f5;
  color: var(--success);
  font-size: 11px;
}

.node-meta-label {
  font-weight: 700;
}

.hash-value {
  min-width: 0;
  overflow-wrap: anywhere;
  color: #285944;
  font-family: "Cascadia Mono", Consolas, monospace;
}

.hash-value.unreadable {
  color: var(--danger);
}

.node-badge {
  margin-left: 8px;
  padding: 1px 6px;
  border: 1px solid var(--line);
  border-radius: 4px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
}

.status-row {
  color: var(--danger);
}

[hidden] {
  display: none !important;
}

.page-footer {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 0 0 26px;
  color: var(--muted);
  font-size: 12px;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

@media (max-width: 760px) {
  .header-inner,
  .page-main,
  .page-footer {
    width: min(100% - 24px, 1180px);
  }

  .header-inner {
    min-height: 0;
    align-items: flex-start;
    flex-direction: column;
    gap: 20px;
  }

  h1 {
    font-size: 24px;
  }

  .metrics {
    width: 100%;
    grid-auto-flow: row;
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }

  .metric {
    min-width: 0;
    padding: 4px 10px;
  }

  .toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .search-field {
    flex-basis: auto;
  }

  .toolbar-actions {
    flex-wrap: wrap;
  }

  .match-count {
    margin-left: auto;
  }

  .tree-scroll {
    max-height: none;
    padding: 10px 8px 18px;
  }

  .tree-children {
    margin-left: 8px !important;
    padding-left: 11px !important;
  }

  .node-row {
    grid-template-columns: 16px 20px minmax(0, 1fr);
  }

  .node-size {
    grid-column: 3;
    padding-left: 0;
    white-space: normal;
  }

  .node-meta {
    grid-template-columns: 1fr;
    margin-left: 43px;
  }
}

@media print {
  :root {
    --page: #ffffff;
    --shadow: none;
  }

  .toolbar {
    display: none;
  }

  .tree-panel {
    border-color: #b8b8b8;
    box-shadow: none;
  }

  .tree-scroll {
    max-height: none;
    overflow: visible;
  }

  details > .tree-children {
    display: block !important;
  }

  .page-footer {
    padding-top: 12px;
  }
}
"""


_HTML_ICON_SPRITE = """
<svg class="icon-sprite" aria-hidden="true">
  <symbol id="icon-folder" viewBox="0 0 24 24">
    <path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"></path>
  </symbol>
  <symbol id="icon-file" viewBox="0 0 24 24">
    <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"></path>
    <polyline points="14 2 14 8 20 8"></polyline>
  </symbol>
  <symbol id="icon-link" viewBox="0 0 24 24">
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"></path>
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"></path>
  </symbol>
  <symbol id="icon-alert" viewBox="0 0 24 24">
    <circle cx="12" cy="12" r="10"></circle>
    <line x1="12" x2="12" y1="8" y2="12"></line>
    <line x1="12" x2="12.01" y1="16" y2="16"></line>
  </symbol>
  <symbol id="icon-search" viewBox="0 0 24 24">
    <circle cx="11" cy="11" r="8"></circle>
    <path d="m21 21-4.3-4.3"></path>
  </symbol>
  <symbol id="icon-chevron-right" viewBox="0 0 24 24">
    <path d="m9 18 6-6-6-6"></path>
  </symbol>
</svg>
"""


_HTML_SCRIPT = """
<script>
(function () {
  "use strict";

  var statsNode = document.getElementById("snapshot-stats");
  var stats = JSON.parse(statsNode.textContent);
  Object.keys(stats).forEach(function (key) {
    var target = document.querySelector('[data-stat="' + key + '"]');
    if (target) {
      target.textContent = String(stats[key]);
    }
  });

  var input = document.getElementById("tree-search");
  var matchCount = document.getElementById("match-count");
  var items = Array.prototype.slice.call(document.querySelectorAll(".tree-item"));
  var rootDetails = document.querySelector(".root-item > details");

  function reveal(item) {
    item.hidden = false;
    var parentDetails = item.parentElement.closest("details");
    while (parentDetails) {
      parentDetails.open = true;
      var parentItem = parentDetails.closest(".tree-item");
      if (!parentItem) {
        break;
      }
      parentItem.hidden = false;
      parentDetails = parentItem.parentElement.closest("details");
    }
  }

  function filterTree() {
    var query = input.value.trim().toLocaleLowerCase();
    if (!query) {
      items.forEach(function (item) {
        item.hidden = false;
      });
      matchCount.textContent = "";
      return;
    }

    items.forEach(function (item) {
      item.hidden = true;
    });
    var matches = items.filter(function (item) {
      return (item.getAttribute("data-search") || "").toLocaleLowerCase().indexOf(query) !== -1;
    });
    matches.forEach(reveal);
    matchCount.textContent = matches.length + " 项";
  }

  input.addEventListener("input", filterTree);
  input.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      input.value = "";
      filterTree();
      input.blur();
    }
  });

  document.getElementById("expand-all").addEventListener("click", function () {
    document.querySelectorAll("details").forEach(function (details) {
      details.open = true;
    });
  });

  document.getElementById("collapse-all").addEventListener("click", function () {
    document.querySelectorAll("details").forEach(function (details) {
      details.open = false;
    });
    if (rootDetails) {
      rootDetails.open = true;
    }
  });

  var printState = [];
  window.addEventListener("beforeprint", function () {
    printState = Array.prototype.map.call(document.querySelectorAll("details"), function (details) {
      var wasOpen = details.open;
      details.open = true;
      return wasOpen;
    });
  });
  window.addEventListener("afterprint", function () {
    document.querySelectorAll("details").forEach(function (details, index) {
      details.open = printState[index];
    });
  });
}());
</script>
"""


def _html_icon(icon_name: str, class_name: str) -> str:
    return (
        f'<svg class="{class_name}" aria-hidden="true" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round"><use href="#icon-{icon_name}"></use></svg>'
    )


def _html_search_path(path_parts: tuple[str, ...]) -> str:
    return html.escape("/".join(path_parts), quote=True)


def _html_display_name(name: str) -> str:
    return html.escape(_display_name(name))


def _iter_html_directory(
    directory: Path,
    path_parts: tuple[str, ...],
    depth: int,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler],
    hash_progress: Optional[_HashProgress],
) -> Iterator[str]:
    entries = _read_entries(directory, options, stats, excluded_paths, on_warning)
    indent = "  " * depth
    if entries is None:
        search_path = _html_search_path(path_parts + ("unreadable",))
        yield (
            f'{indent}<li class="tree-item status-item" data-search="{search_path}">'
            f'<div class="node-row status-row"><span class="chevron-spacer"></span>'
            f'{_html_icon("alert", "node-icon alert-icon")}<span class="node-name">无法读取</span>'
            '<span class="node-size">error</span></div></li>'
        )
        return

    for entry in entries:
        current_parts = path_parts + (entry.name,)
        search_path = _html_search_path(current_parts)
        name = _html_display_name(entry.name)

        if entry.kind == "directory":
            stats.directories += 1
            open_attribute = " open" if depth <= 1 else ""
            yield f'{indent}<li class="tree-item directory-item" data-search="{search_path}">'
            yield f'{indent}  <details{open_attribute}>'
            yield (
                f'{indent}    <summary class="node-row">'
                f'{_html_icon("chevron-right", "chevron")}'
                f'{_html_icon("folder", "node-icon folder-icon")}'
                f'<span class="node-name">{name}</span><span></span></summary>'
            )
            yield f'{indent}    <ul class="tree-children">'
            yield from _iter_html_directory(
                entry.path,
                current_parts,
                depth + 1,
                options,
                stats,
                excluded_paths,
                on_warning,
                hash_progress,
            )
            yield f'{indent}    </ul>'
            yield f'{indent}  </details>'
            yield f'{indent}</li>'
        elif entry.kind == "file":
            stats.files += 1
            details = _collect_file_details(
                entry,
                options,
                stats,
                on_warning,
                hash_progress,
            )
            size_label = f"{details.size:,} B" if details.size is not None else "未知"
            size_value = str(details.size) if details.size is not None else ""
            yield f'{indent}<li class="tree-item file-item" data-search="{search_path}">'
            yield (
                f'{indent}  <div class="node-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("file", "node-icon file-icon")}'
                f'<span class="node-name">{name}</span>'
                f'<span class="node-size" data-bytes="{size_value}">{size_label}</span></div>'
            )
            if options.include_hash:
                hash_value = details.sha256 or "unreadable"
                hash_class = " unreadable" if hash_value == "unreadable" else ""
                yield (
                    f'{indent}  <div class="node-meta"><span class="node-meta-label">SHA-256</span>'
                    f'<code class="hash-value{hash_class}">{html.escape(hash_value)}</code></div>'
                )
            yield f'{indent}</li>'
        elif entry.kind == "link":
            stats.links += 1
            yield (
                f'{indent}<li class="tree-item link-item" data-search="{search_path}">'
                f'<div class="node-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("link", "node-icon link-icon")}<span class="node-name">{name}'
                '<span class="node-badge">链接，未跟随</span></span><span></span></div></li>'
            )
        elif entry.kind == "other":
            yield (
                f'{indent}<li class="tree-item status-item" data-search="{search_path}">'
                f'<div class="node-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("alert", "node-icon alert-icon")}<span class="node-name">{name}'
                '<span class="node-badge">特殊文件</span></span><span></span></div></li>'
            )
        else:
            yield (
                f'{indent}<li class="tree-item status-item" data-search="{search_path}">'
                f'<div class="node-row status-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("alert", "node-icon alert-icon")}<span class="node-name">{name}'
                '<span class="node-badge">无法读取</span></span><span></span></div></li>'
            )


def iter_snapshot_html(
    root: Path,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler] = None,
    hash_progress: Optional[_HashProgress] = None,
) -> Iterator[str]:
    """Yield a standalone, interactive HTML snapshot."""
    root_label = root.name or root.drive.rstrip(":\\/") or "root"
    escaped_root = _html_display_name(root_label)
    root_search = _html_search_path((root_label,))
    if options.directories_only:
        mode_label = "仅目录"
    elif options.include_hash:
        mode_label = "目录、文件大小与 SHA-256"
    else:
        mode_label = "目录与文件大小"

    yield "<!doctype html>"
    yield '<html lang="zh-CN">'
    yield "<head>"
    yield '  <meta charset="utf-8">'
    yield '  <meta name="viewport" content="width=device-width, initial-scale=1">'
    yield f"  <title>{escaped_root} - DirTree Snapshot</title>"
    yield "  <style>"
    yield _HTML_STYLE
    yield "  </style>"
    yield "</head>"
    yield "<body>"
    yield _HTML_ICON_SPRITE
    yield '<header class="page-header">'
    yield '  <div class="header-inner">'
    yield '    <div class="title-group">'
    yield '      <p class="product-name">DirTree Snapshot</p>'
    yield f"      <h1>{escaped_root}</h1>"
    yield f'      <p class="snapshot-mode">{mode_label}</p>'
    yield "    </div>"
    yield '    <dl class="metrics" aria-label="清单统计">'
    yield '      <div class="metric"><dt>目录</dt><dd data-stat="directories">0</dd></div>'
    yield '      <div class="metric"><dt>文件</dt><dd data-stat="files">0</dd></div>'
    yield '      <div class="metric"><dt>链接</dt><dd data-stat="links">0</dd></div>'
    yield '      <div class="metric metric-errors"><dt>错误</dt><dd data-stat="errors">0</dd></div>'
    yield "    </dl>"
    yield "  </div>"
    yield "</header>"
    yield '<main class="page-main">'
    yield '  <section class="tree-panel" aria-label="目录结构">'
    yield '    <div class="toolbar">'
    yield '      <label class="search-field">'
    yield '        <span class="sr-only">搜索文件或目录</span>'
    yield _html_icon("search", "search-icon")
    yield '        <input id="tree-search" type="search" autocomplete="off" placeholder="搜索文件或目录">'
    yield "      </label>"
    yield '      <div class="toolbar-actions">'
    yield '        <button id="expand-all" type="button">全部展开</button>'
    yield '        <button id="collapse-all" type="button">全部折叠</button>'
    yield '        <output id="match-count" class="match-count" aria-live="polite"></output>'
    yield "      </div>"
    yield "    </div>"
    yield '    <div class="tree-scroll">'
    yield '      <ul class="tree tree-root">'
    yield f'        <li class="tree-item directory-item root-item" data-search="{root_search}">'
    yield "          <details open>"
    yield '            <summary class="node-row">'
    yield _html_icon("chevron-right", "chevron")
    yield _html_icon("folder", "node-icon folder-icon")
    yield f'              <span class="node-name root-name">{escaped_root}<span class="node-badge">根目录</span></span><span></span>'
    yield "            </summary>"
    yield '            <ul class="tree-children">'
    yield from _iter_html_directory(
        root,
        (),
        1,
        options,
        stats,
        excluded_paths,
        on_warning,
        hash_progress,
    )
    yield "            </ul>"
    yield "          </details>"
    yield "        </li>"
    yield "      </ul>"
    yield "    </div>"
    yield "  </section>"
    yield (
        "  <noscript><p>"
        f"目录 {stats.directories}，文件 {stats.files}，链接 {stats.links}，错误 {stats.errors}。"
        "</p></noscript>"
    )
    yield "</main>"
    stats_json = json.dumps(
        {
            "directories": stats.directories,
            "files": stats.files,
            "links": stats.links,
            "errors": stats.errors,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    yield f'<script id="snapshot-stats" type="application/json">{stats_json}</script>'
    yield '<footer class="page-footer">'
    yield '  <span>Snapshot format v2</span>'
    yield f"  <span>{mode_label}</span>"
    yield "</footer>"
    yield _HTML_SCRIPT
    yield "</body>"
    yield "</html>"


def write_snapshot(
    root: Path,
    output: Path,
    options: SnapshotOptions,
    on_warning: Optional[WarningHandler] = None,
    hash_progress: Optional[_HashProgress] = None,
) -> SnapshotStats:
    """Write a snapshot atomically and return its scan statistics."""
    root = _absolute_path(root)
    output = _absolute_path(output)

    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    if options.output_format not in {"html", "text"}:
        raise ValueError(f"Unsupported output format: {options.output_format}")

    stats = SnapshotStats()
    excluded_paths = {_path_key(output)}
    file_descriptor = -1
    temporary_path: Optional[Path] = None

    try:
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".dirtree-",
            suffix=".tmp",
            dir=output.parent,
        )
        temporary_path = Path(temporary_name)
        excluded_paths.add(_path_key(temporary_path))

        with os.fdopen(
            file_descriptor,
            "w",
            encoding="utf-8-sig",
            newline="\n",
        ) as output_file:
            file_descriptor = -1
            if options.output_format == "html":
                snapshot_lines = iter_snapshot_html(
                    root,
                    options,
                    stats,
                    excluded_paths,
                    on_warning,
                    hash_progress,
                )
            else:
                snapshot_lines = iter_snapshot_lines(
                    root,
                    options,
                    stats,
                    excluded_paths,
                    on_warning,
                    hash_progress,
                )
            for line in snapshot_lines:
                output_file.write(line)
                output_file.write("\n")

        os.replace(temporary_path, output)
        temporary_path = None
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass

    return stats


def _clean_path_argument(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        value = value[1:-1].strip()
    return os.path.expandvars(os.path.expanduser(value))


_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def default_output_path(root: Path, output_format: str = "html") -> Path:
    label = root.name or root.drive.rstrip(":\\/") or "root"
    label = _INVALID_FILENAME_CHARS.sub("_", label).strip(". ") or "root"
    extension = "html" if output_format == "html" else "txt"
    return Path.cwd() / f"{label}-tree.{extension}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirtree",
        description=(
            "Create a deterministic directory tree snapshot for a single folder."
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="directory to scan; prompts when omitted",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output file (default: <folder>-tree.html in the current directory)",
    )
    parser.add_argument(
        "--format",
        choices=("html", "text"),
        help="output format; inferred from .txt, otherwise defaults to html",
    )
    parser.add_argument(
        "-d",
        "--dirs-only",
        action="store_true",
        help="include directories and links, but omit regular files",
    )
    parser.add_argument(
        "--hash",
        action="store_true",
        help="include SHA-256 hashes and show hashing progress",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dirs_only and args.hash:
        parser.error("--hash cannot be combined with --dirs-only")

    directory_value = args.directory
    if directory_value is None:
        print("DirTree Snapshot")
        try:
            directory_value = input("Directory to scan: ")
        except (EOFError, KeyboardInterrupt):
            print("\nError: no directory was provided.", file=sys.stderr)
            return 2

    cleaned_directory = _clean_path_argument(directory_value)
    if not cleaned_directory:
        print("Error: no directory was provided.", file=sys.stderr)
        return 2

    root = _absolute_path(Path(cleaned_directory))
    if not root.is_dir():
        print(f"Error: directory does not exist: {root}", file=sys.stderr)
        return 2

    output: Optional[Path] = None
    if args.output:
        cleaned_output = _clean_path_argument(args.output)
        if not cleaned_output:
            print("Error: the output path is empty.", file=sys.stderr)
            return 2
        output = _absolute_path(Path(cleaned_output))

    output_format = args.format
    if output_format is None:
        if output is not None and output.suffix.lower() == ".txt":
            output_format = "text"
        else:
            output_format = "html"

    if output is None:
        output = _absolute_path(default_output_path(root, output_format))

    options = SnapshotOptions(
        directories_only=args.dirs_only,
        include_hash=args.hash,
        output_format=output_format,
    )

    print(f"Scanning: {root}")

    hash_progress: Optional[_HashProgress] = None
    if options.include_hash:
        print("Counting files and bytes for hash progress...", file=sys.stderr)
        totals = _measure_hash_work(root, {_path_key(output)})
        hash_progress = _HashProgress(
            totals=totals,
            stream=sys.stderr,
            enabled=sys.stderr.isatty(),
        )
        hash_progress.start()

    def show_warning(path: Path, message: str) -> None:
        warning = f"Warning: {path}: {message}"
        if hash_progress is not None:
            hash_progress.message(warning)
        else:
            print(warning, file=sys.stderr)

    try:
        stats = write_snapshot(
            root,
            output,
            options,
            show_warning,
            hash_progress,
        )
    except (OSError, ValueError) as exc:
        if hash_progress is not None:
            hash_progress.finish(completed=False)
        print(f"Error: could not create snapshot: {exc}", file=sys.stderr)
        return 1

    if hash_progress is not None:
        hash_progress.finish(completed=True)

    print(f"Snapshot written: {output}")
    print(
        f"Found {stats.directories} directories, {stats.files} files, "
        f"and {stats.links} links."
    )
    if stats.errors:
        print(
            f"Snapshot is incomplete: {stats.errors} read error(s) occurred.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
