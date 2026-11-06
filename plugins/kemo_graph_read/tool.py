"""Read-only Kemo Graph operation guide."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_OPERATIONS = {
    "configuration_status",
    "libraries",
    "refresh",
    "status",
    "scan",
    "query",
    "documents",
    "jobs",
    "graph",
    "entities",
    "cache",
    "logs",
    "config",
    "update_status",
    "projects",
}


def _run_shared(
    action: str,
    *args: Any,
    context: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    root = Path(str(context.get("root") or Path.cwd())).resolve()
    implementation = root / "plugins" / "kemo_graph" / "tool.py"
    spec = importlib.util.spec_from_file_location(
        "_kemo_graph_shared_implementation",
        implementation,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Kemo Graph 共用操作实现不可用")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run(action, *args, context=context, **kwargs)


def run(
    action: str,
    operation: str = "",
    *,
    context: dict[str, Any],
    **arguments: Any,
) -> dict[str, Any]:
    normalized_action = str(action or "").strip().casefold()
    if normalized_action != "operation_guide":
        if normalized_action not in {"overview", "libraries", "configuration_status"}:
            raise ValueError("kemo_graph_read 不支持该 action")
        return _run_shared(action, context=context)
    normalized_operation = str(operation or "").strip().casefold()
    if normalized_operation not in _OPERATIONS:
        raise ValueError(
            "kemo_graph_read 只接受只读 operation；读取 operations："
            + ", ".join(sorted(_OPERATIONS))
        )
    if normalized_operation == "documents" and arguments.get("document_action", "list") not in {
        "", "list", "content"
    }:
        raise ValueError("kemo_graph_read 的 documents 只允许 list / content")
    if normalized_operation == "entities" and arguments.get("entity_action", "get") not in {
        "", "get"
    }:
        raise ValueError("kemo_graph_read 的 entities 只允许 get")
    if normalized_operation == "cache" and arguments.get("cache_action", "list") not in {
        "", "list", "show"
    }:
        raise ValueError("kemo_graph_read 的 cache 只允许 list / show")
    if normalized_operation == "projects" and arguments.get("project_action", "list") not in {
        "", "list"
    }:
        raise ValueError("kemo_graph_read 的 projects 只允许 list")
    return _run_shared(
        "operation_guide",
        normalized_operation,
        context=context,
        **arguments,
    )
