"""Explicit Kemo Graph sidecar operations; no scheduler calls this module."""

from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from client import api_request, verify_service
from errors import GraphExpandError
from registry import (
    GraphConfig,
    GraphLibrary,
    QUERY_ARTIFACT_DIR,
    STATUS_PATH,
    atomic_json,
    atomic_text,
    integer,
    library_signature,
    resolve_libraries,
)


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


def status_libraries(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    service = verify_service(config)
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
            rows.append({
                **library.public_dict(),
                "registry_signature": library_signature(library),
                "connected": False,
                "status": "error",
                "error": str(exc),
            })
    snapshot = {
        "schema_version": 2,
        "object": "kemo.graph_sidecar_status",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base_url": config.base_url,
        "summary": {
            "libraries": len(rows),
            "connected": sum(bool(row.get("connected")) for row in rows),
            "ready": sum(row.get("status") == "ready" for row in rows),
            "empty": sum(row.get("status") == "empty" for row in rows),
            "not_initialized": sum(row.get("status") == "not_initialized" for row in rows),
            "pending": sum(row.get("status") in {"pending", "processing"} for row in rows),
            "failed": sum(row.get("status") in {"degraded", "error"} for row in rows),
        },
        "libraries": rows,
    }
    atomic_json(STATUS_PATH, snapshot)
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
            rows.append({"library_id": library.id, "ok": False, "error": str(exc)})
    return {"ok": all(row["ok"] for row in rows), "libraries": rows}


def _query_payload(arguments: dict[str, Any], mode: str) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise GraphExpandError("query 不能为空")
    force = arguments.get("force", False)
    if not isinstance(force, bool):
        raise GraphExpandError("force 必须是布尔值")
    top_k = integer(arguments.get("top_k"), field="top_k", default=20, minimum=1, maximum=100)
    depth = integer(
        arguments.get("graph_depth"),
        field="graph_depth",
        default=2,
        minimum=1,
        maximum=10,
    )
    if mode == "graph":
        return {"query": query, "depth": depth, "direction": "both", "confidence": None, "force": force}
    if mode == "rag":
        return {"query": query, "top_k": top_k, "threshold": None, "force": force}
    if mode in {"hybrid", "answer"}:
        return {
            "query": query,
            "graph_depth": depth,
            "rag_top_k": top_k,
            "graph_confidence": None,
            "rag_threshold": None,
            "direction": "both",
            "force": force,
        }
    return {"query": query, "top_k": top_k, "force": force}


def _service_default_query_payload(
    payload: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = {key: value for key, value in payload.items() if key != "force"}
    if mode in {"hybrid", "answer"}:
        body.pop("direction", None)
    return body, {"force": payload.get("force", False)}


def query_libraries(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    mode = str(arguments.get("mode") or "hybrid").strip().casefold()
    if mode not in {"graph", "rag", "hybrid", "answer", "global"}:
        raise GraphExpandError("mode 只允许 graph、rag、hybrid、answer、global")
    libraries = resolve_libraries(
        config,
        arguments.get("library_ids"),
        caller_user=caller_user,
    )
    if not libraries:
        raise GraphExpandError("没有可查询的图谱库")
    payload = _query_payload(arguments, mode)
    portable = [library for library in libraries if library.kind == "portable"]
    defaults = [library for library in libraries if library.kind == "service_default"]
    results: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if len(portable) > 1:
        data = api_request(config, "/stores/query/federated", {
            "store_roots": [library.store_root for library in portable],
            "query": payload["query"],
            "mode": mode,
            "top_k": payload.get("rag_top_k") or payload.get("top_k", 20),
            "graph_depth": payload.get("graph_depth") or payload.get("depth", 2),
            "force": payload.get("force", False),
        }, timeout=config.ingest_timeout_seconds if mode in {"answer", "global"} else 300)
        results.append({"library_ids": [library.id for library in portable], "data": data})
        federated = _result(data)
        failed_stores = federated.get("stores_failed")
        if isinstance(failed_stores, list) and failed_stores:
            warnings.append({
                "library_ids": [library.id for library in portable],
                "type": "federated_partial_failure",
                "stores_failed": failed_stores,
            })
    else:
        for library in portable:
            data = api_request(
                config,
                f"/stores/query/{mode}",
                {"store_root": library.store_root, **payload},
                timeout=config.ingest_timeout_seconds if mode in {"answer", "global"} else 300,
            )
            results.append({"library_ids": [library.id], "data": data})
    for library in defaults:
        body, query_params = _service_default_query_payload(payload, mode)
        data = api_request(
            config,
            f"/query/{mode}",
            body,
            query=query_params,
            timeout=config.ingest_timeout_seconds if mode in {"answer", "global"} else 300,
        )
        results.append({"library_ids": [library.id], "data": data})
    rendered = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    omitted = len(rendered) > 14_000
    response = {
        "ok": True,
        "query": payload["query"],
        "mode": mode,
        "library_ids": [library.id for library in libraries],
        "inline": None if omitted else results,
        "result_chars": len(rendered),
        "result_omitted": omitted,
        "partial": bool(warnings),
        "warnings": warnings,
    }
    if omitted:
        artifact = QUERY_ARTIFACT_DIR / f"query-{uuid.uuid4().hex}.json"
        atomic_text(artifact, rendered + "\n")
        response["artifacts"] = [{
            "path": artifact.relative_to(QUERY_ARTIFACT_DIR.parents[1]).as_posix(),
            "kind": "file",
            "name": artifact.name,
        }]
    return response


def ingest_library(
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
    if len(libraries) != 1:
        raise GraphExpandError("ingest 每次必须明确选择一个 library_id")
    mode = str(arguments.get("mode") or "both").strip().casefold()
    if mode not in {"graph", "rag", "both"}:
        raise GraphExpandError("mode 只允许 graph、rag、both")
    library = libraries[0]
    payload: dict[str, Any] = {"paths": None, "mode": mode}
    endpoint = "/ingest"
    if library.kind == "portable":
        endpoint = "/stores/ingest"
        payload["store_root"] = library.store_root
    data = api_request(config, endpoint, payload, timeout=config.ingest_timeout_seconds)
    result = _result(data)
    failed = result.get("failed")
    if not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
        raise GraphExpandError("知识整理响应缺少有效 failed 计数")
    return {
        "ok": failed == 0,
        "library_id": library.id,
        "failed": failed,
        "data": data,
        "details": result.get("details") if isinstance(result.get("details"), list) else [],
        "error": f"知识整理存在 {failed} 个失败项" if failed else None,
    }


def upload_markdown(
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
    if len(libraries) != 1:
        raise GraphExpandError("upload 每次必须明确选择一个 library_id")
    filename = str(arguments.get("filename") or "").strip()
    content = arguments.get("content")
    if not filename or any(part in filename for part in ("/", "\\", "..")):
        raise GraphExpandError("filename 必须是不含路径的文件名")
    if not isinstance(content, str) or not content.strip():
        raise GraphExpandError("content 必须是非空 Markdown 文本")
    if len(content) > 200_000:
        raise GraphExpandError("content 超过 200,000 字符，请改用注册 source_roots 后执行 sync")
    library = libraries[0]
    payload = {"filename": filename, "content": content}
    endpoint = "/upload"
    if library.kind == "portable":
        endpoint = "/stores/upload"
        payload["store_root"] = library.store_root
    data = api_request(config, endpoint, payload)
    return {"ok": True, "library_id": library.id, "ingest_started": False, "data": data}


def document_operation(
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
    if len(libraries) != 1:
        raise GraphExpandError("documents 每次必须明确选择一个 library_id")
    library = libraries[0]
    action = str(arguments.get("action") or "list").strip().casefold()
    source_id = str(arguments.get("source_id") or "").strip()
    if library.kind == "portable":
        base = "/stores/documents"
        root_payload = {"store_root": library.store_root}
        if action == "list":
            data = api_request(config, f"{base}/list", {
                **root_payload,
                "status": arguments.get("status", "active"),
                "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                "page_size": integer(arguments.get("page_size"), field="page_size", default=20, minimum=1, maximum=100),
            })
        elif action == "content":
            if not source_id:
                raise GraphExpandError("content 需要 source_id")
            data = api_request(config, f"{base}/content", {**root_payload, "source_id": source_id})
        elif action == "update":
            if not source_id or not isinstance(arguments.get("content"), str):
                raise GraphExpandError("update 需要 source_id 和 content")
            data = api_request(config, f"{base}/update", {
                **root_payload,
                "source_id": source_id,
                "content": arguments["content"],
                "expected_content_hash": arguments.get("expected_content_hash"),
            })
        elif action == "delete":
            if not source_id or arguments.get("confirm") != "delete":
                raise GraphExpandError("delete 需要 source_id 和 confirm='delete'")
            data = api_request(config, f"{base}/delete", {**root_payload, "source_id": source_id})
        else:
            raise GraphExpandError("documents.action 只允许 list、content、update、delete")
    else:
        escaped = urllib.parse.quote(source_id, safe="")
        if action == "list":
            data = api_request(config, "/documents", method="GET", query={
                "status": arguments.get("status", "active"),
                "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                "page_size": integer(arguments.get("page_size"), field="page_size", default=20, minimum=1, maximum=100),
            })
        elif action == "content":
            if not source_id:
                raise GraphExpandError("content 需要 source_id")
            data = api_request(config, f"/documents/{escaped}/content", method="GET")
        elif action == "update":
            if not source_id or not isinstance(arguments.get("content"), str):
                raise GraphExpandError("update 需要 source_id 和 content")
            data = api_request(config, f"/documents/{escaped}/content", {
                "content": arguments["content"],
                "expected_content_hash": arguments.get("expected_content_hash"),
            }, method="PUT")
        elif action == "delete":
            if not source_id or arguments.get("confirm") != "delete":
                raise GraphExpandError("delete 需要 source_id 和 confirm='delete'")
            data = api_request(config, f"/documents/{escaped}", method="DELETE")
        else:
            raise GraphExpandError("documents.action 只允许 list、content、update、delete")
    return {"ok": True, "library_id": library.id, "action": action, "data": data}


def jobs_operation(
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
    if len(libraries) != 1 or libraries[0].kind != "portable":
        raise GraphExpandError("jobs 目前只支持单个 portable 图谱库")
    library = libraries[0]
    job_id = str(arguments.get("job_id") or "").strip()
    if job_id:
        data = api_request(config, "/stores/jobs/get", {
            "store_root": library.store_root,
            "job_id": job_id,
        })
    else:
        data = api_request(config, "/stores/jobs/list", {
            "store_root": library.store_root,
            "limit": integer(arguments.get("limit"), field="limit", default=100, minimum=1, maximum=1000),
        })
    return {"ok": True, "library_id": library.id, "data": data}
