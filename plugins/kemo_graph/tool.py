"""Read-only guide for the global Kemo Graph sidecar extension."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_OPERATIONS = {
    "activate",
    "configuration_status",
    "libraries",
    "refresh",
    "status",
    "initialize",
    "scan",
    "sync",
    "ingest",
    "query",
    "upload",
    "import_file",
    "documents",
    "jobs",
    "deactivate",
}


def _configuration(root: Path, user: str) -> dict[str, Any]:
    path = root / "global_expand" / "kemo_graph" / "graph_config.json"
    if not path.is_file():
        return {"active": False, "module": "global:kemo_graph", "libraries": []}
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {
            "active": True,
            "valid": False,
            "module": "global:kemo_graph",
            "error": "graph_config.json 无法读取",
            "libraries": [],
        }
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        return {
            "active": True,
            "valid": False,
            "module": "global:kemo_graph",
            "error": "graph_config.json 不是 schema_version=2 注册表",
            "libraries": [],
        }
    admin_users = value.get("admin_users")
    if (
        not isinstance(admin_users, list)
        or not admin_users
        or any(not isinstance(item, str) or not item.strip() for item in admin_users)
    ):
        return {
            "active": True,
            "valid": False,
            "module": "global:kemo_graph",
            "error": "graph_config.json 缺少有效 admin_users",
            "libraries": [],
        }
    normalized_admins = list(dict.fromkeys(item.strip() for item in admin_users))
    libraries = value.get("libraries")
    rows: list[dict[str, Any]] = []
    if isinstance(libraries, list):
        for item in libraries:
            if not isinstance(item, dict):
                continue
            raw_allowed = item.get("allowed_users", normalized_admins)
            if (
                not isinstance(raw_allowed, list)
                or not raw_allowed
                or any(
                    not isinstance(candidate, str) or not candidate.strip()
                    for candidate in raw_allowed
                )
            ):
                continue
            allowed = list(
                dict.fromkeys(candidate.strip() for candidate in raw_allowed)
            )
            if "*" not in allowed and user not in allowed:
                continue
            rows.append({
                "id": str(item.get("id") or ""),
                "display_name": str(item.get("display_name") or ""),
                "kind": str(item.get("kind") or "portable"),
                "enabled": item.get("enabled", True) is True,
                "store_root": item.get("store_root"),
                "source_roots": item.get("source_roots", []),
                "scope": item.get("scope"),
                "owner_id": item.get("owner_id"),
                "allowed_users": allowed,
            })
    return {
        "active": True,
        "valid": True,
        "module": "global:kemo_graph",
        "base_url": str(value.get("base_url") or ""),
        "allow_remote": value.get("allow_remote") is True,
        "caller_is_admin": user in normalized_admins,
        "libraries": rows,
    }


def _operation_guide(
    operation: str,
    *,
    caller_user: str,
    library_ids: list[str] | None,
    query: str,
    mode: str,
    filename: str,
    content: str,
    path: str,
    ingest_after_import: bool,
    document_action: str,
    source_id: str,
    expected_content_hash: str,
    confirm_deletions: bool,
    job_id: str,
    limit: int,
) -> dict[str, Any]:
    if operation not in _OPERATIONS:
        raise ValueError("operation_guide 需要合法 operation")
    params: dict[str, Any] = {}
    selected = list(dict.fromkeys(item.strip() for item in (library_ids or []) if item.strip()))
    if selected:
        params["library_ids"] = selected
    if operation == "query":
        if not query.strip():
            raise ValueError("query 操作需要 query")
        params.update({
            "query": query.strip(),
            "mode": mode if mode in {"graph", "rag", "hybrid", "answer", "global"} else "hybrid",
            "top_k": 20,
            "graph_depth": 2,
            "force": False,
        })
    elif operation == "sync":
        params["confirm_deletions"] = confirm_deletions
    elif operation == "ingest":
        if len(selected) != 1:
            raise ValueError("ingest 每次必须且只能选择一个 library_id")
        params["mode"] = mode if mode in {"graph", "rag", "both"} else "both"
    elif operation == "upload":
        if len(selected) != 1 or not filename.strip() or not content.strip():
            raise ValueError("upload 需要一个 library_id、filename 和 content")
        params.update({"filename": filename.strip(), "content": content})
    elif operation == "import_file":
        if len(selected) != 1 or not path.strip():
            raise ValueError("import_file 需要一个 library_id 和本地文件绝对路径 path")
        params.update(
            {
                "path": path.strip(),
                "ingest_after_import": ingest_after_import,
            }
        )
    elif operation == "documents":
        if len(selected) != 1:
            raise ValueError("documents 每次必须且只能选择一个 library_id")
        normalized_action = document_action.strip().casefold() or "list"
        if normalized_action not in {"list", "content", "update", "delete"}:
            raise ValueError("document_action 只允许 list、content、update、delete")
        params["action"] = normalized_action
        if normalized_action != "list":
            if not source_id.strip():
                raise ValueError(f"documents.{normalized_action} 需要 source_id")
            params["source_id"] = source_id.strip()
        if normalized_action == "update":
            params["content"] = content
            if expected_content_hash.strip():
                params["expected_content_hash"] = expected_content_hash.strip()
        if normalized_action == "delete":
            params["confirm"] = "delete"
    elif operation == "jobs":
        if len(selected) != 1:
            raise ValueError("jobs 每次必须且只能选择一个 library_id")
        if job_id.strip():
            params["job_id"] = job_id.strip()
        else:
            params["limit"] = limit if isinstance(limit, int) and 1 <= limit <= 1000 else 100
    elif operation == "activate":
        params = {
            "schema_version": 2,
            "base_url": "http://127.0.0.1:8000/api/v1",
            "admin_users": [caller_user],
            "allow_remote": False,
            "timeout_seconds": 15,
            "ingest_timeout_seconds": 1800,
            "libraries": [{
                "id": "kemo_graph_builtin",
                "kind": "service_default",
                "display_name": "Kemo Graph 内置文档库",
                "enabled": True,
                "allowed_users": [caller_user],
            }],
        }
    warning = ""
    if operation == "deactivate":
        warning = "只删除拓展本地激活配置；保留同步游标、状态快照和全部外部 Store。"
    elif operation == "sync":
        warning = "只导入已注册 source_roots 的变化；默认不传播删除，也不立即 ingest。"
    elif operation == "ingest":
        warning = "长耗时操作，可能调用 LLM、Embedding 和 Rerank；完成后再手动 status。"
    elif operation == "query":
        warning = "仅在用户明确要求时查询；同一轮默认合并一次，继续/下一步/重来不触发新查询。"
    elif operation == "import_file":
        warning = (
            "将管理员明确指定的本地文件上传到所选 Library；默认只转换导入，"
            "不立即 ingest。支持格式和 50 MB 上限仍由两端共同校验。"
        )
    arguments: dict[str, Any] = {
        "scope": "global",
        "module": "kemo_graph",
        "command": operation,
        "params": params,
    }
    if operation in {"ingest", "import_file"}:
        arguments["timeout"] = 3600
    return {"tool": "expand_call", "arguments": arguments, "warning": warning}


def run(
    action: str,
    operation: str = "",
    library_ids: list[str] | None = None,
    query: str = "",
    mode: str = "",
    filename: str = "",
    content: str = "",
    path: str = "",
    ingest_after_import: bool = False,
    document_action: str = "",
    source_id: str = "",
    expected_content_hash: str = "",
    confirm_deletions: bool = False,
    job_id: str = "",
    limit: int = 100,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(str(context.get("root") or Path.cwd())).resolve()
    user = str(context.get("user") or "").strip()
    if not user:
        raise ValueError("缺少可信调用用户")
    normalized = str(action or "").strip().casefold()
    if normalized == "overview":
        return {
            "ok": True,
            "module": "global:kemo_graph",
            "positioning": "按需调用的外挂超级文档站",
            "principles": [
                "不替换、不增强、不缩减本地知识库和记忆",
                "普通 Prompt 刷新不访问 kemo-graph",
                "Store 绝对路径只能来自管理员注册表；文件导入路径必须由管理员明确指定",
                "查询、扫描、同步和构建仅在用户明确要求后执行",
                "真实操作统一使用 expand_call",
            ],
            "recommended_flow": ["configuration_status", "status", "query"],
            "maintenance_flow": ["scan", "sync", "ingest", "status"],
        }
    if normalized in {"libraries", "configuration_status"}:
        return {"ok": True, **_configuration(root, user)}
    if normalized == "operation_guide":
        if library_ids is not None and (
            not isinstance(library_ids, list)
            or any(not isinstance(item, str) for item in library_ids)
        ):
            raise ValueError("library_ids 必须是字符串数组")
        configuration = _configuration(root, user)
        selected = [
            item.strip()
            for item in (library_ids or [])
            if isinstance(item, str) and item.strip()
        ]
        visible_ids = {
            str(item.get("id") or "")
            for item in configuration.get("libraries", [])
            if isinstance(item, dict)
        }
        unauthorized = [item for item in selected if item not in visible_ids]
        if unauthorized and str(operation or "").strip().casefold() != "activate":
            raise PermissionError(
                "当前用户无权使用图谱库：" + ", ".join(unauthorized)
            )
        normalized_operation = str(operation or "").strip().casefold()
        mutating = normalized_operation in {
            "initialize",
            "sync",
            "ingest",
            "upload",
            "import_file",
            "deactivate",
        } or (
            normalized_operation == "documents"
            and document_action.strip().casefold() in {"update", "delete"}
        )
        if mutating and not configuration.get("caller_is_admin", False):
            raise PermissionError("只有 Kemo Graph admin_users 可以生成写操作调用")
        return {
            "ok": True,
            **_operation_guide(
                normalized_operation,
                caller_user=user,
                library_ids=library_ids,
                query=query,
                mode=str(mode or "").strip().casefold(),
                filename=filename,
                content=content,
                path=path,
                ingest_after_import=ingest_after_import,
                document_action=document_action,
                source_id=source_id,
                expected_content_hash=expected_content_hash,
                confirm_deletions=confirm_deletions,
                job_id=job_id,
                limit=limit,
            ),
        }
    raise ValueError(f"不支持的 action：{action}")
