from __future__ import annotations

from pathlib import Path
from typing import Any

from run.config import load_config

from plugins.memory_manage.memory_ops import (
    add_fragment,
    delete_fragment,
    edit_fragment,
    get_fragment,
    list_entries,
    search_by_content,
    search_many,
    search_by_title,
)


def run(
    action: str,
    tier: str,
    query: str | None = None,
    queries: list[dict[str, Any]] | None = None,
    filename: str | None = None,
    content: str | None = None,
    new_filename: str | None = None,
    limit: int = 50,
    context_chars: int = 240,
    case_sensitive: bool = False,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"]).resolve()
    user = str(context["user"])
    if context.get("agent") == "self_improve" and action not in {
        "search_by_title",
        "search_by_content",
        "search_many",
    }:
        raise PermissionError(
            "self_improve 只能用 memory_manage 搜索；候选和晋升由运行时原子持久化"
        )
    if context.get("agent") == "memory_temporary_important" and action not in {
        "list",
        "get",
    }:
        raise PermissionError(
            "memory_temporary_important 只能读取记忆；热视图与永久协调由运行时原子持久化"
        )
    config = load_config(user, root)
    if action == "list":
        return list_entries(root, user, config, tier, limit=limit)
    if action == "get":
        if not filename:
            raise ValueError("get 需要 filename")
        return get_fragment(root, user, config, tier, filename)
    if action == "search_by_title":
        return search_by_title(
            root, user, config, tier, query or "",
            limit=limit, case_sensitive=case_sensitive,
        )
    if action == "search_by_content":
        return search_by_content(
            root, user, config, tier, query or "",
            limit=limit, context_chars=context_chars, case_sensitive=case_sensitive,
        )
    if action == "search_many":
        return search_many(
            root,
            user,
            config,
            tier,
            queries or [],
            limit=limit,
            context_chars=context_chars,
            case_sensitive=case_sensitive,
        )
    if action == "delete":
        if not filename:
            raise ValueError("delete 需要 filename")
        return delete_fragment(root, user, config, tier, filename)
    if action == "edit":
        if not filename or content is None:
            raise ValueError("edit 需要 filename 和 content")
        return edit_fragment(
            root,
            user,
            config,
            tier,
            filename,
            content,
            new_filename=new_filename,
        )
    if action == "add":
        if not filename or content is None:
            raise ValueError("add 需要 filename 和 content")
        return add_fragment(root, user, config, tier, filename, content)
    raise ValueError(f"未知 memory_manage action：{action}")
