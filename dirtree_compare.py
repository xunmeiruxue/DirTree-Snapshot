#!/usr/bin/env python3
"""Compare DirTree Snapshot files and render readable reports."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterator, Optional, Sequence

from dirtree_assets import load_text, render

COMPARE_FORMAT_VERSION = 2
_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_FILE_DETAILS_PATTERN = re.compile(
    r" \[size=(?P<size>\d+|\?) B(?:, sha256=(?P<hash>[0-9a-fA-F]{64}|unreadable))?\]$"
)
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_STATUS_ORDER = {"changed": 0, "renamed": 1, "removed": 2, "added": 3, "same": 4}
_KIND_LABELS = {
    "directory": "目录",
    "file": "文件",
    "link": "链接",
    "special": "特殊文件",
    "unreadable": "无法读取",
}
_STATUS_LABELS = {
    "added": "新增",
    "removed": "缺失",
    "changed": "已更改",
    "renamed": "已重命名",
    "same": "相同",
}


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    kind: str
    size: Optional[int] = None
    sha256: Optional[str] = None


@dataclass
class SnapshotData:
    source_name: str
    source_format: str
    entries: list[ManifestEntry]
    warnings: list[str]
    created_at: Optional[str] = None


@dataclass(frozen=True)
class ComparisonRow:
    status: str
    path: str
    kind: str
    left: Optional[ManifestEntry]
    right: Optional[ManifestEntry]
    details: tuple[str, ...]
    hash_verified: bool = False


@dataclass
class ComparisonResult:
    rows: list[ComparisonRow]
    counts: dict[str, int]
    hash_verified: int
    unverified_files: int
    warnings: list[str]

    @property
    def differences(self) -> int:
        return (
            self.counts["added"]
            + self.counts["removed"]
            + self.counts["changed"]
            + self.counts["renamed"]
        )


class _SnapshotHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.entries: list[ManifestEntry] = []
        self.warnings: list[str] = []
        self.recognized = False
        self.created_at: Optional[str] = None
        self._items: list[dict[str, object]] = []
        self._hash_target: Optional[dict[str, object]] = None
        self._hash_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = {name: value or "" for name, value in attrs}
        classes = set(attributes.get("class", "").split())

        if tag == "time" and "snapshot-time" in classes:
            self.created_at = attributes.get("data-created-at") or attributes.get("datetime") or None

        if tag == "li" and "tree-item" in classes:
            self.recognized = True
            kind = attributes.get("data-kind") or self._kind_from_classes(classes)
            size = self._parse_size(attributes.get("data-size"))
            context: dict[str, object] = {
                "path": attributes.get("data-search", ""),
                "kind": kind,
                "size": size,
                "sha256": attributes.get("data-sha256") or None,
                "root": "root-item" in classes,
            }
            self._items.append(context)
            return

        if not self._items:
            return

        if tag == "span" and "node-size" in classes:
            size = self._parse_size(attributes.get("data-bytes"))
            if size is not None:
                self._items[-1]["size"] = size
        elif tag == "code" and "hash-value" in classes:
            self._hash_target = self._items[-1]
            self._hash_parts = []

    def handle_data(self, data: str) -> None:
        if self._hash_target is not None:
            self._hash_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "code" and self._hash_target is not None:
            hash_value = "".join(self._hash_parts).strip()
            if hash_value:
                self._hash_target["sha256"] = hash_value
            self._hash_target = None
            self._hash_parts = []
            return

        if tag != "li" or not self._items:
            return

        context = self._items.pop()
        if context["root"]:
            return
        path = _normalize_path(str(context["path"]))
        if not path:
            return
        self.entries.append(
            ManifestEntry(
                path=path,
                kind=str(context["kind"]),
                size=context["size"] if isinstance(context["size"], int) else None,
                sha256=str(context["sha256"]) if context["sha256"] else None,
            )
        )

    @staticmethod
    def _kind_from_classes(classes: set[str]) -> str:
        if "directory-item" in classes:
            return "directory"
        if "file-item" in classes:
            return "file"
        if "link-item" in classes:
            return "link"
        return "unreadable"

    @staticmethod
    def _parse_size(value: Optional[str]) -> Optional[int]:
        if value and value.isdigit():
            return int(value)
        return None


def _normalize_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


def _parse_html_snapshot(path: Path, content: str) -> SnapshotData:
    parser = _SnapshotHTMLParser()
    parser.feed(content)
    parser.close()
    if not parser.recognized:
        raise ValueError(f"Not a supported DirTree HTML snapshot: {path.name}")
    return SnapshotData(
        source_name=path.name,
        source_format="html",
        entries=parser.entries,
        warnings=parser.warnings,
        created_at=parser.created_at,
    )


def _parse_text_snapshot(path: Path, content: str) -> SnapshotData:
    lines = content.splitlines()
    if not lines or not lines[0].startswith("# DirTree Snapshot"):
        raise ValueError(f"Not a supported DirTree text snapshot: {path.name}")

    entries: list[ManifestEntry] = []
    warnings: list[str] = []
    created_at = next(
        (line[len("# Created: ") :] for line in lines if line.startswith("# Created: ")),
        None,
    )
    directory_stack: list[str] = []
    last_connector = chr(96) + "-- "

    for line_number, line in enumerate(lines[1:], start=2):
        if not line or line == "." or line.startswith("#"):
            continue

        remainder = line
        depth = 0
        while remainder.startswith("|   ") or remainder.startswith("    "):
            remainder = remainder[4:]
            depth += 1

        if remainder.startswith("|-- "):
            label = remainder[4:]
        elif remainder.startswith(last_connector):
            label = remainder[4:]
        else:
            warnings.append(f"第 {line_number} 行无法识别，已跳过")
            continue

        directory_stack = directory_stack[:depth]
        if label == "[unreadable]":
            warnings.append(f"第 {line_number} 行标记为无法读取")
            continue

        if label.endswith("/"):
            name = label[:-1]
            entry_path = _normalize_path("/".join(directory_stack + [name]))
            entries.append(ManifestEntry(path=entry_path, kind="directory"))
            directory_stack.append(name)
            continue

        kind = "file"
        size: Optional[int] = None
        sha256: Optional[str] = None

        if label.endswith(" [link-not-followed]"):
            name = label[: -len(" [link-not-followed]")]
            kind = "link"
        elif label.endswith(" [special-file]"):
            name = label[: -len(" [special-file]")]
            kind = "special"
        elif label.endswith(" [unreadable]"):
            name = label[: -len(" [unreadable]")]
            kind = "unreadable"
        else:
            match = _FILE_DETAILS_PATTERN.search(label)
            if match:
                name = label[: match.start()]
                size_text = match.group("size")
                size = int(size_text) if size_text.isdigit() else None
                sha256 = match.group("hash")
            else:
                name = label

        entry_path = _normalize_path("/".join(directory_stack + [name]))
        entries.append(
            ManifestEntry(path=entry_path, kind=kind, size=size, sha256=sha256)
        )

    return SnapshotData(
        source_name=path.name,
        source_format="text",
        entries=entries,
        warnings=warnings,
        created_at=created_at,
    )


def _parse_json_snapshot(path: Path, content: str) -> SnapshotData:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON snapshot {path.name}: {exc}") from exc

    if payload.get("schema") != "dirtree.snapshot":
        raise ValueError(f"Unsupported JSON snapshot schema: {path.name}")
    if payload.get("schema_version") != 1:
        raise ValueError(f"Unsupported JSON snapshot version: {path.name}")

    entries: list[ManifestEntry] = []
    warnings: list[str] = []
    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError(f"JSON snapshot entries must be an array: {path.name}")

    valid_kinds = {"directory", "file", "link", "special", "unreadable"}
    for index, raw_entry in enumerate(raw_entries, start=1):
        if not isinstance(raw_entry, dict):
            warnings.append(f"{path.name} 第 {index} 个条目不是对象，已跳过")
            continue
        entry_path = _normalize_path(str(raw_entry.get("path", "")))
        kind = str(raw_entry.get("kind", ""))
        if not entry_path or kind not in valid_kinds:
            warnings.append(f"{path.name} 第 {index} 个条目缺少有效路径或类型，已跳过")
            continue
        raw_size = raw_entry.get("size")
        size = raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None
        raw_hash = raw_entry.get("sha256")
        sha256 = str(raw_hash) if raw_hash is not None else None
        entries.append(ManifestEntry(path=entry_path, kind=kind, size=size, sha256=sha256))

    return SnapshotData(
        source_name=path.name,
        source_format="json",
        entries=entries,
        warnings=warnings,
        created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
    )


def load_snapshot(path: Path) -> SnapshotData:
    path = Path(os.path.abspath(os.fspath(path)))
    if not path.is_file():
        raise ValueError(f"Snapshot file does not exist: {path}")
    try:
        content = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"Could not read snapshot {path.name}: {exc}") from exc

    stripped = content.lstrip()
    if stripped.startswith("{"):
        return _parse_json_snapshot(path, content)
    if stripped.lower().startswith("<!doctype html") or stripped.lower().startswith("<html"):
        return _parse_html_snapshot(path, content)
    return _parse_text_snapshot(path, content)


def _usable_hash(value: Optional[str]) -> bool:
    return bool(value and _HASH_PATTERN.fullmatch(value))


def _index_entries(
    snapshot: SnapshotData,
    case_sensitive: bool,
) -> tuple[dict[str, ManifestEntry], list[str]]:
    indexed: dict[str, ManifestEntry] = {}
    warnings: list[str] = []
    for entry in snapshot.entries:
        key = entry.path if case_sensitive else entry.path.casefold()
        if key in indexed:
            warnings.append(
                f"{snapshot.source_name} 中存在重复路径：{indexed[key].path} / {entry.path}"
            )
            continue
        indexed[key] = entry
    return indexed, warnings


def compare_snapshots(
    left_snapshot: SnapshotData,
    right_snapshot: SnapshotData,
    case_sensitive: bool = False,
) -> ComparisonResult:
    left_entries, left_warnings = _index_entries(left_snapshot, case_sensitive)
    right_entries, right_warnings = _index_entries(right_snapshot, case_sensitive)
    rows: list[ComparisonRow] = []
    hash_verified = 0
    unverified_files = 0

    # Match only unique hashes among unmatched files. Ambiguous duplicate hashes
    # remain added/removed instead of being guessed as renames.
    left_hashes: dict[str, list[tuple[str, ManifestEntry]]] = {}
    right_hashes: dict[str, list[tuple[str, ManifestEntry]]] = {}
    for key, entry in left_entries.items():
        if entry.kind == "file" and _usable_hash(entry.sha256):
            left_hashes.setdefault(entry.sha256.lower(), []).append((key, entry))
    for key, entry in right_entries.items():
        if entry.kind == "file" and _usable_hash(entry.sha256):
            right_hashes.setdefault(entry.sha256.lower(), []).append((key, entry))

    renamed_by_left: dict[str, tuple[str, ManifestEntry]] = {}
    renamed_by_right: dict[str, tuple[str, ManifestEntry]] = {}
    for digest, left_matches in left_hashes.items():
        right_matches = right_hashes.get(digest, [])
        if len(left_matches) == 1 and len(right_matches) == 1:
            left_key, left_entry = left_matches[0]
            right_key, right_entry = right_matches[0]
            if left_key not in right_entries and right_key not in left_entries:
                renamed_by_left[left_key] = (right_key, right_entry)
                renamed_by_right[right_key] = (left_key, left_entry)
                rows.append(
                    ComparisonRow(
                        status="renamed",
                        path=right_entry.path,
                        kind="file",
                        left=left_entry,
                        right=right_entry,
                        details=(f"重命名：{left_entry.path} -> {right_entry.path}",),
                        hash_verified=True,
                    )
                )
                hash_verified += 1

    remaining_left = set(left_entries) - set(renamed_by_left)
    remaining_right = set(right_entries) - set(renamed_by_right)
    for key in sorted(remaining_left | remaining_right, key=str.casefold):
        left = left_entries.get(key) if key in remaining_left else None
        right = right_entries.get(key) if key in remaining_right else None

        if left is None and right is not None:
            rows.append(
                ComparisonRow(
                    status="added",
                    path=right.path,
                    kind=right.kind,
                    left=None,
                    right=right,
                    details=("仅存在于右侧清单",),
                )
            )
            continue

        if right is None and left is not None:
            rows.append(
                ComparisonRow(
                    status="removed",
                    path=left.path,
                    kind=left.kind,
                    left=left,
                    right=None,
                    details=("仅存在于左侧清单",),
                )
            )
            continue

        assert left is not None and right is not None
        changes: list[str] = []
        notes: list[str] = []
        verified = False

        if left.path != right.path:
            changes.append("路径大小写不同")
        if left.kind != right.kind:
            changes.append(
                f"类型不同：{_KIND_LABELS.get(left.kind, left.kind)} -> "
                f"{_KIND_LABELS.get(right.kind, right.kind)}"
            )
        elif left.kind == "file":
            if left.size is not None and right.size is not None:
                if left.size != right.size:
                    changes.append(f"大小不同：{left.size} B -> {right.size} B")
            else:
                notes.append("至少一侧未记录文件大小")

            if _usable_hash(left.sha256) and _usable_hash(right.sha256):
                verified = True
                hash_verified += 1
                if left.sha256.lower() != right.sha256.lower():
                    changes.append("SHA-256 不同")
                else:
                    notes.append("SHA-256 一致")
            else:
                unverified_files += 1
                if left.sha256 == "unreadable" or right.sha256 == "unreadable":
                    notes.append("至少一侧哈希无法读取")
                else:
                    notes.append("未进行双侧 SHA-256 校验")
        elif left.kind == "directory":
            notes.append("目录存在于两侧")
        elif left.kind == "link":
            notes.append("链接存在于两侧")

        status = "changed" if changes else "same"
        detail_values = tuple(changes if changes else notes or ["项目一致"])
        rows.append(
            ComparisonRow(
                status=status,
                path=right.path,
                kind=right.kind,
                left=left,
                right=right,
                details=detail_values,
                hash_verified=verified,
            )
        )

    rows.sort(key=lambda row: (_STATUS_ORDER[row.status], row.path.casefold(), row.path))
    counts = {status: 0 for status in _STATUS_ORDER}
    for row in rows:
        counts[row.status] += 1

    warnings = (
        list(left_snapshot.warnings)
        + list(right_snapshot.warnings)
        + left_warnings
        + right_warnings
    )
    return ComparisonResult(
        rows=rows,
        counts=counts,
        hash_verified=hash_verified,
        unverified_files=unverified_files,
        warnings=warnings,
    )


def _safe_output_stem(path: Path) -> str:
    value = path.stem
    value = re.sub(
        r"(?:-|_)(?:tree|snapshot)(?:-|_)\d{8}-\d{6}(?:-\d+)?$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    for suffix in ("-tree", "_tree", "-snapshot", "_snapshot"):
        if value.lower().endswith(suffix):
            value = value[: -len(suffix)]
            break
    value = _INVALID_FILENAME_CHARS.sub("_", value).strip(". ")
    return value or "snapshot"


def _current_timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _display_timestamp(value: Optional[str]) -> str:
    if not value:
        return "未记录"
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


def default_compare_output(
    left: Path,
    right: Path,
    output_format: str,
    created_at: Optional[str] = None,
) -> Path:
    extension = {"html": "html", "json": "json", "text": "txt"}.get(output_format, output_format)
    timestamp = _filename_timestamp(created_at or _current_timestamp())
    left_stem = _safe_output_stem(left)
    right_stem = _safe_output_stem(right)
    if left_stem.casefold() == right_stem.casefold():
        report_stem = f"{left_stem}-diff"
    else:
        report_stem = f"{left_stem}-vs-{right_stem}-diff"
    filename = f"{report_stem}-{timestamp}.{extension}"
    return _next_available_path(Path.cwd() / filename)


def _format_entry_text(entry: Optional[ManifestEntry]) -> str:
    if entry is None:
        return "-"
    values = [_KIND_LABELS.get(entry.kind, entry.kind)]
    if entry.kind == "file":
        values.append(f"size={entry.size if entry.size is not None else '?'} B")
        if entry.sha256:
            values.append(f"sha256={entry.sha256}")
    return ", ".join(values)


def _entry_payload(entry: Optional[ManifestEntry]) -> Optional[dict[str, object]]:
    if entry is None:
        return None
    payload: dict[str, object] = {"path": entry.path, "kind": entry.kind}
    if entry.kind == "file":
        payload["size"] = entry.size
        if entry.sha256 is not None:
            payload["sha256"] = entry.sha256
    return payload


def iter_json_report(
    left: SnapshotData,
    right: SnapshotData,
    result: ComparisonResult,
    include_unchanged: bool,
    created_at: str,
) -> Iterator[str]:
    rows: list[dict[str, object]] = []
    for row in result.rows:
        if row.status == "same" and not include_unchanged:
            continue
        rows.append(
            {
                "status": row.status,
                "status_label": _STATUS_LABELS[row.status],
                "path": row.path,
                "kind": row.kind,
                "hash_verified": row.hash_verified,
                "details": list(row.details),
                "left": _entry_payload(row.left),
                "right": _entry_payload(row.right),
            }
        )
    payload = {
        "schema": "dirtree.comparison",
        "schema_version": COMPARE_FORMAT_VERSION,
        "created_at": created_at,
        "left": {
            "name": left.source_name,
            "format": left.source_format,
            "created_at": left.created_at,
        },
        "right": {
            "name": right.source_name,
            "format": right.source_format,
            "created_at": right.created_at,
        },
        "summary": {
            **result.counts,
            "differences": result.differences,
            "hash_verified": result.hash_verified,
            "unverified_files": result.unverified_files,
        },
        "warnings": list(result.warnings),
        "entries": rows,
    }
    yield json.dumps(payload, ensure_ascii=False, indent=2)


def iter_text_report(
    left: SnapshotData,
    right: SnapshotData,
    result: ComparisonResult,
    include_unchanged: bool,
    created_at: str,
) -> Iterator[str]:
    yield f"# DirTree Comparison v{COMPARE_FORMAT_VERSION}"
    yield f"# Created: {created_at}"
    yield f"# Left: {left.source_name}"
    yield f"# Left created: {left.created_at or 'unknown'}"
    yield f"# Right: {right.source_name}"
    yield f"# Right created: {right.created_at or 'unknown'}"
    yield (
        "# Summary: "
        f"changed={result.counts['changed']} renamed={result.counts['renamed']} "
        f"added={result.counts['added']} removed={result.counts['removed']} "
        f"same={result.counts['same']} "
        f"hash-verified={result.hash_verified}"
    )
    yield ""

    for row in result.rows:
        if row.status == "same" and not include_unchanged:
            continue
        yield f"[{row.status.upper()}] {row.path}"
        yield f"  left:  {_format_entry_text(row.left)}"
        yield f"  right: {_format_entry_text(row.right)}"
        for detail in row.details:
            yield f"  detail: {detail}"
        yield ""

    if result.warnings:
        yield "# Warnings"
        for warning in result.warnings:
            yield f"- {warning}"


def _format_size(value: Optional[int]) -> str:
    return f"{value:,} B" if value is not None else "未记录"


def _entry_html(entry: Optional[ManifestEntry]) -> str:
    if entry is None:
        return '<span class="no-value">-</span>'
    if entry.kind != "file":
        return f'<span class="kind-value">{html.escape(_KIND_LABELS.get(entry.kind, entry.kind))}</span>'

    parts = ['<div class="metadata">', f'<span class="size-value">{_format_size(entry.size)}</span>']
    if entry.sha256:
        hash_value = html.escape(entry.sha256)
        parts.append(
            '<details class="hash-details"><summary>SHA-256</summary>'
            f'<code class="hash-value">{hash_value}</code></details>'
        )
    else:
        parts.append('<span class="no-value">未记录哈希</span>')
    parts.append("</div>")
    return "".join(parts)


def iter_html_report(
    left: SnapshotData,
    right: SnapshotData,
    result: ComparisonResult,
    include_unchanged: bool,
    created_at: str,
) -> Iterator[str]:
    initial_filter = "all" if include_unchanged else "differences"
    notices: list[str] = []
    if result.unverified_files:
        notices.append(
            '<div class="notice">'
            f"有 {result.unverified_files} 个同路径文件未同时包含可用 SHA-256；"
            "这些文件仅根据路径、类型和大小判断。"
            "</div>"
        )
    notices.extend(
        f'<div class="notice">{html.escape(warning)}</div>'
        for warning in result.warnings
    )
    yield render(
        "compare_head.html",
        STYLES=load_text("compare.css"),
        INITIAL_FILTER=initial_filter,
        LEFT_NAME=html.escape(left.source_name),
        LEFT_CREATED=html.escape(_display_timestamp(left.created_at)),
        RIGHT_NAME=html.escape(right.source_name),
        RIGHT_CREATED=html.escape(_display_timestamp(right.created_at)),
        CHANGED=str(result.counts["changed"]),
        RENAMED=str(result.counts["renamed"]),
        ADDED=str(result.counts["added"]),
        REMOVED=str(result.counts["removed"]),
        SAME=str(result.counts["same"]),
        HASH_VERIFIED=str(result.hash_verified),
        NOTICES="\n".join(notices),
    )

    for row in result.rows:
        search_value = html.escape(row.path, quote=True)
        status_label = _STATUS_LABELS[row.status]
        kind_label = _KIND_LABELS.get(row.kind, row.kind)
        detail_html = "；".join(html.escape(detail) for detail in row.details)
        yield f'      <tr data-status="{row.status}" data-search="{search_value}">'
        yield (
            f'        <td data-label="状态"><span class="status-badge status-{row.status}">'
            f"{status_label}</span></td>"
        )
        yield (
            f'        <td data-label="相对路径"><code class="path-value">{html.escape(row.path)}</code>'
            f'<div class="reason">{detail_html}</div></td>'
        )
        yield f'        <td data-label="类型"><span class="kind-value">{html.escape(kind_label)}</span></td>'
        yield f'        <td data-label="左侧">{_entry_html(row.left)}</td>'
        yield f'        <td data-label="右侧">{_entry_html(row.right)}</td>'
        yield "      </tr>"

    yield render(
        "compare_footer.html",
        DIFFERENCES=str(result.differences),
        REPORT_TIME=_display_timestamp(created_at),
        SCRIPT=load_text("compare.js"),
    )

def _write_report(
    path: Path,
    lines: Iterator[str],
    encoding: str = "utf-8-sig",
) -> None:
    path = Path(os.path.abspath(os.fspath(path)))
    if not path.parent.is_dir():
        raise ValueError(f"Output directory does not exist: {path.parent}")

    descriptor = -1
    temporary_path: Optional[Path] = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".dirtree-compare-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding=encoding, newline="\n") as output:
            descriptor = -1
            for line in lines:
                output.write(line)
                output.write("\n")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass


def build_compare_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dirtree compare",
        description="Compare two DirTree Snapshot HTML, text, or JSON files.",
    )
    parser.add_argument("left", help="source or earlier snapshot")
    parser.add_argument("right", help="backup or later snapshot")
    parser.add_argument(
        "-o",
        "--output",
        help="report output file (default name includes the current timestamp)",
    )
    parser.add_argument(
        "--format",
        choices=("html", "text", "json"),
        help="report format; inferred from .txt/.json, otherwise defaults to html",
    )
    parser.add_argument(
        "--include-unchanged",
        action="store_true",
        help="show unchanged items initially and include them in text/JSON reports",
    )
    parser.add_argument(
        "--case-sensitive",
        action="store_true",
        help="compare relative paths with case sensitivity",
    )
    return parser


def run_compare(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_compare_parser()
    args = parser.parse_args(argv)
    left_path = Path(os.path.expandvars(os.path.expanduser(args.left.strip('"'))))
    right_path = Path(os.path.expandvars(os.path.expanduser(args.right.strip('"'))))

    try:
        left = load_snapshot(left_path)
        right = load_snapshot(right_path)
        result = compare_snapshots(left, right, case_sensitive=args.case_sensitive)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    report_created_at = _current_timestamp()
    output: Optional[Path] = None
    if args.output:
        output = Path(os.path.expandvars(os.path.expanduser(args.output.strip('"'))))

    output_format = args.format
    if output_format is None:
        if output is not None and output.suffix.lower() == ".txt":
            output_format = "text"
        elif output is not None and output.suffix.lower() == ".json":
            output_format = "json"
        else:
            output_format = "html"
    if output is None:
        output = default_compare_output(
            left_path,
            right_path,
            output_format,
            report_created_at,
        )

    absolute_output = Path(os.path.abspath(os.fspath(output)))
    input_keys = {
        os.path.normcase(os.path.abspath(os.fspath(left_path))),
        os.path.normcase(os.path.abspath(os.fspath(right_path))),
    }
    if os.path.normcase(os.fspath(absolute_output)) in input_keys:
        print("Error: comparison output cannot overwrite an input snapshot", file=sys.stderr)
        return 2

    if output_format == "html":
        lines = iter_html_report(
            left,
            right,
            result,
            args.include_unchanged,
            report_created_at,
        )
    elif output_format == "json":
        lines = iter_json_report(
            left,
            right,
            result,
            args.include_unchanged,
            report_created_at,
        )
    else:
        lines = iter_text_report(
            left,
            right,
            result,
            args.include_unchanged,
            report_created_at,
        )

    try:
        _write_report(
            absolute_output,
            lines,
            encoding="utf-8" if output_format == "json" else "utf-8-sig",
        )
    except (OSError, ValueError) as exc:
        print(f"Error: could not write comparison report: {exc}", file=sys.stderr)
        return 1

    print(
        f"Left snapshot: {left.source_name} ({len(left.entries)} entries, "
        f"{_display_timestamp(left.created_at)})"
    )
    print(
        f"Right snapshot: {right.source_name} ({len(right.entries)} entries, "
        f"{_display_timestamp(right.created_at)})"
    )
    print(f"Changed: {result.counts['changed']}")
    print(f"Renamed: {result.counts['renamed']}")
    print(f"Added: {result.counts['added']}")
    print(f"Removed: {result.counts['removed']}")
    print(f"Same: {result.counts['same']}")
    print(f"SHA-256 verified: {result.hash_verified}")
    print(f"Report time: {_display_timestamp(report_created_at)}")
    print(f"Comparison report: {absolute_output}")
    for warning in result.warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    return 0
