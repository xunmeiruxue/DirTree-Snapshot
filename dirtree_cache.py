#!/usr/bin/env python3
"""Persistent, conservative SHA-256 cache for DirTree Snapshot."""

from __future__ import annotations

import argparse
import os
import platform
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

CACHE_SCHEMA_VERSION = 1
HASH_ALGORITHM = "sha256"
_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_INTERVAL = 128


class HashCacheError(RuntimeError):
    """Raised when a cache database cannot be opened."""


@dataclass(frozen=True)
class CacheProbe:
    scope: str
    path_key: str
    file_identity: str
    size: int
    mtime_ns: int
    ctime_ns: int
    algorithm: str = HASH_ALGORITHM


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stores: int = 0


@dataclass(frozen=True)
class CacheInfo:
    path: Path
    exists: bool
    entries: int = 0
    file_size: int = 0
    oldest_seen_ns: Optional[int] = None
    newest_seen_ns: Optional[int] = None


def default_cache_path() -> Path:
    override = os.environ.get("DIRTREE_CACHE_FILE")
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override)))
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "DirTree" / "hash-cache-v1.sqlite3"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "dirtree" / "hash-cache-v1.sqlite3"


def _path_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _probe_file(path: Path, algorithm: str = HASH_ALGORITHM) -> Optional[CacheProbe]:
    try:
        stat_result = os.stat(os.fspath(path), follow_symlinks=False)
    except OSError:
        return None
    path_value = _path_key(path)
    machine = (platform.node() or "unknown-machine").casefold()
    device = int(getattr(stat_result, "st_dev", 0))
    inode = int(getattr(stat_result, "st_ino", 0))
    identity = f"inode:{inode}" if inode else f"path:{path_value}"
    return CacheProbe(
        scope=f"{machine}|device:{device}",
        path_key=path_value,
        file_identity=identity,
        size=int(stat_result.st_size),
        mtime_ns=int(stat_result.st_mtime_ns),
        ctime_ns=int(stat_result.st_ctime_ns),
        algorithm=algorithm,
    )


class HashCache:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or default_cache_path())
        self.stats = CacheStats()
        self.disabled = False
        self.error_message: Optional[str] = None
        self._pending = 0
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, timeout=10)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=NORMAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hash_entries (
                    scope TEXT NOT NULL,
                    path_key TEXT NOT NULL,
                    algorithm TEXT NOT NULL,
                    file_identity TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    mtime_ns INTEGER NOT NULL,
                    ctime_ns INTEGER NOT NULL,
                    digest TEXT NOT NULL,
                    last_seen_ns INTEGER NOT NULL,
                    PRIMARY KEY (scope, path_key, algorithm)
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS hash_entries_last_seen ON hash_entries(last_seen_ns)"
            )
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            if version > CACHE_SCHEMA_VERSION:
                raise HashCacheError(
                    f"Cache schema {version} is newer than supported version {CACHE_SCHEMA_VERSION}"
                )
            self._connection.execute(f"PRAGMA user_version={CACHE_SCHEMA_VERSION}")
            self._connection.commit()
        except (OSError, sqlite3.Error, HashCacheError) as exc:
            raise HashCacheError(f"Could not open hash cache {self.path}: {exc}") from exc

    def lookup(self, path: Path) -> tuple[Optional[str], Optional[CacheProbe]]:
        probe = _probe_file(path)
        if probe is None or self.disabled:
            self.stats.misses += 1
            return None, probe
        try:
            row = self._connection.execute(
                """
                SELECT file_identity, size, mtime_ns, ctime_ns, digest
                FROM hash_entries
                WHERE scope = ? AND path_key = ? AND algorithm = ?
                """,
                (probe.scope, probe.path_key, probe.algorithm),
            ).fetchone()
            if row is not None:
                identity, size, mtime_ns, ctime_ns, digest = row
                if (
                    identity == probe.file_identity
                    and int(size) == probe.size
                    and int(mtime_ns) == probe.mtime_ns
                    and int(ctime_ns) == probe.ctime_ns
                    and isinstance(digest, str)
                    and _HASH_PATTERN.fullmatch(digest)
                ):
                    self._connection.execute(
                        """
                        UPDATE hash_entries SET last_seen_ns = ?
                        WHERE scope = ? AND path_key = ? AND algorithm = ?
                        """,
                        (time.time_ns(), probe.scope, probe.path_key, probe.algorithm),
                    )
                    self._mark_pending()
                    self.stats.hits += 1
                    return digest, probe
            self.stats.misses += 1
            return None, probe
        except sqlite3.Error as exc:
            self._disable(exc)
            self.stats.misses += 1
            return None, probe

    def store(self, probe: Optional[CacheProbe], digest: str) -> None:
        normalized = digest.lower()
        if probe is None or self.disabled or not _HASH_PATTERN.fullmatch(normalized):
            return
        try:
            self._connection.execute(
                """
                INSERT INTO hash_entries (
                    scope, path_key, algorithm, file_identity,
                    size, mtime_ns, ctime_ns, digest, last_seen_ns
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, path_key, algorithm) DO UPDATE SET
                    file_identity = excluded.file_identity,
                    size = excluded.size,
                    mtime_ns = excluded.mtime_ns,
                    ctime_ns = excluded.ctime_ns,
                    digest = excluded.digest,
                    last_seen_ns = excluded.last_seen_ns
                """,
                (
                    probe.scope,
                    probe.path_key,
                    probe.algorithm,
                    probe.file_identity,
                    probe.size,
                    probe.mtime_ns,
                    probe.ctime_ns,
                    normalized,
                    time.time_ns(),
                ),
            )
            self.stats.stores += 1
            self._mark_pending()
        except sqlite3.Error as exc:
            self._disable(exc)

    def prune(self, days: int) -> int:
        if days < 0:
            raise ValueError("days must be zero or greater")
        cutoff = time.time_ns() - (days * 24 * 60 * 60 * 1_000_000_000)
        try:
            cursor = self._connection.execute(
                "DELETE FROM hash_entries WHERE last_seen_ns < ?",
                (cutoff,),
            )
            removed = int(cursor.rowcount if cursor.rowcount >= 0 else 0)
            self._connection.commit()
            self._pending = 0
            if removed:
                self._connection.execute("VACUUM")
            return removed
        except sqlite3.Error as exc:
            raise HashCacheError(f"Could not prune hash cache: {exc}") from exc

    def info(self) -> CacheInfo:
        try:
            row = self._connection.execute(
                "SELECT COUNT(*), MIN(last_seen_ns), MAX(last_seen_ns) FROM hash_entries"
            ).fetchone()
            self._connection.commit()
            return CacheInfo(
                path=self.path,
                exists=True,
                entries=int(row[0]),
                file_size=self.path.stat().st_size if self.path.exists() else 0,
                oldest_seen_ns=int(row[1]) if row[1] is not None else None,
                newest_seen_ns=int(row[2]) if row[2] is not None else None,
            )
        except (OSError, sqlite3.Error) as exc:
            raise HashCacheError(f"Could not read hash cache information: {exc}") from exc

    def close(self) -> None:
        try:
            if not self.disabled:
                self._connection.commit()
        finally:
            self._connection.close()
            self._pending = 0

    def __enter__(self) -> "HashCache":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _mark_pending(self) -> None:
        self._pending += 1
        if self._pending >= _COMMIT_INTERVAL:
            self._connection.commit()
            self._pending = 0

    def _disable(self, exc: sqlite3.Error) -> None:
        self.disabled = True
        self.error_message = str(exc)
        try:
            self._connection.rollback()
        except sqlite3.Error:
            pass


def cache_info(path: Optional[Path] = None) -> CacheInfo:
    cache_path = Path(path or default_cache_path())
    if not cache_path.exists():
        return CacheInfo(path=cache_path, exists=False)
    with HashCache(cache_path) as cache:
        return cache.info()


def clear_cache(path: Optional[Path] = None) -> bool:
    cache_path = Path(path or default_cache_path())
    existed = cache_path.exists()
    for suffix in ("", "-wal", "-shm", "-journal"):
        candidate = Path(os.fspath(cache_path) + suffix)
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    return existed


def prune_cache(days: int, path: Optional[Path] = None) -> int:
    cache_path = Path(path or default_cache_path())
    if not cache_path.exists():
        return 0
    with HashCache(cache_path) as cache:
        return cache.prune(days)


def _format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{value} B"


def _format_seen(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return datetime.fromtimestamp(value / 1_000_000_000).astimezone().isoformat(timespec="seconds")


def build_cache_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirtree cache",
        description="Inspect or clean the local SHA-256 cache.",
    )
    parser.add_argument("action", choices=("info", "clear", "prune"))
    parser.add_argument("--days", type=int, default=30, help="stale age for prune (default: 30)")
    parser.add_argument("--file", help="override cache database path")
    return parser


def run_cache(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_cache_parser()
    args = parser.parse_args(argv)
    cache_path = Path(os.path.expandvars(os.path.expanduser(args.file))) if args.file else None
    try:
        if args.action == "clear":
            removed = clear_cache(cache_path)
            print(f"Hash cache cleared: {cache_path or default_cache_path()}")
            if not removed:
                print("Cache did not exist.")
            return 0
        if args.action == "prune":
            if args.days < 0:
                parser.error("--days must be zero or greater")
            removed = prune_cache(args.days, cache_path)
            print(f"Removed {removed} stale cache entries.")
            print(f"Hash cache: {cache_path or default_cache_path()}")
            return 0

        info = cache_info(cache_path)
        print(f"Hash cache: {info.path}")
        print(f"Exists: {'yes' if info.exists else 'no'}")
        print(f"Entries: {info.entries}")
        print(f"Database size: {_format_bytes(info.file_size)}")
        print(f"Oldest use: {_format_seen(info.oldest_seen_ns)}")
        print(f"Newest use: {_format_seen(info.newest_seen_ns)}")
        return 0
    except (HashCacheError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
