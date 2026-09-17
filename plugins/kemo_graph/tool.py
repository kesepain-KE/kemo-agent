"""Read-only guide for the global Kemo Graph sidecar extension."""

from __future__ import annotations

import json
import os
import stat
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
    "graph",
    "entities",
    "cache",
    "maintenance",
    "logs",
    "config",
    "update_status",
    "projects",
    "deactivate",
}
_MAX_INGEST_PATHS = 1000
_MAX_INGEST_PATH_CHARS = 4096
_MAX_INGEST_PATH_TOTAL_CHARS = 200_000


def _source_root_available(value: str) -> bool:
    path = Path(value)
    if not path.is_absolute():
        return False
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            if (
                reparse_flag
                and getattr(metadata, "st_file_attributes", 0) & reparse_flag
            ):
                return False
            isjunction = getattr(os.path, "isjunction", None)
            if isjunction and isjunction(current):
                return False
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _normalize_ingest_paths(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError("paths 必须是非空字符串数组")
    if len(value) > _MAX_INGEST_PATHS:
        raise ValueError(f"paths 最多允许 {_MAX_INGEST_PATHS} 项")
    normalized: list[str] = []
    seen: set[str] = set()
    total_chars = 0
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"paths[{index}] 必须是非空字符串")
        path = item.strip()
        if len(path) > _MAX_INGEST_PATH_CHARS:
            raise ValueError(
                f"paths[{index}] 超过 {_MAX_INGEST_PATH_CHARS} 字符"
            )
        # Keep the guide and operation contracts identical: repeated entries
        # still count toward the submitted payload bound before deduplication.
        total_chars += len(path)
        if total_chars > _MAX_INGEST_PATH_TOTAL_CHARS:
            raise ValueError(
                f"paths 总长度超过 {_MAX_INGEST_PATH_TOTAL_CHARS} 字符"
            )
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


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
            raw_sources = item.get("source_roots", [])
            source_roots = (
                [
                    candidate.strip()
                    for candidate in raw_sources
                    if isinstance(candidate, str) and candidate.strip()
                ]
                if isinstance(raw_sources, list)
                else []
            )
            rows.append({
                "id": str(item.get("id") or ""),
                "display_name": str(item.get("display_name") or ""),
                "kind": str(item.get("kind") or "portable"),
                "enabled": item.get("enabled", True) is True,
                "store_root": item.get("store_root"),
                "source_roots": source_roots,
                "unavailable_source_roots": [
                    candidate
                    for candidate in source_roots
                    if not _source_root_available(candidate)
                ],
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
    paths: list[str] | None,
    query: str,
    mode: str,
    filename: str,
    content: str,
    path: str,
    ingest_after_import: bool,
    document_action: str,
    source_id: str,
    source_ids: list[str] | None,
    expected_content_hash: str,
    expected_relative_path: str,
    confirm_deletions: bool,
    job_id: str,
    limit: int | None,
    graph_action: str,
    entity_kind: str,
    entity_action: str,
    cache_action: str,
    maintenance_action: str,
    log_category: str,
    log_date: str,
    node_id: str,
    edge_id: str,
    cache_key: str,
    stale_only: bool,
    page: int,
    page_size: int | None,
    expected_revision: str,
    depth: int,
    direction: str,
    edge_limit: int,
    nodes_page: int | None,
    nodes_page_size: int,
    use_llm: bool,
    summarize: bool,
    force: bool,
    project: str | None,
    search: str,
    graph_status: str,
    rag_status: str,
    include_summary: bool,
    project_action: str,
    name: str,
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
        normalized_paths = _normalize_ingest_paths(paths)
        if normalized_paths is not None:
            params["paths"] = normalized_paths
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
        if normalized_action not in {"list", "content", "update", "delete", "move"}:
            raise ValueError("document_action 只允许 list、content、update、delete、move")
        params["action"] = normalized_action
        if normalized_action == "list":
            params["page"] = page if isinstance(page, int) and page >= 1 else 1
            params["page_size"] = page_size if isinstance(page_size, int) and 1 <= page_size <= 100 else 20
            if project is not None:
                params["project"] = project
            if search.strip():
                params["search"] = search.strip()
            if graph_status.strip():
                params["graph_status"] = graph_status.strip()
            if rag_status.strip():
                params["rag_status"] = rag_status.strip()
            if include_summary:
                params["include_summary"] = True
        elif normalized_action != "move":
            if not source_id.strip():
                raise ValueError(f"documents.{normalized_action} 需要 source_id")
            params["source_id"] = source_id.strip()
        if normalized_action == "update":
            params["content"] = content
            if expected_content_hash.strip():
                params["expected_content_hash"] = expected_content_hash.strip()
        if normalized_action == "delete":
            params["confirm"] = "delete"
        if normalized_action == "move":
            normalized_source_ids = list(
                dict.fromkeys(
                    item.strip()
                    for item in (source_ids or [])
                    if isinstance(item, str) and item.strip()
                )
            )
            has_single = bool(source_id.strip())
            has_batch = bool(normalized_source_ids)
            if has_single == has_batch:
                raise ValueError("documents.move 需要 source_id 或 source_ids，且二者只能提供一个")
            if has_batch:
                if len(normalized_source_ids) > 1000:
                    raise ValueError("source_ids 最多允许 1000 项")
                if filename.strip() or expected_relative_path.strip():
                    raise ValueError("批量移动不支持 filename 或 expected_relative_path")
                params["source_ids"] = normalized_source_ids
                params["project"] = project if project is not None else ""
            else:
                if project is None and not filename.strip():
                    raise ValueError("单篇移动至少需要 filename 或 project")
                params["source_id"] = source_id.strip()
                if filename.strip():
                    params["filename"] = filename.strip()
                if project is not None:
                    params["project"] = project
                if expected_relative_path.strip():
                    params["expected_relative_path"] = expected_relative_path.strip()
    elif operation == "jobs":
        if len(selected) != 1:
            raise ValueError("jobs 每次必须且只能选择一个 library_id")
        if job_id.strip():
            params["job_id"] = job_id.strip()
        else:
            params["limit"] = limit if isinstance(limit, int) and 1 <= limit <= 1000 else 100
    elif operation == "graph":
        if len(selected) != 1:
            raise ValueError("graph 每次必须且只能选择一个 library_id")
        action = graph_action.strip().casefold() or "full"
        allowed = {"full", "visualization_meta", "visualization_nodes", "visualization_edges", "neighborhood"}
        if action not in allowed:
            raise ValueError("graph_action 不合法")
        params["action"] = action
        if action in {"visualization_nodes", "visualization_edges"}:
            params["page"] = page if isinstance(page, int) and page >= 1 else 1
            maximum = 5000 if action.endswith("nodes") else 10000
            default_size = 1000 if action.endswith("nodes") else 2000
            params["page_size"] = page_size if isinstance(page_size, int) and 1 <= page_size <= maximum else default_size
        if action == "full":
            if nodes_page is not None:
                params["nodes_page"] = nodes_page
            params["nodes_page_size"] = nodes_page_size
        if action == "neighborhood":
            if not node_id.strip():
                raise ValueError("graph.neighborhood 需要 node_id")
            params.update({
                "node_id": node_id.strip(),
                "depth": depth,
                "direction": direction,
                "limit": limit if isinstance(limit, int) and 1 <= limit <= 10000 else 2000,
                "edge_limit": edge_limit,
            })
        if expected_revision.strip():
            params["expected_revision"] = expected_revision.strip()
    elif operation == "entities":
        if len(selected) != 1:
            raise ValueError("entities 每次必须且只能选择一个 library_id")
        kind = entity_kind.strip().casefold()
        action = entity_action.strip().casefold() or "get"
        if kind not in {"node", "relation"} or action not in {"get", "delete"}:
            raise ValueError("entities 需要合法的 entity_kind 与 entity_action")
        params.update({"kind": kind, "action": action})
        identifier = node_id.strip() if kind == "node" else edge_id.strip()
        if not identifier:
            raise ValueError("entities 缺少对应的 node_id 或 edge_id")
        params["node_id" if kind == "node" else "edge_id"] = identifier
    elif operation == "cache":
        if len(selected) != 1:
            raise ValueError("cache 每次必须且只能选择一个 library_id")
        action = cache_action.strip().casefold() or "list"
        if action not in {"list", "show", "clear"}:
            raise ValueError("cache_action 只允许 list、show、clear")
        params["action"] = action
        if action == "list":
            params.update({
                "page": page,
                "page_size": page_size if isinstance(page_size, int) and 1 <= page_size <= 100 else 20,
            })
        elif action == "show":
            if not cache_key.strip():
                raise ValueError("cache.show 需要 cache_key")
            params["cache_key"] = cache_key.strip()
        else:
            params["stale_only"] = stale_only
    elif operation == "maintenance":
        if len(selected) != 1:
            raise ValueError("maintenance 每次必须且只能选择一个 library_id")
        action = maintenance_action.strip().casefold().replace("-", "_") or "summarize"
        if action not in {"summarize", "organize_graph", "cleanup_recycle"}:
            raise ValueError("maintenance_action 不合法")
        params.update({"action": action, "use_llm": use_llm, "summarize": summarize, "force": force})
    elif operation == "logs":
        category = log_category.strip().casefold() or "internal"
        if category not in {"internal", "query", "terminal"}:
            raise ValueError("log_category 只允许 internal、query、terminal")
        params.update({
            "category": category,
            "limit": limit if isinstance(limit, int) and 1 <= limit <= 500 else 200,
        })
        if log_date.strip():
            params["date"] = log_date.strip()
    elif operation in {"config", "update_status"}:
        params = {}
    elif operation == "projects":
        if len(selected) != 1:
            raise ValueError("projects 每次必须且只能选择一个 library_id")
        action = project_action.strip().casefold() or "list"
        if action not in {"list", "create"}:
            raise ValueError("project_action 只允许 list、create")
        params["action"] = action
        if action == "create":
            if not name.strip() or len(name.strip()) > 160:
                raise ValueError("projects.create 需要 1..160 字符的 name")
            params["name"] = name.strip()
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
    elif operation in {"graph", "config", "logs"}:
        warning = "仅在用户明确要求读取对应图谱结构、配置镜像或运行日志时调用。"
    elif operation == "maintenance":
        warning = "维护与永久清理属于受控写操作；cleanup_recycle force=true 执行前必须再次确认。"
    elif operation == "entities" and entity_action.strip().casefold() == "delete":
        warning = "节点或关系删除会修改知识图谱，必须先获得用户明确确认。"
    elif operation == "cache" and cache_action.strip().casefold() == "clear":
        warning = "检索缓存清理属于写操作，必须先获得用户明确确认。"
    elif operation == "projects" and project_action.strip().casefold() == "create":
        warning = "将在所选知识库创建项目文件夹。"
    elif operation == "documents" and document_action.strip().casefold() == "move":
        warning = "移动或重命名只改变 Store 内 Markdown 位置；上游源文件后续同步仍可能按来源规则重建。"
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
    paths: list[str] | None = None,
    query: str = "",
    mode: str = "",
    filename: str = "",
    content: str = "",
    path: str = "",
    ingest_after_import: bool = False,
    document_action: str = "",
    source_id: str = "",
    source_ids: list[str] | None = None,
    expected_content_hash: str = "",
    expected_relative_path: str = "",
    confirm_deletions: bool = False,
    job_id: str = "",
    limit: int | None = None,
    graph_action: str = "",
    entity_kind: str = "",
    entity_action: str = "",
    cache_action: str = "",
    maintenance_action: str = "",
    log_category: str = "",
    log_date: str = "",
    node_id: str = "",
    edge_id: str = "",
    cache_key: str = "",
    stale_only: bool = False,
    page: int = 1,
    page_size: int | None = None,
    expected_revision: str = "",
    depth: int = 2,
    direction: str = "both",
    edge_limit: int = 10000,
    nodes_page: int | None = None,
    nodes_page_size: int = 100,
    use_llm: bool = True,
    summarize: bool = True,
    force: bool = False,
    project: str | None = None,
    search: str = "",
    graph_status: str = "",
    rag_status: str = "",
    include_summary: bool = False,
    project_action: str = "",
    name: str = "",
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
            "maintenance",
            "deactivate",
        } or (
            normalized_operation == "documents"
            and document_action.strip().casefold() in {"update", "delete", "move"}
        ) or (
            normalized_operation == "entities"
            and entity_action.strip().casefold() == "delete"
        ) or (
            normalized_operation == "cache"
            and cache_action.strip().casefold() == "clear"
        ) or (
            normalized_operation == "projects"
            and project_action.strip().casefold() == "create"
        )
        if mutating and not configuration.get("caller_is_admin", False):
            raise PermissionError("只有 Kemo Graph admin_users 可以生成写操作调用")
        if normalized_operation in {"logs", "config", "update_status"} and not configuration.get(
            "caller_is_admin", False
        ):
            raise PermissionError("只有 Kemo Graph admin_users 可以读取服务端全局信息")
        return {
            "ok": True,
            **_operation_guide(
                normalized_operation,
                caller_user=user,
                library_ids=library_ids,
                paths=paths,
                query=query,
                mode=str(mode or "").strip().casefold(),
                filename=filename,
                content=content,
                path=path,
                ingest_after_import=ingest_after_import,
                document_action=document_action,
                source_id=source_id,
                source_ids=source_ids,
                expected_content_hash=expected_content_hash,
                expected_relative_path=expected_relative_path,
                confirm_deletions=confirm_deletions,
                job_id=job_id,
                limit=limit,
                graph_action=graph_action,
                entity_kind=entity_kind,
                entity_action=entity_action,
                cache_action=cache_action,
                maintenance_action=maintenance_action,
                log_category=log_category,
                log_date=log_date,
                node_id=node_id,
                edge_id=edge_id,
                cache_key=cache_key,
                stale_only=stale_only,
                page=page,
                page_size=page_size,
                expected_revision=expected_revision,
                depth=depth,
                direction=direction,
                edge_limit=edge_limit,
                nodes_page=nodes_page,
                nodes_page_size=nodes_page_size,
                use_llm=use_llm,
                summarize=summarize,
                force=force,
                project=project,
                search=search,
                graph_status=graph_status,
                rag_status=rag_status,
                include_summary=include_summary,
                project_action=project_action,
                name=name,
            ),
        }
    raise ValueError(f"不支持的 action：{action}")
