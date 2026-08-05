"""Explicit, incremental source scanning for registered portable libraries."""

from __future__ import annotations

import hashlib
import json
import os
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
)


SUPPORTED_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xls", ".epub", ".rtf",
    ".md", ".markdown", ".txt", ".log", ".html", ".htm", ".rst", ".csv",
    ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml",
})
MAX_IMPORT_BYTES = 50 * 1024 * 1024


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        isjunction = getattr(os.path, "isjunction", None)
        return bool(isjunction and isjunction(path))
    except OSError:
        return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


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


def _library_files(library: GraphLibrary) -> dict[str, dict[str, Any]]:
    files: dict[str, dict[str, Any]] = {}
    for root_text in library.source_roots:
        root = Path(root_text)
        for current_root, directories, filenames in os.walk(root, followlinks=False):
            base = Path(current_root)
            directories[:] = [
                name
                for name in directories
                if name != "kemo-graph-storage"
                and not name.startswith(".")
                and not _is_link(base / name)
            ]
            for filename in filenames:
                path = base / filename
                if filename.startswith(".") or _is_link(path) or not path.is_file():
                    continue
                if path.suffix.casefold() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                if stat.st_size > MAX_IMPORT_BYTES:
                    continue
                resolved = path.resolve()
                files[str(resolved)] = {
                    "sha256": _sha256_file(resolved),
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "source_root": root_text,
                }
    return files


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
        try:
            previous, signature_matches = _previous_files(state, library)
            current = _library_files(library)
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
        try:
            initialized = api_request(config, "/stores/initialize", {
                "store_root": library.store_root,
                "scope": library.scope,
                "owner_id": library.owner_id,
                "display_name": library.display_name,
            })
            previous, _ = _previous_files(state, library)
            current = _library_files(library)
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
        changes = _diff(current, previous)
        next_files = {path: dict(item) for path, item in previous.items()}
        imported = 0
        deleted = 0
        failures: list[dict[str, str]] = []
        for path in changes["added"] + changes["modified"]:
            try:
                data = api_request(config, "/stores/import-path", {
                    "store_root": library.store_root,
                    "path": path,
                    "ingest_after_import": False,
                }, timeout=max(config.timeout_seconds, 120))
                result = _extract_result(data)
                next_files[path] = {
                    **current[path],
                    "source_id": result.get("source_id"),
                    "relative_path": result.get("markdown_relative_path")
                    or result.get("relative_path"),
                    "missing": False,
                }
                imported += 1
            except Exception as exc:
                failures.append({"path": path, "error": str(exc)[:500]})
        for path in changes["unchanged"]:
            next_files[path] = {**previous[path], "missing": False}
        missing = changes["deleted"]
        if missing and confirm_deletions:
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
                    failed_count = result.get("failed", 0)
                    if not isinstance(failed_count, int) or isinstance(failed_count, bool):
                        raise GraphExpandError("批量删除响应缺少有效 failed 计数")
                    deleted_rows = result.get("documents")
                    deleted_ids = {
                        str(item.get("source_id"))
                        for item in deleted_rows
                        if isinstance(item, dict) and item.get("source_id")
                    } if isinstance(deleted_rows, list) else set()
                    if failed_count == 0 and not deleted_ids:
                        deleted_ids = set(deletable)
                    for path in missing:
                        if str(previous[path].get("source_id") or "") in deleted_ids:
                            next_files.pop(path, None)
                            deleted += 1
                    failure_rows = result.get("failures")
                    if isinstance(failure_rows, list):
                        for item in failure_rows:
                            if not isinstance(item, dict):
                                continue
                            failures.append({
                                "path": str(item.get("source_id") or "<delete-batch>"),
                                "error": str(item.get("message") or item.get("error_type") or "删除失败")[:500],
                            })
                    if failed_count and not isinstance(failure_rows, list):
                        failures.append({
                            "path": "<delete-batch>",
                            "error": f"批量删除存在 {failed_count} 个失败项",
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
        initialized_result = _extract_result(initialized)
        manifest = initialized_result.get("manifest") if isinstance(initialized_result.get("manifest"), dict) else {}
        state_libraries[library.id] = {
            "registry_signature": library_signature(library),
            "store_id": manifest.get("store_id"),
            "files": next_files,
        }
        atomic_json(SYNC_STATE_PATH, state)
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
