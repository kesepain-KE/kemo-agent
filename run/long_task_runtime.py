"""Small cross-Run helpers for conversation-scoped long tasks."""

from __future__ import annotations

import copy
from typing import Any


MAX_LONG_TASK_RUNS = 128


LONG_TASK_CONTINUATION_PROMPT = (
    "【长任务自动续跑】\n"
    "当前轮已达到单轮最大工具调用次数，长任务模式已获得用户授权并继续执行。\n"
    "请读取上一轮已经完成的工具结果、失败记录和未执行调用，继续完成原始任务；"
    "不要重复已经成功的操作。若任务已经完成，请直接给出最终结果并结束；"
    "若需要用户批准、凭据或关键决策，请暂停并明确说明。"
)


def is_continuable_terminal(metadata: dict[str, Any] | None) -> bool:
    """Only the explicit per-round tool-count boundary may auto-continue."""

    value = metadata or {}
    return (
        str(value.get("status") or "").casefold() == "limited"
        and str(value.get("stop_reason") or "").casefold()
        == "max_tool_iterations"
    )


def continuation_request(
    request: dict[str, Any],
    *,
    run_id: str,
    task_id: str,
    continuation: int,
    original_prompt: str,
) -> dict[str, Any]:
    """Build a transient next Run without changing the original user input."""

    # The request carries thread-affine objects (guidance mailbox, transport
    # registry) that cannot be deep-copied. Copy ordinary fields while keeping
    # those runtime handles shared across the logical task.
    next_request = dict(request)
    for key in ("content", "uploaded_files"):
        value = request.get(key)
        next_request[key] = copy.deepcopy(value) if isinstance(value, (list, dict)) else value
    next_request["run_id"] = run_id
    next_request["prompt"] = LONG_TASK_CONTINUATION_PROMPT
    next_request["content"] = []
    next_request["uploaded_files"] = []
    next_request["_long_task_continuation"] = True
    next_request["_user_metadata"] = {
        "synthetic": True,
        "origin": "long_task_continuation",
        "long_task_id": task_id,
        "continuation": max(1, int(continuation)),
        "long_task_original_prompt": str(original_prompt or ""),
    }
    return next_request


def long_task_event_metadata(
    state: dict[str, Any],
    *,
    terminal: bool,
    continuation: bool = False,
) -> dict[str, Any]:
    return {
        "terminal": bool(terminal),
        "long_task": True,
        "long_task_state": copy.deepcopy(state),
        "long_task_continuation": bool(continuation),
    }


def terminal_run_stats(event: Any) -> dict[str, Any]:
    """Extract bounded, additive statistics from one committed Run."""

    metadata = event.metadata if isinstance(getattr(event, "metadata", None), dict) else {}
    usage = event.usage if isinstance(getattr(event, "usage", None), dict) else {}
    if not usage and isinstance(metadata.get("usage"), dict):
        usage = metadata["usage"]
    return {
        "elapsed_ms": max(0, int(metadata.get("elapsed_ms") or 0)),
        "tool_calls": max(0, int(metadata.get("tool_calls") or 0)),
        "provider_requests": max(0, int(usage.get("provider_request_count") or 0)),
        "usage": copy.deepcopy(usage),
        "stop_reason": str(metadata.get("stop_reason") or ""),
    }


def semantic_user_text(message: dict[str, Any], rendered_text: str) -> str:
    """Return the user's semantic request for memory/summary consumers.

    Automatic continuation prompts are durable control records, not new user
    facts.  Consumers may still keep the assistant progress from every Run,
    but should attribute it to the original request.
    """

    metadata = message.get("metadata")
    if (
        isinstance(metadata, dict)
        and metadata.get("synthetic") is True
        and metadata.get("origin") == "long_task_continuation"
    ):
        original = str(metadata.get("long_task_original_prompt") or "").strip()
        if original:
            return original
    return str(rendered_text or "")


__all__ = [
    "LONG_TASK_CONTINUATION_PROMPT",
    "MAX_LONG_TASK_RUNS",
    "is_continuable_terminal",
    "continuation_request",
    "long_task_event_metadata",
    "semantic_user_text",
    "terminal_run_stats",
]
