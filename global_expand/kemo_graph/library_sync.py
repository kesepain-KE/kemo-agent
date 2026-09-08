"""Explicit, incremental source scanning for registered portable libraries."""

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


def _load_state() -> dict[str, Any]:
    if not SYNC_STATE_PATH.is_file():
        return {"schema_version": 2, "libraries": {}}
    try:
        value = json.loads(SYNC_STATE_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"schema_version": 2, "libraries": {}}
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        return {"schema_version": 2, "libraries": {}}
    libraries = value.get("libraries")
    value["libraries"] = libraries if isinstance(libraries, dict) else {}
    return value


def _library_files(library: GraphLibrary) -> _LibraryScan:
    files: dict[str, dict[str, Any]] = {}
    roots: list[_RootSnapshot] = []
    directory_snapshots: list[_DirectorySnapshot] = []
    file_snapshots: dict[str, _FileSnapshot] = {}
    unavailable = unavailable_source_roots(library)
    if unavailable:
        raise GraphExpandError(
            f"source_root 当前不可用：{', '.join(unavailable)}"
        )
    for root_text in library.source_roots:
        root_snapshot = _capture_root(root_text)
        roots.append(root_snapshot)
        pending_directories = [root_snapshot.logical]
        while pending_directories:
            base = pending_directories.pop()
            directory_snapshot = _capture_directory(root_snapshot, base)
            names = _directory_entries(base)
            directory_snapshot = _DirectorySnapshot(
                directory_snapshot.logical,
                directory_snapshot.resolved,
                directory_snapshot.root,
                directory_snapshot.state,
                names,
            )
            directory_snapshots.append(directory_snapshot)
            accepted_directories: list[Path] = []
            for name in names:
                if name == "kemo-graph-storage" or name.startswith("."):
                    continue
                path = base / name
                metadata = _safe_lstat(path)
                if stat.S_ISDIR(metadata.st_mode):
                    _validate_path_components(path, final_kind="directory")
                    _resolved_path(root_snapshot, path)
                    accepted_directories.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode):
                    if path.suffix.casefold() in SUPPORTED_EXTENSIONS:
                        raise _SnapshotInvalidated(f"扫描文件不是普通文件：{path}")
                    continue
                if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                    continue
                metadata = _validate_path_components(path, final_kind="file")
                if metadata.st_size > MAX_IMPORT_BYTES:
                    continue
                resolved = _resolved_path(root_snapshot, path)
                state, digest = _read_verified_file(
                    root_snapshot,
                    directory_snapshot,
                    path,
                )
                key = str(resolved)
                if key in file_snapshots:
                    raise _SnapshotInvalidated(f"扫描文件规范化路径发生冲突：{path}")
                snapshot = _FileSnapshot(
                    path,
                    resolved,
                    root_snapshot,
                    directory_snapshot,
                    state,
                    digest,
                )
                file_snapshots[key] = snapshot
                files[key] = {
                    "sha256": digest,
                    "size": state.size,
                    "mtime_ns": state.mtime_ns,
                    "source_root": root_text,
                }
            _verify_directory(directory_snapshot, verify_entries=True)
            pending_directories.extend(reversed(accepted_directories))
    unavailable = unavailable_source_roots(library)
    if unavailable:
        raise GraphExpandError(
            f"source_root 扫描期间变为不可用：{', '.join(unavailable)}"
        )
    scan = _LibraryScan(
        files,
        tuple(roots),
        tuple(directory_snapshots),
        file_snapshots,
    )
    _verify_scan(scan)
    return scan


def _source_unavailable_row(
    library: GraphLibrary,
    unavailable: list[str],
    *,
    sync: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "library_id": library.id,
        "ok": False,
        "status": "source_unavailable",
        "unavailable_source_roots": unavailable,
        "reason": "来源目录当前不可用；为防止把暂时离线误判为删除，本次未计算差异且未推进游标",
    }
    if sync:
        row.update({
            "imported": 0,
            "deleted": 0,
            "failed": 0,
            "deletions_pending_confirmation": 0,
        })
    else:
        row.update({
            "summary": {"added": 0, "modified": 0, "deleted": 0, "unchanged": 0},
            "changes": {},
            "truncated": False,
        })
    return row


def _previous_files(
    state: dict[str, Any],
    library: GraphLibrary,
) -> tuple[dict[str, dict[str, Any]], bool]:
    libraries = state.get("libraries") if isinstance(state.get("libraries"), dict) else {}
    stored = libraries.get(library.id) if isinstance(libraries.get(library.id), dict) else {}
    signature_matches = stored.get("registry_signature") == library_signature(library)
    files = stored.get("files") if signature_matches and isinstance(stored.get("files"), dict) else {}
    return ({
        str(path): dict(item)
        for path, item in files.items()
        if isinstance(path, str) and isinstance(item, dict)
    }, signature_matches)


def _diff(
    current: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    added: list[str] = []
    modified: list[str] = []
    unchanged: list[str] = []
    for path, item in current.items():
        old = previous.get(path)
        if old is None:
            added.append(path)
        elif old.get("sha256") != item.get("sha256") or old.get("missing") is True:
            modified.append(path)
        else:
            unchanged.append(path)
    deleted = sorted(set(previous) - set(current))
    return {
        "added": sorted(added),
        "modified": sorted(modified),
        "deleted": deleted,
        "unchanged": sorted(unchanged),
    }


def scan_libraries(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    libraries = resolve_libraries(
        config,
        arguments.get("library_ids"),
        caller_user=caller_user,
    )
    state = _load_state()
    rows: list[dict[str, Any]] = []
    for library in libraries:
        if library.kind != "portable":
            rows.append({
                "library_id": library.id,
                "ok": True,
                "status": "skipped",
                "reason": "service_default 由 kemo-graph 自身管理文档",
            })
            continue
        if not library.source_roots:
            rows.append({
                "library_id": library.id,
                "ok": True,
                "status": "uploaded_only",
                "summary": {"added": 0, "modified": 0, "deleted": 0, "unchanged": 0},
            })
            continue
        unavailable = unavailable_source_roots(library)
        if unavailable:
            rows.append(_source_unavailable_row(library, unavailable, sync=False))
            continue
        try:
            previous, signature_matches = _previous_files(state, library)
            current = _library_files(library).files
            changes = _diff(current, previous)
            state_libraries = state.get("libraries") if isinstance(state.get("libraries"), dict) else {}
            registry_changed = (
                isinstance(state_libraries.get(library.id), dict)
                and not signature_matches
            )
            rows.append({
                "library_id": library.id,
                "ok": True,
                "status": "scanned",
                "registry_changed": registry_changed,
                "summary": {key: len(value) for key, value in changes.items()},
                "changes": {key: value[:50] for key, value in changes.items() if key != "unchanged"},
                "truncated": any(len(value) > 50 for key, value in changes.items() if key != "unchanged"),
            })
        except Exception as exc:
            rows.append({
                "library_id": library.id,
                "ok": False,
                "status": "error",
                "error": str(exc)[:500],
            })
    return {"ok": all(bool(row.get("ok")) for row in rows), "libraries": rows}


def _extract_result(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = data.get("result", data)
    return result if isinstance(result, dict) else {}


def _snapshot_failure_row(
    library: GraphLibrary,
    error: Exception,
    *,
    path: str,
    imported: int,
    failures: list[dict[str, str]],
    pending_deletions: int,
) -> dict[str, Any]:
    all_failures = [
        *failures,
        {"path": path, "error": str(error)[:500]},
    ]
    return {
        "library_id": library.id,
        "ok": False,
        "status": "error",
        "reason": (
            "本地来源快照或远端导入确认失效；已停止后续远端操作，"
            "仅记录 kemo-graph 已用哈希确认的成功导入"
        ),
        "imported": imported,
        "deleted": 0,
        "deletions_pending_confirmation": pending_deletions,
        "failed": len(all_failures),
        "failures": all_failures[:20],
    }


def _persist_library_cursor(
    state: dict[str, Any],
    state_libraries: dict[str, Any],
    library: GraphLibrary,
    initialized: Any,
    files: dict[str, dict[str, Any]],
) -> None:
    initialized_result = _extract_result(initialized)
    manifest = (
        initialized_result.get("manifest")
        if isinstance(initialized_result.get("manifest"), dict)
        else {}
    )
    state_libraries[library.id] = {
        "registry_signature": library_signature(library),
        "store_id": manifest.get("store_id"),
        "files": files,
    }
    atomic_json(SYNC_STATE_PATH, state)


def _confirmed_import_metadata(
    result: dict[str, Any],
    snapshot: _FileSnapshot,
) -> tuple[str, str]:
    origin_hash = result.get("origin_hash")
    if (
        not isinstance(origin_hash, str)
        or origin_hash.casefold() != snapshot.sha256
    ):
        raise _SnapshotInvalidated(
            f"kemo-graph 未确认导入的是已扫描文件内容：{snapshot.logical}"
        )
    source_id = result.get("source_id")
    if (
        not isinstance(source_id, str)
        or not source_id.strip()
        or len(source_id) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in source_id)
    ):
        raise _SnapshotInvalidated(
            f"kemo-graph 导入响应缺少有效 source_id：{snapshot.logical}"
        )
    relative_path = result.get("markdown_relative_path") or result.get("relative_path")
    if (
        not isinstance(relative_path, str)
        or not relative_path.strip()
        or len(relative_path) > 4096
        or Path(relative_path).is_absolute()
        or ".." in relative_path.replace("\\", "/").split("/")
        or any(
            ord(character) < 32 or ord(character) == 127
            for character in relative_path
        )
    ):
        raise _SnapshotInvalidated(
            f"kemo-graph 导入响应缺少有效 relative_path：{snapshot.logical}"
        )
    return source_id.strip(), relative_path.strip()


def _confirmed_delete_result(
    result: dict[str, Any],
    expected_source_ids: list[str],
) -> tuple[set[str], list[dict[str, Any]]]:
    requested = result.get("requested")
    deleted_count = result.get("deleted")
    failed_count = result.get("failed")
    counts = (requested, deleted_count, failed_count)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in counts):
        raise GraphExpandError("批量删除响应缺少有效 requested/deleted/failed 计数")
    if any(value < 0 for value in counts):
        raise GraphExpandError("批量删除响应包含负数计数")
    if requested != len(expected_source_ids) or deleted_count + failed_count != requested:
        raise GraphExpandError("批量删除响应计数与请求不一致")

    deleted_rows = result.get("documents")
    failure_rows = result.get("failures")
    if not isinstance(deleted_rows, list) or not isinstance(failure_rows, list):
        raise GraphExpandError("批量删除响应缺少 documents/failures 明细")

    def source_ids(rows: list[Any], *, field: str) -> list[str]:
        values: list[str] = []
        for item in rows:
            source_id = item.get("source_id") if isinstance(item, dict) else None
            if (
                not isinstance(source_id, str)
                or not source_id.strip()
                or len(source_id) > 512
            ):
                raise GraphExpandError(f"批量删除响应 {field} 含无效 source_id")
            values.append(source_id.strip())
        if len(set(values)) != len(values):
            raise GraphExpandError(f"批量删除响应 {field} 含重复 source_id")
        return values

    deleted_ids = source_ids(deleted_rows, field="documents")
    failed_ids = source_ids(failure_rows, field="failures")
    if len(deleted_ids) != deleted_count or len(failed_ids) != failed_count:
        raise GraphExpandError("批量删除响应计数与明细不一致")
    expected = set(expected_source_ids)
    if set(deleted_ids) & set(failed_ids) or set(deleted_ids) | set(failed_ids) != expected:
        raise GraphExpandError("批量删除响应明细未完整覆盖请求 source_id")
    return set(deleted_ids), failure_rows


def sync_libraries(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    confirm_deletions = arguments.get("confirm_deletions", False)
    if not isinstance(confirm_deletions, bool):
        raise GraphExpandError("confirm_deletions 必须是布尔值")
    libraries = resolve_libraries(
        config,
        arguments.get("library_ids"),
        caller_user=caller_user,
    )
    state = _load_state()
    state_libraries = state.setdefault("libraries", {})
    rows: list[dict[str, Any]] = []
    for library in libraries:
        if library.kind != "portable":
            rows.append({
                "library_id": library.id,
                "ok": True,
                "status": "skipped",
                "reason": "service_default 由 kemo-graph 自身管理文档",
            })
            continue
        if not library.source_roots:
            rows.append({
                "library_id": library.id,
                "ok": True,
                "status": "uploaded_only",
                "imported": 0,
                "deleted": 0,
                "failed": 0,
            })
            continue
        unavailable = unavailable_source_roots(library)
        if unavailable:
            rows.append(_source_unavailable_row(library, unavailable, sync=True))
            continue
        try:
            previous, _ = _previous_files(state, library)
            scan = _library_files(library)
            current = scan.files
            changes = _diff(current, previous)
            _verify_scan(scan)
            initialized = api_request(config, "/stores/initialize", {
                "store_root": library.store_root,
                "scope": library.scope,
                "owner_id": library.owner_id,
                "display_name": library.display_name,
            })
        except Exception as exc:
            rows.append({
                "library_id": library.id,
                "ok": False,
                "status": "error",
                "imported": 0,
                "deleted": 0,
                "failed": 1,
                "failures": [{"path": "<initialize-or-scan>", "error": str(exc)[:500]}],
            })
            continue
        next_files = {path: dict(item) for path, item in previous.items()}
        imported = 0
        deleted = 0
        failures: list[dict[str, str]] = []
        snapshot_failure: tuple[str, _SnapshotInvalidated] | None = None
        for path in changes["added"] + changes["modified"]:
            try:
                snapshot = scan.file_snapshots[path]
                _read_verified_file(
                    snapshot.root,
                    snapshot.directory,
                    snapshot.logical,
                    expected=snapshot.state,
                    expected_sha256=snapshot.sha256,
                )
                data = api_request(config, "/stores/import-path", {
                    "store_root": library.store_root,
                    "path": path,
                    "ingest_after_import": False,
                    "expected_origin_hash": snapshot.sha256,
                }, timeout=max(config.timeout_seconds, 120))
                result = _extract_result(data)
                source_id, relative_path = _confirmed_import_metadata(
                    result,
                    snapshot,
                )
                next_files[path] = {
                    **current[path],
                    "source_id": source_id,
                    "relative_path": relative_path,
                    "missing": False,
                }
                imported += 1
                _verify_file(snapshot)
            except _SnapshotInvalidated as exc:
                snapshot_failure = (path, exc)
                break
            except Exception as exc:
                failures.append({"path": path, "error": str(exc)[:500]})
        if snapshot_failure is not None:
            failed_path, error = snapshot_failure
            if imported:
                _persist_library_cursor(
                    state,
                    state_libraries,
                    library,
                    initialized,
                    next_files,
                )
            rows.append(_snapshot_failure_row(
                library,
                error,
                path=failed_path,
                imported=imported,
                failures=failures,
                pending_deletions=len(changes["deleted"]),
            ))
            continue
        for path in changes["unchanged"]:
            next_files[path] = {**previous[path], "missing": False}
        missing = changes["deleted"]
        if missing and confirm_deletions:
            try:
                _verify_scan(scan)
            except _SnapshotInvalidated as exc:
                if imported:
                    _persist_library_cursor(
                        state,
                        state_libraries,
                        library,
                        initialized,
                        next_files,
                    )
                rows.append(_snapshot_failure_row(
                    library,
                    exc,
                    path="<source-snapshot-before-delete>",
                    imported=imported,
                    failures=failures,
                    pending_deletions=len(missing),
                ))
                continue
            deletable = [
                str(previous[path].get("source_id"))
                for path in missing
                if previous[path].get("source_id")
            ]
            unresolved = [path for path in missing if not previous[path].get("source_id")]
            if deletable:
                try:
                    data = api_request(config, "/stores/documents/delete-batch", {
                        "store_root": library.store_root,
                        "source_ids": deletable,
                    })
                    result = _extract_result(data)
                    deleted_ids, failure_rows = _confirmed_delete_result(
                        result,
                        deletable,
                    )
                    for path in missing:
                        if str(previous[path].get("source_id") or "") in deleted_ids:
                            next_files.pop(path, None)
                            deleted += 1
                    for item in failure_rows:
                        failures.append({
                            "path": str(item.get("source_id") or "<delete-batch>"),
                            "error": str(
                                item.get("message")
                                or item.get("error_type")
                                or "删除失败"
                            )[:500],
                        })
                except Exception as exc:
                    failures.append({"path": "<delete-batch>", "error": str(exc)[:500]})
            for path in unresolved:
                next_files[path] = {**previous[path], "missing": True}
                failures.append({"path": path, "error": "缺少 source_id，未传播删除"})
        else:
            for path in missing:
                next_files[path] = {**previous[path], "missing": True}
        for path in missing:
            if path in next_files:
                next_files[path] = {**next_files[path], "missing": True}
        pending_deletions = sum(path in next_files for path in missing)
        _persist_library_cursor(
            state,
            state_libraries,
            library,
            initialized,
            next_files,
        )
        rows.append({
            "library_id": library.id,
            "ok": not failures,
            "status": "synced" if not failures else "partial",
            "imported": imported,
            "deleted": deleted,
            "deletions_pending_confirmation": pending_deletions,
            "unchanged": len(changes["unchanged"]),
            "failed": len(failures),
            "failures": failures[:20],
        })
    return {
        "ok": all(bool(row.get("ok")) for row in rows),
        "libraries": rows,
    }
