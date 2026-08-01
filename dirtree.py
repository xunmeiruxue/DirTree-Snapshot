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
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence, TextIO

from dirtree_assets import load_text, render
from dirtree_compare import run_compare

VERSION = "0.8.0"
HASH_CHUNK_SIZE = 1024 * 1024
PROGRESS_REFRESH_SECONDS = 0.1
WarningHandler = Callable[[Path, str], None]


@dataclass(frozen=True)
class SnapshotOptions:
    directories_only: bool = False
    include_hash: bool = False
    output_format: str = "html"
    created_at: Optional[str] = None


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


def _collect_json_entries(
    directory: Path,
    path_parts: tuple[str, ...],
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler],
    hash_progress: Optional[_HashProgress],
) -> list[dict[str, object]]:
    entries = _read_entries(directory, options, stats, excluded_paths, on_warning)
    if entries is None:
        return [{"path": "/".join(path_parts + ("[unreadable]",)), "kind": "unreadable"}]

    result: list[dict[str, object]] = []
    for entry in entries:
        current_parts = path_parts + (entry.name,)
        relative_path = "/".join(current_parts)
        if entry.kind == "directory":
            stats.directories += 1
            result.append({"path": relative_path, "kind": "directory"})
            result.extend(
                _collect_json_entries(
                    entry.path,
                    current_parts,
                    options,
                    stats,
                    excluded_paths,
                    on_warning,
                    hash_progress,
                )
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
            item: dict[str, object] = {
                "path": relative_path,
                "kind": "file",
                "size": details.size,
            }
            if options.include_hash:
                item["sha256"] = details.sha256 or "unreadable"
            result.append(item)
        elif entry.kind == "link":
            stats.links += 1
            result.append({"path": relative_path, "kind": "link"})
        elif entry.kind == "other":
            result.append({"path": relative_path, "kind": "special"})
        else:
            result.append({"path": relative_path, "kind": "unreadable"})
    return result


def iter_snapshot_json(
    root: Path,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler] = None,
    hash_progress: Optional[_HashProgress] = None,
) -> Iterator[str]:
    """Yield a canonical machine-readable JSON snapshot."""
    entries = _collect_json_entries(
        root,
        (),
        options,
        stats,
        excluded_paths,
        on_warning,
        hash_progress,
    )
    mode = "directories-only" if options.directories_only else "files-and-directories"
    details = "none" if options.directories_only else "size-bytes"
    if options.include_hash:
        details += ",sha256"
    root_name = root.name or root.drive.rstrip(":\\/") or "root"
    payload = {
        "schema": "dirtree.snapshot",
        "schema_version": 1,
        "tool_version": VERSION,
        "created_at": options.created_at or _current_timestamp(),
        "root_name": root_name,
        "mode": mode,
        "details": details,
        "hash": {
            "algorithm": "SHA-256",
            "enabled": options.include_hash,
        },
        "statistics": {
            "directories": stats.directories,
            "files": stats.files,
            "links": stats.links,
            "errors": stats.errors,
        },
        "entries": entries,
    }
    yield json.dumps(payload, ensure_ascii=False, indent=2)


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
    yield f"# Created: {options.created_at or _current_timestamp()}"
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
            f'{indent}<li class="tree-item status-item" data-kind="unreadable" data-search="{search_path}">'
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
            yield f'{indent}<li class="tree-item directory-item" data-kind="directory" data-search="{search_path}">'
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
            hash_attribute = html.escape(details.sha256 or "", quote=True)
            yield (
                f'{indent}<li class="tree-item file-item" data-kind="file" '
                f'data-size="{size_value}" data-sha256="{hash_attribute}" data-search="{search_path}">'
            )
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
                f'{indent}<li class="tree-item link-item" data-kind="link" data-search="{search_path}">'
                f'<div class="node-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("link", "node-icon link-icon")}<span class="node-name">{name}'
                '<span class="node-badge">链接，未跟随</span></span><span></span></div></li>'
            )
        elif entry.kind == "other":
            yield (
                f'{indent}<li class="tree-item status-item" data-kind="special" data-search="{search_path}">'
                f'<div class="node-row"><span class="chevron-spacer"></span>'
                f'{_html_icon("alert", "node-icon alert-icon")}<span class="node-name">{name}'
                '<span class="node-badge">特殊文件</span></span><span></span></div></li>'
            )
        else:
            yield (
                f'{indent}<li class="tree-item status-item" data-kind="unreadable" data-search="{search_path}">'
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
    created_at = options.created_at or _current_timestamp()
    created_attribute = html.escape(created_at, quote=True)
    created_display = html.escape(_display_timestamp(created_at))
    if options.directories_only:
        mode_label = "仅目录"
    elif options.include_hash:
        mode_label = "目录、文件大小与 SHA-256"
    else:
        mode_label = "目录与文件大小"

    yield render(
        "snapshot_head.html",
        TITLE=f"{escaped_root} - DirTree Snapshot",
        STYLES=load_text("snapshot.css"),
        ICONS=load_text("snapshot_icons.svg"),
        ROOT_NAME=escaped_root,
        MODE=mode_label,
        CREATED_AT_ATTR=created_attribute,
        CREATED_AT_DISPLAY=created_display,
        ROOT_SEARCH=root_search,
    )
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
    yield render(
        "snapshot_footer.html",
        DIRECTORIES=str(stats.directories),
        FILES=str(stats.files),
        LINKS=str(stats.links),
        ERRORS=str(stats.errors),
        STATS_JSON=stats_json,
        MODE=mode_label,
        SCRIPT=load_text("snapshot.js"),
    )

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
    if options.created_at is None:
        options = replace(options, created_at=_current_timestamp())

    if not root.is_dir():
        raise ValueError(f"Not a directory: {root}")
    if not output.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {output.parent}")
    if options.output_format not in {"html", "text", "json"}:
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
            encoding="utf-8" if options.output_format == "json" else "utf-8-sig",
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
            elif options.output_format == "json":
                snapshot_lines = iter_snapshot_json(
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


def _current_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M:%S %z")


def _filename_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parsed = datetime.now().astimezone()
    return parsed.strftime("%Y%m%d-%H%M%S")


def _next_available_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not find an available output filename for: {path.name}")


def default_output_path(
    root: Path,
    output_format: str = "html",
    created_at: Optional[str] = None,
) -> Path:
    label = root.name or root.drive.rstrip(":\\/") or "root"
    label = _INVALID_FILENAME_CHARS.sub("_", label).strip(". ") or "root"
    extension = {"html": "html", "text": "txt", "json": "json"}.get(output_format, output_format)
    timestamp = _filename_timestamp(created_at or _current_timestamp())
    path = Path.cwd() / f"{label}-tree-{timestamp}.{extension}"
    return _next_available_path(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirtree",
        description=(
            "Create a deterministic directory tree snapshot for a single folder."
        ),
        epilog="Compare snapshots: dirtree compare LEFT RIGHT",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="directory to scan; prompts when omitted",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="output file (default: <folder>-tree-YYYYMMDD-HHMMSS.html)",
    )
    parser.add_argument(
        "--format",
        choices=("html", "text", "json"),
        help="output format; inferred from .txt/.json, otherwise defaults to html",
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


def _run_snapshot(arguments: Sequence[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(arguments)

    if args.dirs_only and args.hash:
        parser.error("--hash cannot be combined with --dirs-only")

    directory_value = args.directory
    if directory_value is None:
        print("Error: no directory was provided.", file=sys.stderr)
        return 2

    cleaned_directory = _clean_path_argument(directory_value)
    if not cleaned_directory:
        print("Error: no directory was provided.", file=sys.stderr)
        return 2

    root = _absolute_path(Path(cleaned_directory))
    if not root.is_dir():
        print(f"Error: directory does not exist: {root}", file=sys.stderr)
        return 2

    created_at = _current_timestamp()
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
        elif output is not None and output.suffix.lower() == ".json":
            output_format = "json"
        else:
            output_format = "html"

    if output is None:
        output = _absolute_path(default_output_path(root, output_format, created_at))

    options = SnapshotOptions(
        directories_only=args.dirs_only,
        include_hash=args.hash,
        output_format=output_format,
        created_at=created_at,
    )

    print(f"Scanning: {root}")
    print(f"Output format: {options.output_format}")
    print(f"SHA-256: {'enabled' if options.include_hash else 'disabled'}")
    print(f"Snapshot time: {_display_timestamp(created_at)}")
    print(f"Output file: {output}")

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


def _prompt_value(prompt: str) -> Optional[str]:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.", file=sys.stderr)
        return None


def _is_yes(value: str) -> bool:
    return value.casefold() in {"y", "yes", "1", "true"}


def _run_interactive() -> int:
    print("DirTree Snapshot")
    print()
    print("[1] Generate snapshot")
    print("[2] Compare two snapshots")
    action = _prompt_value("Choose action (1/2, default 1): ")
    if action is None:
        return 2

    if action.casefold() in {"2", "c", "compare"}:
        left_value = _prompt_value("Left snapshot file: ")
        if left_value is None:
            return 2
        right_value = _prompt_value("Right snapshot file: ")
        if right_value is None:
            return 2
        if not left_value or not right_value:
            print("Error: both snapshot files are required.", file=sys.stderr)
            return 2

        output_value = _prompt_value("Output file (Enter for automatic name): ")
        if output_value is None:
            return 2
        include_value = _prompt_value("Include unchanged items? (y/N): ")
        if include_value is None:
            return 2

        compare_arguments = [
            _clean_path_argument(left_value),
            _clean_path_argument(right_value),
        ]
        if output_value:
            compare_arguments.extend(["-o", _clean_path_argument(output_value)])
        if _is_yes(include_value):
            compare_arguments.append("--include-unchanged")

        return run_compare(compare_arguments)

    directory_value = _prompt_value("Directory to scan: ")
    if directory_value is None:
        return 2
    if not directory_value:
        print("Error: no directory was provided.", file=sys.stderr)
        return 2
    hash_value = _prompt_value("Calculate SHA-256 hashes? (y/N): ")
    if hash_value is None:
        return 2
    format_value = _prompt_value("Output format (html/text/json, default html): ")
    if format_value is None:
        return 2
    format_value = format_value.casefold() or "html"
    if format_value not in {"html", "text", "json"}:
        print("Error: output format must be html, text, or json.", file=sys.stderr)
        return 2

    snapshot_arguments = [_clean_path_argument(directory_value)]
    if _is_yes(hash_value):
        snapshot_arguments.append("--hash")
    if format_value != "html":
        snapshot_arguments.extend(["--format", format_value])
    return _run_snapshot(snapshot_arguments)


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        return _run_interactive()
    if arguments[0].casefold() == "compare":
        return run_compare(arguments[1:])
    return _run_snapshot(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
