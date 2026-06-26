#!/usr/bin/env python3
"""Create deterministic directory tree snapshots."""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

VERSION = "0.1.0"
WarningHandler = Callable[[Path, str], None]


@dataclass(frozen=True)
class SnapshotOptions:
    directories_only: bool = False


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
                entries.append(_TreeEntry(path=path, name=entry.name, kind=kind))
    except OSError as exc:
        _report_error(stats, on_warning, directory, str(exc))
        return None

    kind_order = {"directory": 0, "file": 1, "link": 2, "other": 3, "unreadable": 4}
    entries.sort(key=lambda item: (kind_order[item.kind], item.name.casefold(), item.name))
    return entries


def _iter_directory(
    directory: Path,
    prefix: str,
    options: SnapshotOptions,
    stats: SnapshotStats,
    excluded_paths: set[str],
    on_warning: Optional[WarningHandler],
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
            )
        elif entry.kind == "file":
            stats.files += 1
            yield f"{prefix}{connector}{name}"
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
) -> Iterator[str]:
    """Yield snapshot lines while updating stats."""
    yield "# DirTree Snapshot v1"
    mode = "directories-only" if options.directories_only else "files-and-directories"
    yield f"# Mode: {mode}"
    yield "# Paths: relative-to-root"
    yield "."
    yield from _iter_directory(root, "", options, stats, excluded_paths, on_warning)
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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

    options = SnapshotOptions(directories_only=args.dirs_only)

    print(f"Scanning: {root}")

    def show_warning(path: Path, message: str) -> None:
        print(f"Warning: {path}: {message}", file=sys.stderr)

    try:
        stats = write_snapshot(root, output, options, show_warning)
    except (OSError, ValueError) as exc:
        print(f"Error: could not create snapshot: {exc}", file=sys.stderr)
        return 1

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
