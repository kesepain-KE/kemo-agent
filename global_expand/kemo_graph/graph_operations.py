"""Explicit Kemo Graph sidecar operations; no scheduler calls this module."""

from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from client import MAX_UPLOAD_BYTES, api_request, api_upload_file, verify_service
from errors import GraphExpandError, error_metadata
from registry import (
    GraphConfig,
    GraphLibrary,
    GRAPH_ARTIFACT_DIR,
    QUERY_ARTIFACT_DIR,
    STATUS_PATH,
    atomic_json,
    atomic_text,
    integer,
    library_signature,
    resolve_libraries,
)


# Mirrors kemo-graph provider/tools/document_tools.py::SUPPORTED_DOCUMENT_SUFFIXES.
# Keep this client-side guard synchronized with the server and retain server-side
# validation as the final authority.
SUPPORTED_IMPORT_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".pptx",
        ".xlsx",
        ".xlsm",
        ".xls",
        ".html",
        ".htm",
        ".epub",
        ".rtf",
        ".eml",
        ".txt",
        ".log",
        ".rst",
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".ndjson",
        ".yaml",
        ".yml",
        ".xml",
        ".md",
        ".markdown",
    }
)
MAX_INGEST_PATHS = 1000
MAX_INGEST_PATH_CHARS = 4096
MAX_INGEST_PATH_TOTAL_CHARS = 200_000



INLINE_DEFAULT_MAX_CHARS = 14_000

def _require_read_admin(config: GraphConfig, caller_user: str | None, operation: str) -> None:
    """触发用户必须是注册表管理员；避免非管理员借机器本地与网络回环绕过 admin_users。"""
    if not config.is_admin(caller_user):
        raise PermissionError(f"只有 admin_users 可以执行 {operation}")


def _require_write_admin(config: GraphConfig, caller_user: str | None, operation: str) -> None:
    """写操作校验发起用户。

    框架调用一定带真实用户标识；caller_user=None 只可能来自本地 CLI 与
    模块合同校验器，属可信本地入口，故保留既有 is_admin(None) 语义。
    """
    if caller_user is not None and caller_user not in config.admin_users:
        raise PermissionError(f"只有 admin_users 可以执行写操作 {operation}")


def _one_library(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None,
    operation: str,
) -> GraphLibrary:
    libraries = resolve_libraries(
        config,
        arguments.get("library_ids"),
        caller_user=caller_user,
    )
    if len(libraries) != 1:
        raise GraphExpandError(f"{operation} 每次必须明确选择一个 library_id")
    return libraries[0]


def _revision(value: Any) -> str | None:
    revision = str(value or "").strip()
    if not revision:
        return None
    if len(revision) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in revision):
        raise GraphExpandError("expected_revision 必须是 64 位十六进制内容版本号")
    return revision


def _identifier(arguments: dict[str, Any], field: str, *, label: str) -> str:
    value = str(arguments.get(field) or "").strip()
    if not value:
        raise GraphExpandError(f"{label} 需要 {field}")
    if len(value) > 255:
        raise GraphExpandError(f"{field} 超过 255 字符")
    return value


def _direction(value: Any) -> str:
    direction = str(value or "both").strip().casefold() or "both"
    if direction not in {"forward", "backward", "both"}:
        raise GraphExpandError("direction 只允许 forward、backward、both")
    return direction


def _emit(payload: Any, *, label: str, inline_max_chars: int = INLINE_DEFAULT_MAX_CHARS) -> dict[str, Any]:
    """大结果不塞主上下文：写入模块内 artifact 后返回索引。"""
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    response: dict[str, Any] = {
        "ok": True,
        "kind": label,
        "result_chars": len(rendered),
    }
    if len(rendered) <= inline_max_chars:
        response["inline"] = payload
        return response
    artifact = GRAPH_ARTIFACT_DIR / f"{label}-{uuid.uuid4().hex}.json"
    atomic_text(artifact, rendered + "\n")
    response["inline"] = None
    response["result_omitted"] = True
    response["artifacts"] = [
        {
            "path": artifact.relative_to(GRAPH_ARTIFACT_DIR.parents[1]).as_posix(),
            "kind": "file",
            "name": artifact.name,
        }
    ]
    return response


def graph_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """图谱结构与可视化读取；只读，不调用 LLM、Embedding 或 Rerank，按库 ACL 授权。"""
    library = _one_library(config, arguments, caller_user=caller_user, operation="graph")
    action = str(arguments.get("action") or "full").strip().casefold()
    node_id = str(arguments.get("node_id") or "").strip()
    if library.kind == "portable":
        root = {"store_root": library.store_root}
        if action == "full":
            payload = {
                **root,
                "nodes_page": None
                if arguments.get("nodes_page") in (None, "")
                else integer(arguments.get("nodes_page"), field="nodes_page", default=1, minimum=1, maximum=100000),
                "nodes_page_size": integer(
                    arguments.get("nodes_page_size"),
                    field="nodes_page_size",
                    default=100,
                    minimum=1,
                    maximum=1000,
                ),
            }
            data = api_request(config, "/stores/graph/full", payload, timeout=300)
        elif action in {"visualization_meta", "visualization_nodes", "visualization_edges"}:
            suffix = action.split("_", 1)[1]
            is_nodes = suffix == "nodes"
            payload = {
                **root,
                "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                "page_size": integer(
                    arguments.get("page_size"),
                    field="page_size",
                    default=1000 if is_nodes else 2000,
                    minimum=1,
                    maximum=10000 if is_nodes else 10000,
                ),
            }
            revision = _revision(arguments.get("expected_revision"))
            if revision:
                payload["expected_revision"] = revision
            if suffix == "meta":
                payload = dict(root)
            data = api_request(config, f"/stores/graph/visualization/{suffix}", payload, timeout=300)
        elif action == "neighborhood":
            if not node_id:
                raise GraphExpandError("neighborhood 需要 node_id")
            payload = {
                **root,
                "node_id": node_id,
                "depth": integer(arguments.get("depth"), field="depth", default=2, minimum=1, maximum=10),
                "direction": _direction(arguments.get("direction")),
                "limit": integer(arguments.get("limit"), field="limit", default=2000, minimum=1, maximum=10000),
                "edge_limit": integer(
                    arguments.get("edge_limit"),
                    field="edge_limit",
                    default=10000,
                    minimum=1,
                    maximum=50000,
                ),
            }
            revision = _revision(arguments.get("expected_revision"))
            if revision:
                payload["expected_revision"] = revision
            data = api_request(config, "/stores/graph/neighborhood", payload, timeout=300)
        else:
            raise GraphExpandError(
                "graph.action 只允许 full、visualization_meta、visualization_nodes、visualization_edges、neighborhood"
            )
    else:
        if action == "full":
            nodes_page = arguments.get("nodes_page")
            data = api_request(
                config,
                "/graph",
                method="GET",
                query={
                    "nodes_page": None
                    if nodes_page in (None, "")
                    else integer(nodes_page, field="nodes_page", default=1, minimum=1, maximum=100000),
                    "nodes_page_size": integer(
                        arguments.get("nodes_page_size"),
                        field="nodes_page_size",
                        default=100,
                        minimum=1,
                        maximum=1000,
                    ),
                },
                timeout=300,
            )
        elif action in {"visualization_meta", "visualization_nodes", "visualization_edges"}:
            suffix = action.split("_", 1)[1]
            if suffix == "meta":
                data = api_request(
                    config,
                    "/graph/visualization/meta",
                    method="GET",
                    query=None,
                    timeout=300,
                )
            else:
                is_nodes = suffix == "nodes"
                data = api_request(
                    config,
                    f"/graph/visualization/{suffix}",
                    method="GET",
                    query={
                        "page": integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000),
                        "page_size": integer(
                            arguments.get("page_size"),
                            field="page_size",
                            default=1000 if is_nodes else 2000,
                            minimum=1,
                            maximum=5000 if is_nodes else 10000,
                        ),
                        "expected_revision": _revision(arguments.get("expected_revision")),
                    },
                    timeout=300,
                )
        elif action == "neighborhood":
            if not node_id:
                raise GraphExpandError("neighborhood 需要 node_id")
            escaped = urllib.parse.quote(node_id, safe="")
            data = api_request(
                config,
                f"/graph/neighborhood/{escaped}",
                method="GET",
                query={
                    "depth": integer(arguments.get("depth"), field="depth", default=2, minimum=1, maximum=10),
                    "direction": _direction(arguments.get("direction")),
                    "limit": integer(arguments.get("limit"), field="limit", default=2000, minimum=1, maximum=10000),
                    "edge_limit": integer(
                        arguments.get("edge_limit"),
                        field="edge_limit",
                        default=10000,
                        minimum=1,
                        maximum=50000,
                    ),
                    "expected_revision": _revision(arguments.get("expected_revision")),
                },
                timeout=300,
            )
        else:
            raise GraphExpandError(
                "graph.action 只允许 full、visualization_meta、visualization_nodes、visualization_edges、neighborhood"
            )
    return {
        "library_id": library.id,
        "action": action,
        **_emit(data, label="graph"),
    }


def entity_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """节点或关系详情读取（只读）与删除（管理员写操作）。"""
    kind = str(arguments.get("kind") or "").strip().casefold()
    if kind not in {"node", "relation"}:
        raise GraphExpandError("kind 只允许 node、relation")
    label = "nodes" if kind == "node" else "relations"
    action = str(arguments.get("action") or "get").strip().casefold()
    if action not in {"get", "delete"}:
        raise GraphExpandError("action 只允许 get、delete")
    field = "node_id" if kind == "node" else "edge_id"
    operation = f"{label}.{action}"
    if action == "delete":
        _require_write_admin(config, caller_user, operation)
    identifier = _identifier(arguments, field, label=operation)
    library = _one_library(config, arguments, caller_user=caller_user, operation=operation)
    if library.kind == "portable":
        data = api_request(
            config,
            f"/stores/{label}/{action}",
            {"store_root": library.store_root, field: identifier},
            timeout=300,
        )
    else:
        escaped = urllib.parse.quote(identifier, safe="")
        data = api_request(
            config,
            f"/{label}/{escaped}",
            method="GET" if action == "get" else "DELETE",
            timeout=300,
        )
    return {
        "library_id": library.id,
        "kind": kind,
        "action": action,
        field: identifier,
        "data": data,
    }


def cache_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """检索缓存列表、详情（只读）与清理（管理员写操作）。"""
    action = str(arguments.get("action") or "list").strip().casefold()
    if action not in {"list", "show", "clear"}:
        raise GraphExpandError("cache.action 只允许 list、show、clear")
    if action == "clear":
        _require_write_admin(config, caller_user, "cache.clear")
    library = _one_library(config, arguments, caller_user=caller_user, operation="cache")
    page = integer(arguments.get("page"), field="page", default=1, minimum=1, maximum=100000)
    page_size = integer(arguments.get("page_size"), field="page_size", default=20, minimum=1, maximum=100)
    cache_key = str(arguments.get("cache_key") or "").strip()
    stale_only = arguments.get("stale_only", False)
    if not isinstance(stale_only, bool):
        raise GraphExpandError("stale_only 必须是布尔值")
    if library.kind == "portable":
        root = {"store_root": library.store_root}
        if action == "list":
            data = api_request(config, "/stores/cache/list", {**root, "page": page, "page_size": page_size})
        elif action == "show":
            if not cache_key:
                raise GraphExpandError("cache.show 需要 cache_key")
            data = api_request(config, "/stores/cache/show", {**root, "cache_key": cache_key})
        else:
            data = api_request(config, "/stores/cache/clear", {**root, "stale_only": stale_only})
    elif action == "list":
        data = api_request(
            config,
            "/search/cache",
            method="GET",
            query={"page": page, "page_size": page_size},
        )
    elif action == "show":
        if not cache_key:
            raise GraphExpandError("cache.show 需要 cache_key")
        escaped = urllib.parse.quote(cache_key, safe="")
        data = api_request(config, f"/search/cache/{escaped}", method="GET")
    else:
        data = api_request(
            config,
            "/search/cache",
            method="DELETE",
            query={"stale_only": stale_only},
        )
    return {"library_id": library.id, "action": action, **_emit(data, label="cache")}


def maintenance_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """维护动作：节点群摘要（同步）、图谱整理（LLM）、回收站清理。全部为管理员操作。"""
    action = str(arguments.get("action") or "summarize").strip().casefold()
    aliases = {
        "summarize": "summarize",
        "organize_graph": "organize_graph",
        "organize-graph": "organize_graph",
        "cleanup_recycle": "cleanup_recycle",
        "cleanup-recycle": "cleanup_recycle",
    }
    normalized = aliases.get(action)
    if normalized is None:
        raise GraphExpandError("maintenance.action 只允许 summarize、organize_graph、cleanup_recycle")
    _require_write_admin(config, caller_user, f"maintenance.{normalized}")
    library = _one_library(config, arguments, caller_user=caller_user, operation="maintenance")
    use_llm = arguments.get("use_llm", True)
    summarize = arguments.get("summarize", True)
    force = arguments.get("force", False)
    for value, field in ((use_llm, "use_llm"), (summarize, "summarize"), (force, "force")):
        if not isinstance(value, bool):
            raise GraphExpandError(f"{field} 必须是布尔值")
    if normalized == "organize_graph":
        payload = {"use_llm": use_llm, "summarize": summarize}
        endpoint = "/maintenance/organize-graph" if library.kind != "portable" else "/stores/maintenance/organize-graph"
    elif normalized == "cleanup_recycle":
        payload = {"force": force}
        endpoint = "/maintenance/cleanup-recycle" if library.kind != "portable" else "/stores/maintenance/cleanup-recycle"
    else:
        payload = {}
        endpoint = "/maintenance/summarize" if library.kind != "portable" else "/stores/maintenance/summarize"
    if library.kind == "portable":
        payload["store_root"] = library.store_root
        data = api_request(
            config,
            endpoint,
            payload,
            timeout=config.ingest_timeout_seconds,
        )
    elif normalized == "cleanup_recycle" and force:
        data = api_request(
            config,
            "/maintenance/recycle",
            method="DELETE",
            timeout=config.ingest_timeout_seconds,
        )
    else:
        data = api_request(
            config,
            endpoint,
            payload,
            timeout=config.ingest_timeout_seconds,
        )
    background = library.kind != "portable" and normalized in {"organize_graph"}
    if background:
        note = "内置库图谱整理返回后台作业句柄，可用 jobs 查询进度。"
    elif library.kind == "portable":
        note = "portable Store 维护操作同步执行，响应即为最终结果。"
    else:
        note = "内置库的本次维护操作同步执行，响应即为最终结果。"
    return {
        "ok": True,
        "library_id": library.id,
        "action": normalized,
        "background_job": background,
        "note": note,
        "data": data,
    }


def logs_operation(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """读取 kemo-graph 自身的运行日志（只读，脱敏由服务端完成）。"""
    _require_read_admin(config, caller_user, "logs")
    category = str(arguments.get("category") or "internal").strip().casefold()
    if category not in {"terminal", "query", "internal"}:
        raise GraphExpandError("category 只允许 terminal、query、internal")
    raw_date = str(arguments.get("date") or "").strip()
    if raw_date:
        if len(raw_date) != 10 or raw_date[4] != "-" or raw_date[7] != "-":
            raise GraphExpandError("date 必须是 YYYY-MM-DD")
        if not (raw_date[:4] + raw_date[5:7] + raw_date[8:]).isdigit():
            raise GraphExpandError("date 必须是 YYYY-MM-DD")
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
        except ValueError as exc:
            raise GraphExpandError("date 必须是有效的 YYYY-MM-DD 日期") from exc
    data = api_request(
        config,
        "/system/logs",
        method="GET",
        query={
            "category": category,
            "date": raw_date or None,
            "limit": integer(arguments.get("limit"), field="limit", default=200, minimum=1, maximum=500),
        },
        timeout=120,
    )
    return {"category": category, "date": raw_date or None, **_emit(data, label="logs")}


def config_read(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """读取 kemo-graph 当前配置的脱敏镜像（只读，密钥由服务端掩码）。"""
    _require_read_admin(config, caller_user, "config")
    data = api_request(config, "/config", method="GET", timeout=120)
    return {"library_id": None, **_emit(data, label="config")}


def update_status(
    config: GraphConfig,
    arguments: dict[str, Any],
    *,
    caller_user: str | None = None,
) -> dict[str, Any]:
    """读取 kemo-graph 应用更新状态（只读；本拓展不提供 check/apply）。"""
    _require_read_admin(config, caller_user, "update_status")
    data = api_request(config, "/update/status", method="GET", timeout=120)
    return {"library_id": None, **_emit(data, label="update")}
