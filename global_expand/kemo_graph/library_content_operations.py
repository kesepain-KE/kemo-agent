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
SUPPORTED_IMPORT_SUFFIXES = frozenset({".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".xls", ".html", ".htm", ".epub", ".rtf", ".eml", ".txt", ".log", ".rst", ".csv", ".tsv", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".xml", ".md", ".markdown"})
MAX_INGEST_PATHS = 1000
MAX_INGEST_PATH_CHARS = 4096
MAX_INGEST_PATH_TOTAL_CHARS = 200_000


def _result(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    result = data.get("result", data)
    return result if isinstance(result, dict) else {}

def _ingest_paths(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise GraphExpandError("paths 必须是非空的字符串数组")
    if len(value) > MAX_INGEST_PATHS:
        raise GraphExpandError(f"paths 最多允许 {MAX_INGEST_PATHS} 项")
    normalized: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise GraphExpandError(f"paths[{index}] 必须是非空字符串")
        path = item.strip()
        if len(path) > MAX_INGEST_PATH_CHARS:
            raise GraphExpandError(
                f"paths[{index}] 超过 {MAX_INGEST_PATH_CHARS} 字符"
            )
        # Bound the submitted payload before deduplication so repeated values
        # cannot bypass the aggregate work limit.
        total_chars += len(path)
        if total_chars > MAX_INGEST_PATH_TOTAL_CHARS:
            raise GraphExpandError(
                f"paths 总长度超过 {MAX_INGEST_PATH_TOTAL_CHARS} 字符"
            )
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized

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
    payload: dict[str, Any] = {
        "paths": _ingest_paths(arguments.get("paths")),
        "mode": mode,
    }
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

def import_file(
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
        raise GraphExpandError("import_file 每次必须明确选择一个 library_id")

    raw_path = arguments.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise GraphExpandError("import_file 需要本地文件绝对路径 path")
    candidate = Path(raw_path.strip()).expanduser()
    if not candidate.is_absolute():
        raise GraphExpandError("import_file.path 必须是绝对路径")
    if candidate.is_symlink():
        raise GraphExpandError("import_file 不接受符号链接")
    try:
        source = candidate.resolve(strict=True)
    except OSError as exc:
        raise GraphExpandError("import_file.path 不存在或无法访问") from exc
    if not source.is_file():
        raise GraphExpandError("import_file.path 必须是普通文件")
    suffix = source.suffix.casefold()
    if suffix not in SUPPORTED_IMPORT_SUFFIXES:
        supported = ", ".join(sorted(SUPPORTED_IMPORT_SUFFIXES))
        raise GraphExpandError(f"import_file 不支持 {suffix or '<无扩展名>'}；允许：{supported}")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise GraphExpandError("无法读取 import_file.path 文件状态") from exc
    if size > MAX_UPLOAD_BYTES:
        raise GraphExpandError("import_file.path 超过 50 MB 上限")

    ingest_after_import = arguments.get("ingest_after_import", False)
    if not isinstance(ingest_after_import, bool):
        raise GraphExpandError("ingest_after_import 必须是布尔值")

    library = libraries[0]
    endpoint = "/import"
    fields: dict[str, str] | None = None
    if library.kind == "portable":
        endpoint = "/stores/import"
        fields = {"store_root": str(library.store_root)}
    data = api_upload_file(
        config,
        endpoint,
        source,
        fields=fields,
        query={"ingest": str(ingest_after_import).lower()},
        timeout=config.ingest_timeout_seconds,
    )
    return {
        "ok": True,
        "library_id": library.id,
        "path": str(source),
        "size": size,
        "import_started": True,
        "ingest_started": ingest_after_import,
        "data": data,
    }
