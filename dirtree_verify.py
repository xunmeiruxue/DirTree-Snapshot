#!/usr/bin/env python3
"""Verify a live directory against a saved DirTree Snapshot."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence

import dirtree
from dirtree_cache import HashCache, HashCacheError
from dirtree_compare import (
    SnapshotData,
    compare_snapshots,
    iter_html_report,
    iter_text_report,
    load_snapshot,
    _write_report,
)

_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _has_hash(snapshot: SnapshotData) -> bool:
    return any(
        entry.kind == "file" and entry.sha256 and _HASH_PATTERN.fullmatch(entry.sha256)
        for entry in snapshot.entries
    )


def _safe_label(path: Path) -> str:
    value = path.name or path.drive.rstrip(":\\/") or "root"
    value = _INVALID_FILENAME_CHARS.sub("_", value).strip(". ")
    return value or "root"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 10000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError(f"Could not find an available verification filename for: {path.name}")


def _default_output(snapshot: Path, directory: Path, output_format: str, created_at: str) -> Path:
    extension = "html" if output_format == "html" else "txt"
    timestamp = dirtree._filename_timestamp(created_at)
    filename = f"{snapshot.stem}-verify-{_safe_label(directory)}-{timestamp}.{extension}"
    return _unique_path(Path.cwd() / filename)


def _cache_exclusions(paths: set[str], cache: Optional[HashCache]) -> None:
    if cache is None:
        return
    for suffix in ("", "-wal", "-shm", "-journal"):
        paths.add(dirtree._path_key(Path(os.fspath(cache.path) + suffix)))


def build_verify_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirtree verify",
        description="Verify a live directory against a saved DirTree Snapshot.",
    )
    parser.add_argument("snapshot", help="saved HTML, text, or JSON snapshot")
    parser.add_argument("directory", help="current directory to verify")
    parser.add_argument("-o", "--output", help="verification report output file")
    parser.add_argument(
        "--format",
        choices=("html", "text"),
        help="report format; inferred from .txt, otherwise defaults to html",
    )
    hash_group = parser.add_mutually_exclusive_group()
    hash_group.add_argument(
        "--hash",
        action="store_true",
        help="force SHA-256 hashing of the live directory",
    )
    hash_group.add_argument(
        "--no-hash",
        action="store_true",
        help="compare paths, types, and sizes without hashing live files",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="do not use or update the local hash cache",
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="include unchanged entries in the initial report",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="compare relative paths with case sensitivity",
    )
    return parser


def run_verify(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_verify_parser()
    args = parser.parse_args(argv)
    snapshot_path = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(args.snapshot.strip('"')))))
    directory = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(args.directory.strip('"')))))

    try:
        saved = load_snapshot(snapshot_path)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not directory.is_dir():
        print(f"Error: directory does not exist: {directory}", file=sys.stderr)
        return 2

    if args.hash:
        include_hash = True
        hash_reason = "forced"
    elif args.no_hash:
        include_hash = False
        hash_reason = "disabled"
    else:
        include_hash = _has_hash(saved)
        hash_reason = "from snapshot" if include_hash else "not present in snapshot"

    if args.no_cache and not include_hash:
        parser.error("--no-cache requires hash verification")

    created_at = dirtree._current_timestamp()
    output: Optional[Path] = None
    if args.output:
        output = Path(os.path.abspath(os.path.expandvars(os.path.expanduser(args.output.strip('"')))))
    output_format = args.format
    if output_format is None:
        output_format = "text" if output is not None and output.suffix.lower() == ".txt" else "html"
    if output is None:
        output = _default_output(snapshot_path, directory, output_format, created_at)

    input_key = os.path.normcase(os.path.abspath(os.fspath(snapshot_path)))
    if os.path.normcase(os.path.abspath(os.fspath(output))) == input_key:
        print("Error: verification report cannot overwrite the snapshot", file=sys.stderr)
        return 2

    cache: Optional[HashCache] = None
    progress: Optional[dirtree._HashProgress] = None
    scan_warnings: list[str] = []
    stats: Optional[dirtree.SnapshotStats] = None

    with tempfile.TemporaryDirectory(prefix="dirtree-verify-") as temporary_directory:
        temporary_snapshot = Path(temporary_directory) / "live.json"
        if include_hash and not args.no_cache:
            try:
                cache = HashCache()
            except HashCacheError as exc:
                print(f"Warning: {exc}; continuing without cache.", file=sys.stderr)

        options = dirtree.SnapshotOptions(
            include_hash=include_hash,
            output_format="json",
            created_at=created_at,
            hash_cache=cache,
        )
        excluded_paths = {
            dirtree._path_key(temporary_snapshot),
            dirtree._path_key(snapshot_path),
            dirtree._path_key(output),
        }
        _cache_exclusions(excluded_paths, cache)

        if include_hash:
            print("Counting files and bytes for verification hash progress...", file=sys.stderr)
            totals = dirtree._measure_hash_work(directory, excluded_paths)
            progress = dirtree._HashProgress(totals, sys.stderr, sys.stderr.isatty())
            progress.start()

        def warning(path: Path, message: str) -> None:
            text = f"Warning: {path}: {message}"
            scan_warnings.append(text)
            if progress is not None:
                progress.message(text)
            else:
                print(text, file=sys.stderr)

        try:
            stats = dirtree.write_snapshot(
                directory,
                temporary_snapshot,
                options,
                warning,
                progress,
                excluded_paths,
            )
        except (OSError, ValueError) as exc:
            if progress is not None:
                progress.finish(completed=False)
            print(f"Error: could not scan directory: {exc}", file=sys.stderr)
            return 3
        finally:
            if cache is not None:
                cache.close()

        if progress is not None:
            progress.finish(completed=True)
        if cache is not None:
            print(
                f"Hash cache result: {cache.stats.hits} reused, "
                f"{cache.stats.stores} updated, {cache.stats.misses} misses"
            )

        try:
            live = load_snapshot(temporary_snapshot)
        except ValueError as exc:
            print(f"Error: could not load live scan: {exc}", file=sys.stderr)
            return 3

        if args.hash and not _has_hash(saved):
            scan_warnings.append(
                "The saved snapshot has no usable SHA-256 values; live hashes cannot be matched."
            )
        live = replace(
            live,
            source_name=f"live:{_safe_label(directory)}",
            created_at=created_at,
            warnings=list(live.warnings) + scan_warnings,
        )
        saved_for_report = replace(saved, source_name=snapshot_path.name)
        result = compare_snapshots(
            saved_for_report,
            live,
            case_sensitive=args.case_sensitive,
        )

        if output_format == "html":
            report_lines = iter_html_report(
                saved_for_report,
                live,
                result,
                args.include_unchanged,
                created_at,
            )
        else:
            report_lines = iter_text_report(
                saved_for_report,
                live,
                result,
                args.include_unchanged,
                created_at,
            )
        try:
            _write_report(output, report_lines)
        except (OSError, ValueError) as exc:
            print(f"Error: could not write verification report: {exc}", file=sys.stderr)
            return 3

    print(f"Snapshot: {snapshot_path.name}")
    print(f"Directory: {directory}")
    print(f"Hash mode: {hash_reason}")
    print(f"Changed: {result.counts['changed']}")
    print(f"Added: {result.counts['added']}")
    print(f"Removed: {result.counts['removed']}")
    print(f"Same: {result.counts['same']}")
    print(f"Verification report: {output}")
    if stats is not None and stats.errors:
        print(f"Verification incomplete: {stats.errors} read error(s) occurred.", file=sys.stderr)
        return 3
    return 1 if result.differences else 0


if __name__ == "__main__":
    raise SystemExit(run_verify())
