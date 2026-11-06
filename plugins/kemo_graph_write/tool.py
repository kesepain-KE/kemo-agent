"""Write-only Kemo Graph operation guide."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_OPERATIONS = {
    "activate",
    "initialize",
    "sync",
    "ingest",
    "upload",
    "import_file",
    "documents",
    "entities",
    "cache",
    "maintenance",
    "projects",
    "deactivate",
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
    if str(action or "").strip().casefold() != "operation_guide":
        raise ValueError("kemo_graph_write 只接受 operation_guide")
    normalized_operation = str(operation or "").strip().casefold()
    if normalized_operation not in _OPERATIONS:
        raise ValueError(
            "kemo_graph_write 只接受写入 operation；写入 operations："
            + ", ".join(sorted(_OPERATIONS))
        )
    if normalized_operation == "documents" and arguments.get("document_action", "") not in {
        "update", "delete", "move"
    }:
        raise ValueError("kemo_graph_write 的 documents 只允许 update / delete / move")
    if normalized_operation == "entities" and arguments.get("entity_action", "") != "delete":
        raise ValueError("kemo_graph_write 的 entities 只允许 delete")
    if normalized_operation == "cache" and arguments.get("cache_action", "") != "clear":
        raise ValueError("kemo_graph_write 的 cache 只允许 clear")
    if normalized_operation == "projects" and arguments.get("project_action", "") != "create":
        raise ValueError("kemo_graph_write 的 projects 只允许 create")
    return _run_shared(
        "operation_guide",
        normalized_operation,
        context=context,
        **arguments,
    )
