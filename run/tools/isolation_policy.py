"""Choose a safe execution boundary without releasing live thread protection."""
from __future__ import annotations

import pickle

from run.tools.execution_watchdog import (
    ExecutionCapacityError, ExecutionIsolationRequired, reserve_execution,
)


def _context_can_cross_process(arguments, context) -> bool:
    """Check the same payload boundary used by ``start_isolated_tool``."""

    safe_context = {
        key: value
        for key, value in context.items()
        if key not in {"cancel_event", "transport_registry"}
    }
    try:
        pickle.dumps((arguments, safe_context))
    except (pickle.PickleError, TypeError, AttributeError, ValueError, OverflowError):
        return False
    return True


def reserve_tool_execution(tool, arguments, context):
    """Return a thread reservation, or None when this new call needs a process."""
    from run.tools import ToolExecutionCapacityError

    force_process = tool.name in {"shell", "external_message"} or (
        tool.name == "file" and str(arguments.get("action") or "") not in {
            "read", "read_range", "list_dir", "tree_dir", "stat", "exists", "hash", "search",
        }
    )
    requires_process = tool.execution_mode == "process" or force_process
    # An in-memory callable normally stays in the thread pool, but side-effect
    # tools are explicitly process-bound.  Their declared entrypoint can be
    # loaded by the child even when a test/runtime registry also injected a
    # callable.  Conversely, a thread-mode plugin with a closure-bearing
    # context must not be sent down the process fallback when the bucket is
    # exhausted.
    entrypoint_file, separator, function_name = getattr(tool, "entrypoint", "").partition(":")
    directory = getattr(tool, "directory", None)
    has_entrypoint = bool(
        separator
        and entrypoint_file
        and function_name
        and directory is not None
        and (directory / entrypoint_file).is_file()
    )
    payload_picklable = _context_can_cross_process(arguments, context)
    live_thread_dependency = not force_process and tool.execution_mode == "thread" and (
        tool.name == "subagent_dispatch"
        or any(context.get(key) is not None for key in (
            "event_callback",
            "task_ownership",
            "task_ownership_factory",
            "transport_registry",
            "shell_ownership",
        ))
    )
    can_isolate = (
        not live_thread_dependency
        and (tool._callable is None or tool._loaded_from_entrypoint or force_process)
        and has_entrypoint
        and payload_picklable
    )
    if requires_process:
        if can_isolate:
            return None
        raise ToolExecutionCapacityError(
            f"工具 {tool.name} 声明必须进程隔离，但当前调用上下文不可安全迁移"
        )
    bucket = f"tool:{context.get('root', '')}:{context.get('user', '')}:{tool.name}"
    try:
        return reserve_execution(
            f"{bucket}:{context.get('source', '')}:{context.get('session_id', '')}",
            bucket=bucket,
        )
    except ExecutionIsolationRequired as exc:
        if can_isolate:
            return None
        raise ToolExecutionCapacityError("内存工具不能安全迁移到进程，原执行仍在运行") from exc
    except ExecutionCapacityError as exc:
        raise ToolExecutionCapacityError(str(exc)) from exc
