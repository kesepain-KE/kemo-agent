"""Kemo Graph library workflow implementation."""
from __future__ import annotations
import json
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from client import MAX_UPLOAD_BYTES, api_request, api_upload_file, verify_service
from errors import GraphExpandError, error_metadata
from registry import (GraphConfig, GraphLibrary, GRAPH_ARTIFACT_DIR, QUERY_ARTIFACT_DIR, STATUS_PATH, atomic_json, atomic_text, integer, library_signature, resolve_libraries)


def _result(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = data.get("result", data)
    return result if isinstance(result, dict) else {}

def _count(value: dict[str, Any], *path: str) -> int:
    current: Any = value
    for key in path:
        current = current.get(key) if isinstance(current, dict) else None
    return int(current) if isinstance(current, int) and not isinstance(current, bool) else 0

def _portable_document_health(
    config: GraphConfig,
    library: GraphLibrary,
) -> tuple[int, int, int]:
    failed = processing = pending = 0
    page = 1
    while True:
        data = api_request(config, "/stores/documents/list", {
            "store_root": library.store_root,
            "status": "active",
            "page": page,
            "page_size": 100,
        })
        result = _result(data)
        documents = result.get("documents")
        if not isinstance(documents, list):
            raise GraphExpandError(f"{library.id} 文档状态响应不符合协议")
        for item in documents:
            if not isinstance(item, dict):
                continue
            states = (item.get("graph_status"), item.get("rag_status"))
            if "failed" in states:
                failed += 1
            elif "processing" in states:
                processing += 1
            elif any(state != "ready" for state in states):
                pending += 1
        pagination = result.get("pagination") if isinstance(result.get("pagination"), dict) else {}
        total_pages = pagination.get("total_pages", 1)
        if not isinstance(total_pages, int) or isinstance(total_pages, bool) or total_pages < 0:
            raise GraphExpandError(f"{library.id} 文档分页响应不符合协议")
        if page >= total_pages:
            return failed, processing, pending
        page += 1

def _default_document_health(config: GraphConfig) -> tuple[int, int, int]:
    failed = processing = pending = 0
    page = 1
    while True:
        data = api_request(
            config,
            "/documents",
            method="GET",
            query={"status": "active", "page": page, "page_size": 100},
        )
        result = _result(data)
        documents = result.get("documents")
        if not isinstance(documents, list):
            raise GraphExpandError("内置文档库状态响应不符合协议")
        for item in documents:
            if not isinstance(item, dict):
                continue
            states = (item.get("graph_status"), item.get("rag_status"))
            if "failed" in states:
                failed += 1
            elif "processing" in states:
                processing += 1
            elif any(state != "ready" for state in states):
                pending += 1
        pagination = result.get("pagination") if isinstance(result.get("pagination"), dict) else {}
        total_pages = pagination.get("total_pages", 1)
        if not isinstance(total_pages, int) or isinstance(total_pages, bool) or total_pages < 0:
            raise GraphExpandError("内置文档库分页响应不符合协议")
        if page >= total_pages:
            return failed, processing, pending
        page += 1

def _library_status(
    result: dict[str, Any],
    *,
    failed: int,
    processing: int,
    document_pending: int,
) -> tuple[str, int, int]:
    sources = result.get("sources") if isinstance(result.get("sources"), dict) else {}
    rag = result.get("rag") if isinstance(result.get("rag"), dict) else {}
    pending_graph = _count(sources, "pending_graph")
    pending_rag = _count(sources, "pending_rag")
    total_sources = _count(sources, "total")
    if result.get("initialized") is not True:
        status = "not_initialized"
    elif processing:
        status = "processing"
    elif failed:
        status = "degraded"
    elif total_sources == 0:
        status = "empty"
    elif rag.get("faiss_healthy") is False:
        status = "degraded"
    elif pending_graph or pending_rag or document_pending:
        status = "pending"
    else:
        status = "ready"
    return status, pending_graph, pending_rag

def _load_status_snapshot() -> dict[str, Any]:
    if not STATUS_PATH.is_file():
        return {}
    try:
        value = json.loads(STATUS_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or not isinstance(value.get("libraries"), list):
        return {}
    return value

def _status_rows_by_id(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("libraries")
    return {
        str(row.get("id")): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    } if isinstance(rows, list) else {}

def _status_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "libraries": len(rows),
        "connected": sum(bool(row.get("connected")) for row in rows),
        "ready": sum(row.get("status") == "ready" for row in rows),
        "empty": sum(row.get("status") == "empty" for row in rows),
        "not_initialized": sum(
            row.get("status") == "not_initialized" for row in rows
        ),
        "pending": sum(
            row.get("status") in {"pending", "processing"} for row in rows
        ),
        "failed": sum(row.get("status") in {"degraded", "error"} for row in rows),
    }

def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None

def _last_success_summary(
    row: dict[str, Any],
    *,
    fallback_checked_at: str,
) -> dict[str, Any] | None:
    previous = row.get("last_success")
    if isinstance(previous, dict):
        active = _nonnegative_int(previous.get("active"))
        total = _nonnegative_int(previous.get("total"))
        if active is not None and total is not None:
            return {
                "checked_at": str(previous.get("checked_at") or fallback_checked_at),
                "status": str(previous.get("status") or "unknown"),
                "active": active,
                "total": total,
                "failed": _nonnegative_int(previous.get("failed")) or 0,
                "processing": _nonnegative_int(previous.get("processing")) or 0,
                "pending": _nonnegative_int(previous.get("pending")) or 0,
            }
    if row.get("connected") is not True:
        return None
    result = row.get("result")
    sources = result.get("sources") if isinstance(result, dict) else None
    if not isinstance(sources, dict):
        return None
    active = _nonnegative_int(sources.get("active"))
    total = _nonnegative_int(sources.get("total"))
    if active is None or total is None:
        return None
    return {
        "checked_at": str(row.get("checked_at") or fallback_checked_at),
        "status": str(row.get("status") or "unknown"),
        "active": active,
        "total": total,
        "failed": _nonnegative_int(row.get("document_failures")) or 0,
        "processing": _nonnegative_int(row.get("document_processing")) or 0,
        "pending": _nonnegative_int(row.get("document_pending")) or 0,
    }

def status_libraries(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    service = verify_service(config)
    previous_snapshot = _load_status_snapshot()
    previous_rows = _status_rows_by_id(previous_snapshot)
    if previous_snapshot.get("base_url") != config.base_url:
        previous_rows = {}
    previous_generated_at = str(previous_snapshot.get("generated_at") or "")
    libraries = resolve_libraries(
        config,
        arguments.get("library_ids"),
        caller_user=caller_user,
    )
    rows: list[dict[str, Any]] = []
    for library in libraries:
        try:
            if library.kind == "service_default":
                result = _result(service)
                failed = processing = document_pending = 0
                if result.get("initialized") is True:
                    failed, processing, document_pending = _default_document_health(config)
                status, pending_graph, pending_rag = _library_status(
                    result,
                    failed=failed,
                    processing=processing,
                    document_pending=document_pending,
                )
                rows.append({
                    **library.public_dict(),
                    "registry_signature": library_signature(library),
                    "connected": True,
                    "status": status,
                    "pending_graph": pending_graph,
                    "pending_rag": pending_rag,
                    "document_failures": failed,
                    "document_processing": processing,
                    "document_pending": document_pending,
                    "result": result,
                })
                continue
            data = api_request(config, "/stores/status", {"store_root": library.store_root})
            result = _result(data)
            failed = processing = document_pending = 0
            if result.get("initialized") is True:
                failed, processing, document_pending = _portable_document_health(config, library)
            status, pending_graph, pending_rag = _library_status(
                result,
                failed=failed,
                processing=processing,
                document_pending=document_pending,
            )
            rows.append({
                **library.public_dict(),
                "registry_signature": library_signature(library),
                "connected": True,
                "status": status,
                "pending_graph": pending_graph,
                "pending_rag": pending_rag,
                "document_failures": failed,
                "document_processing": processing,
                "document_pending": document_pending,
                "result": result,
            })
        except Exception as exc:
            row = {
                **library.public_dict(),
                "registry_signature": library_signature(library),
                "connected": False,
                "status": "error",
                "error": str(exc),
                **error_metadata(exc),
            }
            previous = previous_rows.get(library.id)
            if (
                isinstance(previous, dict)
                and previous.get("registry_signature") == library_signature(library)
            ):
                last_success = _last_success_summary(
                    previous,
                    fallback_checked_at=previous_generated_at,
                )
                if last_success is not None:
                    row["last_success"] = last_success
            rows.append(row)
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for row in rows:
        row["checked_at"] = checked_at
    snapshot = {
        "schema_version": 2,
        "object": "kemo.graph_sidecar_status",
        "generated_at": checked_at,
        "base_url": config.base_url,
        "summary": _status_summary(rows),
        "libraries": rows,
    }
    configured = {library.id: library for library in config.libraries}
    persisted_by_id: dict[str, dict[str, Any]] = {}
    for library_id, previous in previous_rows.items():
        library = configured.get(library_id)
        if (
            library is None
            or previous.get("registry_signature") != library_signature(library)
        ):
            continue
        preserved = dict(previous)
        preserved.setdefault("checked_at", previous_generated_at)
        persisted_by_id[library_id] = preserved
    persisted_by_id.update({str(row["id"]): row for row in rows})
    persisted_rows = [
        persisted_by_id[library.id]
        for library in config.libraries
        if library.id in persisted_by_id
    ]
    atomic_json(
        STATUS_PATH,
        {
            **snapshot,
            "summary": _status_summary(persisted_rows),
            "libraries": persisted_rows,
        },
    )
    return {"ok": all(row.get("status") != "error" for row in rows), **snapshot}

def initialize_libraries(
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
    rows: list[dict[str, Any]] = []
    for library in libraries:
        if library.kind == "service_default":
            rows.append({"library_id": library.id, "ok": True, "status": "managed_by_service"})
            continue
        try:
            data = api_request(config, "/stores/initialize", {
                "store_root": library.store_root,
                "scope": library.scope,
                "owner_id": library.owner_id,
                "display_name": library.display_name,
            })
            rows.append({"library_id": library.id, "ok": True, "data": data})
        except Exception as exc:
            rows.append({
                "library_id": library.id,
                "ok": False,
                "error": str(exc),
                **error_metadata(exc),
            })
    return {"ok": all(row["ok"] for row in rows), "libraries": rows}
