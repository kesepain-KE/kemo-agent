"""Filesystem snapshot safety primitives for Kemo Graph library scans."""


from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from client import api_request
from errors import GraphExpandError
from registry import (
    GraphConfig,
    GraphLibrary,
    SYNC_STATE_PATH,
    atomic_json,
    library_signature,
    resolve_libraries,
    unavailable_source_roots,
)


SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xls", ".epub", ".rtf",
    ".eml",
    ".md", ".markdown", ".txt", ".log", ".html", ".htm", ".rst", ".csv",
    ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml",
})
MAX_IMPORT_BYTES = 50 * 1024 * 1024


class _SnapshotInvalidated(GraphExpandError):
    """Raised when a scanned source can no longer be proven identical."""


@dataclass(frozen=True, slots=True)
class _PathState:
    mode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _RootSnapshot:
    logical: Path
    resolved: Path
    state: _PathState


@dataclass(frozen=True, slots=True)
class _DirectorySnapshot:
    logical: Path
    resolved: Path
    root: _RootSnapshot
    state: _PathState
    entries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    logical: Path
    resolved: Path
    root: _RootSnapshot
    directory: _DirectorySnapshot
    state: _PathState
    sha256: str


@dataclass(frozen=True, slots=True)
class _LibraryScan:
    files: dict[str, dict[str, Any]]
    roots: tuple[_RootSnapshot, ...]
    directories: tuple[_DirectorySnapshot, ...]
    file_snapshots: dict[str, _FileSnapshot]


def _path_state(metadata: os.stat_result) -> _PathState:
    return _PathState(
        mode=int(metadata.st_mode),
        size=int(metadata.st_size),
        mtime_ns=int(metadata.st_mtime_ns),
        ctime_ns=int(metadata.st_ctime_ns),
        device=int(getattr(metadata, "st_dev", 0) or 0),
        inode=int(getattr(metadata, "st_ino", 0) or 0),
    )


def _same_path_state(
    expected: _PathState,
    current: _PathState,
    *,
    compare_ctime: bool = True,
) -> bool:
    if (
        stat.S_IFMT(expected.mode) != stat.S_IFMT(current.mode)
        or expected.size != current.size
        or expected.mtime_ns != current.mtime_ns
        or (compare_ctime and expected.ctime_ns != current.ctime_ns)
    ):
        return False
    if (
        expected.device
        and expected.inode
        and current.device
        and current.inode
        and (expected.device, expected.inode) != (current.device, current.inode)
    ):
        return False
    return True


def _metadata_is_link(path: Path, metadata: os.stat_result) -> bool:
    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if reparse_flag and getattr(metadata, "st_file_attributes", 0) & reparse_flag:
        return True
    isjunction = getattr(os.path, "isjunction", None)
    try:
        return bool(isjunction and isjunction(path))
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"无法确认扫描路径是否为目录联接：{path}") from exc


def _safe_lstat(path: Path, *, expected_kind: str | None = None) -> os.stat_result:
    try:
        metadata = path.lstat()
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"扫描路径不存在或无法访问：{path}") from exc
    if _metadata_is_link(path, metadata):
        raise _SnapshotInvalidated(f"扫描路径不能是符号链接、目录联接或重解析点：{path}")
    if expected_kind == "directory" and not stat.S_ISDIR(metadata.st_mode):
        raise _SnapshotInvalidated(f"扫描目录已不是普通目录：{path}")
    if expected_kind == "file" and not stat.S_ISREG(metadata.st_mode):
        raise _SnapshotInvalidated(f"扫描文件已不是普通文件：{path}")
    return metadata


def _validate_path_components(path: Path, *, final_kind: str) -> os.stat_result:
    if not path.is_absolute():
        raise _SnapshotInvalidated(f"扫描路径必须是绝对路径：{path}")
    current = Path(path.anchor)
    metadata: os.stat_result | None = None
    parts = path.parts[1:] if path.anchor else path.parts
    for index, part in enumerate(parts):
        current /= part
        metadata = _safe_lstat(
            current,
            expected_kind=final_kind if index == len(parts) - 1 else "directory",
        )
    if metadata is None:
        metadata = _safe_lstat(path, expected_kind=final_kind)
    return metadata


def _resolved_path(root: _RootSnapshot, path: Path) -> Path:
    try:
        relative = path.relative_to(root.logical)
    except ValueError as exc:
        raise _SnapshotInvalidated(f"扫描路径已逃逸 source_root：{path}") from exc
    expected = root.resolved.joinpath(*relative.parts)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"扫描路径无法解析：{path}") from exc
    if resolved != expected:
        raise _SnapshotInvalidated(f"扫描路径解析后已逃逸 source_root：{path}")
    try:
        resolved.relative_to(root.resolved)
    except ValueError as exc:
        raise _SnapshotInvalidated(f"扫描路径解析后已逃逸 source_root：{path}") from exc
    return resolved


def _capture_root(root_text: str) -> _RootSnapshot:
    logical = Path(root_text)
    metadata = _validate_path_components(logical, final_kind="directory")
    try:
        resolved = logical.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"source_root 无法解析：{logical}") from exc
    # Registered roots are normalized before being persisted. Following a new
    # link here would turn an attacker-controlled target into the trusted root.
    if resolved != logical:
        raise _SnapshotInvalidated(f"source_root 解析目标已变化：{logical}")
    final = _validate_path_components(logical, final_kind="directory")
    if not _same_path_state(_path_state(metadata), _path_state(final)):
        raise _SnapshotInvalidated(f"source_root 在校验期间发生变化：{logical}")
    return _RootSnapshot(logical, resolved, _path_state(final))


def _verify_root(snapshot: _RootSnapshot) -> None:
    metadata = _validate_path_components(snapshot.logical, final_kind="directory")
    try:
        resolved = snapshot.logical.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"source_root 无法重新解析：{snapshot.logical}") from exc
    if resolved != snapshot.resolved or not _same_path_state(
        snapshot.state,
        _path_state(metadata),
    ):
        raise _SnapshotInvalidated(f"source_root 在扫描后发生变化：{snapshot.logical}")


def _capture_directory(root: _RootSnapshot, path: Path) -> _DirectorySnapshot:
    _verify_root(root)
    metadata = _validate_path_components(path, final_kind="directory")
    resolved = _resolved_path(root, path)
    snapshot = _DirectorySnapshot(path, resolved, root, _path_state(metadata))
    _verify_root(root)
    return snapshot


def _directory_entries(path: Path) -> tuple[str, ...]:
    try:
        with os.scandir(path) as entries:
            return tuple(sorted(entry.name for entry in entries))
    except (OSError, ValueError) as exc:
        raise _SnapshotInvalidated(f"无法枚举扫描目录：{path}") from exc


def _verify_directory(
    snapshot: _DirectorySnapshot,
    *,
    verify_entries: bool = False,
) -> None:
    _verify_root(snapshot.root)
    metadata = _validate_path_components(snapshot.logical, final_kind="directory")
    resolved = _resolved_path(snapshot.root, snapshot.logical)
    if resolved != snapshot.resolved or not _same_path_state(
        snapshot.state,
        _path_state(metadata),
    ):
        raise _SnapshotInvalidated(f"扫描目录在扫描后发生变化：{snapshot.logical}")
    if verify_entries and _directory_entries(snapshot.logical) != snapshot.entries:
        raise _SnapshotInvalidated(f"扫描目录内容在扫描后发生变化：{snapshot.logical}")
    final = _validate_path_components(snapshot.logical, final_kind="directory")
    if not _same_path_state(snapshot.state, _path_state(final)):
        raise _SnapshotInvalidated(f"扫描目录在枚举期间发生变化：{snapshot.logical}")


def _read_verified_file(
    root: _RootSnapshot,
    directory: _DirectorySnapshot,
    path: Path,
    *,
    expected: _PathState | None = None,
    expected_sha256: str | None = None,
) -> tuple[_PathState, str]:
    _verify_directory(directory)
    metadata = _validate_path_components(path, final_kind="file")
    resolved = _resolved_path(root, path)
    before = _path_state(metadata)
    if expected is not None and not _same_path_state(expected, before):
        raise _SnapshotInvalidated(f"扫描文件在扫描后发生变化：{path}")
    if before.size > MAX_IMPORT_BYTES:
        raise _SnapshotInvalidated(f"扫描文件超过 50 MB 安全上限：{path}")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(resolved, flags)
    except OSError as exc:
        raise _SnapshotInvalidated(f"无法安全打开扫描文件：{path}") from exc
    digest = hashlib.sha256()
    try:
        try:
            handle = os.fdopen(descriptor, "rb", closefd=True)
        except OSError:
            os.close(descriptor)
            raise
        with handle:
            opened = _path_state(os.fstat(handle.fileno()))
            # Windows' path stat reports creation time as st_ctime while the
            # CRT fstat may mirror mtime. Keep ctime checks within each API and
            # use dev/inode, mode, size, and mtime across the open boundary.
            if not _same_path_state(before, opened, compare_ctime=False):
                raise _SnapshotInvalidated(f"打开的对象与扫描文件不一致：{path}")
            total = 0
            while block := handle.read(1024 * 1024):
                total += len(block)
                if total > MAX_IMPORT_BYTES:
                    raise _SnapshotInvalidated(f"扫描文件读取期间超过 50 MB：{path}")
                digest.update(block)
            final_opened = _path_state(os.fstat(handle.fileno()))
            if not _same_path_state(opened, final_opened) or total != final_opened.size:
                raise _SnapshotInvalidated(f"扫描文件在读取期间发生变化：{path}")
    except _SnapshotInvalidated:
        raise
    except OSError as exc:
        raise _SnapshotInvalidated(f"读取扫描文件失败：{path}") from exc

    final_metadata = _validate_path_components(path, final_kind="file")
    if _resolved_path(root, path) != resolved or not _same_path_state(
        before,
        _path_state(final_metadata),
    ):
        raise _SnapshotInvalidated(f"扫描文件在读取后发生变化：{path}")
    _verify_directory(directory)
    result = digest.hexdigest()
    if expected_sha256 is not None and result != expected_sha256:
        raise _SnapshotInvalidated(f"扫描文件内容在扫描后发生变化：{path}")
    return before, result


def _verify_file(snapshot: _FileSnapshot) -> None:
    _verify_directory(snapshot.directory)
    metadata = _validate_path_components(snapshot.logical, final_kind="file")
    resolved = _resolved_path(snapshot.root, snapshot.logical)
    if resolved != snapshot.resolved or not _same_path_state(
        snapshot.state,
        _path_state(metadata),
    ):
        raise _SnapshotInvalidated(f"扫描文件在扫描后发生变化：{snapshot.logical}")


def _verify_scan(scan: _LibraryScan) -> None:
    for root in scan.roots:
        _verify_root(root)
    for directory in scan.directories:
        _verify_directory(directory, verify_entries=True)
    for snapshot in scan.file_snapshots.values():
        _verify_file(snapshot)
