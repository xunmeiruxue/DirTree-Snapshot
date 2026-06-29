#!/usr/bin/env python3
"""Create deterministic directory tree snapshots."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence, TextIO

VERSION = "0.2.0"
HASH_CHUNK_SIZE = 1024 * 1024
PROGRESS_REFRESH_SECONDS = 0.1
WarningHandler = Callable[[Path, str], None]


@dataclass(frozen=True)
class SnapshotOptions:
    directories_only: bool = False
    include_hash: bool = False


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


def _file_details(
    entry: _TreeEntry,
    options: SnapshotOptions,
    stats: SnapshotStats,
    on_warning: Optional[WarningHandler],
    hash_progress: Optional[_HashProgress],
) -> str:
    size_text = str(entry.size) if entry.size is not None else "?"
    details = [f"size={size_text} B"]

    if options.include_hash:
        bytes_read = 0

        def track_chunk(byte_count: int) -> None:
            nonlocal bytes_read
            bytes_read += byte_count
            if hash_progress is not None:
                hash_progress.advance_bytes(byte_count)

        try:
            details.append(f"sha256={_sha256(entry.path, track_chunk)}")
        except OSError as exc:
            details.append("sha256=unreadable")
            _report_error(stats, on_warning, entry.path, str(exc))
        finally:
            if hash_progress is not None:
                hash_progress.finish_file(entry.size, bytes_read)

    return f" [{', '.join(details)}]"


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
            details = _file_details(
                entry,
                options,
                stats,
                on_warning,
                hash_progress,
            )
            yield f"{prefix}{connector}{name}{details}"
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
            for line in iter_snapshot_lines(
                root,
                options,
                stats,
                excluded_paths,
                on_warning,
                hash_progress,
            ):
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


def default_output_path(root: Path) -> Path:
    label = root.name or root.drive.rstrip(":\\/") or "root"
    label = _INVALID_FILENAME_CHARS.sub("_", label).strip(". ") or "root"
    return Path.cwd() / f"{label}-tree.txt"


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
        help="output text file (default: <folder>-tree.txt in the current directory)",
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

    if args.output:
        cleaned_output = _clean_path_argument(args.output)
        if not cleaned_output:
            print("Error: the output path is empty.", file=sys.stderr)
            return 2
        output = _absolute_path(Path(cleaned_output))
    else:
        output = _absolute_path(default_output_path(root))

    options = SnapshotOptions(
        directories_only=args.dirs_only,
        include_hash=args.hash,
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
