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
from graph_operations import _one_library, _require_write_admin


def _document_filters(arguments: dict[str, Any]) -> dict[str, Any]:
    """项目 / 检索 / 构建状态过滤，内置库与 portable 库共用同一套字段。"""
    filters: dict[str, Any] = {}
    project = arguments.get("project")
    if project is not None:
        if not isinstance(project, str):
            raise GraphExpandError("project 必须是字符串")
        # 空字符串表示根目录，"/" 表示全部项目，两者都是合法值。
        if len(project) > 160:
            raise GraphExpandError("project 超过 160 字符")
        filters["project"] = project
    search = arguments.get("search")
    if search is not None:
        if not isinstance(search, str) or not search.strip():
            raise GraphExpandError("search 必须是非空字符串")
        if len(search) > 200:
            raise GraphExpandError("search 超过 200 字符")
        filters["search"] = search.strip()
    for field in ("graph_status", "rag_status"):
        value = arguments.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise GraphExpandError(f"{field} 必须是非空字符串")
        if len(value) > 20:
            raise GraphExpandError(f"{field} 超过 20 字符")
        filters[field] = value.strip()
    include_summary = arguments.get("include_summary")
    if include_summary is not None:
        if not isinstance(include_summary, bool):
            raise GraphExpandError("include_summary 必须是布尔值")
        if include_summary:
            filters["include_summary"] = True
    return filters

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
    if action == "move":
        _require_write_admin(config, caller_user, "documents.move")
        raw_source_ids = arguments.get("source_ids")
        if raw_source_ids is not None:
            if not isinstance(raw_source_ids, list) or not raw_source_ids:
                raise GraphExpandError("source_ids 必须是非空字符串数组")
            source_ids = list(
                dict.fromkeys(
                    str(item).strip()
                    for item in raw_source_ids
                    if isinstance(item, str) and item.strip()
                )
            )
            if len(source_ids) != len(raw_source_ids):
                raise GraphExpandError("source_ids 中不能包含空值或重复值")
            if len(source_ids) > 1000:
                raise GraphExpandError("source_ids 最多允许 1000 项")
        else:
            source_ids = []
        if bool(source_id) == bool(source_ids):
            raise GraphExpandError("move 需要 source_id 或 source_ids，且二者只能提供一个")
        if "project" in arguments:
            project = arguments["project"]
            if not isinstance(project, str) or len(project) > 160:
                raise GraphExpandError("project 必须是不超过 160 字符的字符串")
        else:
            project = None
        filename = arguments.get("filename")
        if filename is not None and (
            not isinstance(filename, str)
            or not filename.strip()
            or len(filename.strip()) > 160
        ):
            raise GraphExpandError("filename 必须是 1..160 字符的字符串")
        expected_relative_path = arguments.get("expected_relative_path")
        if expected_relative_path is not None and not isinstance(expected_relative_path, str):
            raise GraphExpandError("expected_relative_path 必须是字符串")
        if source_ids and project is None:
            raise GraphExpandError("批量 move 需要 project")
        if source_ids and (filename is not None or expected_relative_path is not None):
            raise GraphExpandError("批量 move 不支持 filename 或 expected_relative_path")
        if source_id and filename is None and project is None:
            raise GraphExpandError("单篇 move 至少需要 filename 或 project")
    if library.kind == "portable":
        base = "/stores/documents"
        root_payload = {"store_root": library.store_root}
        if action == "list":
            data = api_request(config, f"{base}/list", {
                **root_payload,
                "status": arguments.get("status", "active"),
                "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                "page_size": integer(arguments.get("page_size"), field="page_size", default=20, minimum=1, maximum=100),
                **_document_filters(arguments),
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
        elif action == "move":
            if source_ids:
                data = api_request(
                    config,
                    f"{base}/move-batch",
                    {**root_payload, "source_ids": source_ids, "project": project},
                )
            else:
                payload = {**root_payload, "source_id": source_id}
                if filename is not None:
                    payload["filename"] = filename.strip()
                if project is not None:
                    payload["project"] = project
                if expected_relative_path is not None:
                    payload["expected_relative_path"] = expected_relative_path
                data = api_request(config, f"{base}/location", payload)
        else:
            raise GraphExpandError("documents.action 只允许 list、content、update、delete、move")
    else:
        escaped = urllib.parse.quote(source_id, safe="")
        if action == "list":
            data = api_request(config, "/documents", method="GET", query={
                "status": arguments.get("status", "active"),
                "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                "page_size": integer(arguments.get("page_size"), field="page_size", default=20, minimum=1, maximum=100),
                **_document_filters(arguments),
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
        elif action == "move":
            if source_ids:
                data = api_request(
                    config,
                    "/documents/move-batch",
                    {"source_ids": source_ids, "project": project},
                )
            else:
                payload = {}
                if filename is not None:
                    payload["filename"] = filename.strip()
                if project is not None:
                    payload["project"] = project
                if expected_relative_path is not None:
                    payload["expected_relative_path"] = expected_relative_path
                data = api_request(
                    config,
                    f"/documents/{escaped}/location",
                    payload,
                    method="PATCH",
                )
        else:
            raise GraphExpandError("documents.action 只允许 list、content、update、delete、move")
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
    if len(libraries) != 1:
        raise GraphExpandError("jobs 每次必须明确选择一个 library_id")
    library = libraries[0]
    job_id = str(arguments.get("job_id") or "").strip()
    limit = integer(arguments.get("limit"), field="limit", default=100, minimum=1, maximum=1000)
    if library.kind == "portable":
        if job_id:
            data = api_request(config, "/stores/jobs/get", {
                "store_root": library.store_root,
                "job_id": job_id,
            })
        else:
            data = api_request(config, "/stores/jobs/list", {
                "store_root": library.store_root,
                "limit": limit,
            })
    elif job_id:
        escaped = urllib.parse.quote(job_id, safe="")
        data = api_request(config, f"/jobs/{escaped}", method="GET")
    else:
        data = api_request(
            config,
            "/jobs",
            method="GET",
            query={"limit": limit},
        )
    return {"ok": True, "library_id": library.id, "data": data}

def project_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """列出或创建所选知识库的一级项目目录。"""
    library = _one_library(
        config,
        arguments,
        caller_user=caller_user,
        operation="projects",
    )
    action = str(arguments.get("action") or "list").strip().casefold()
    if action not in {"list", "create"}:
        raise GraphExpandError("projects.action 只允许 list、create")
    if action == "create":
        _require_write_admin(config, caller_user, "projects.create")
        name = str(arguments.get("name") or "").strip()
        if not name or len(name) > 160:
            raise GraphExpandError("projects.create 需要 1..160 字符的 name")
    else:
        name = ""

    if library.kind == "portable":
        endpoint = f"/stores/projects/{action}"
        payload = {"store_root": library.store_root}
        if action == "create":
            payload["name"] = name
        data = api_request(config, endpoint, payload)
    elif action == "list":
        data = api_request(config, "/projects", method="GET")
    else:
        data = api_request(config, "/projects", {"name": name})
    return {"ok": True, "library_id": library.id, "action": action, "data": data}
