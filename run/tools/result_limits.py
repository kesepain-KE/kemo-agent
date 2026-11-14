"""One character budget for model-visible tool results, independent of plugins."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run.tools.errors import ToolResultTooLargeError
from run.tools.result_artifacts import save_result_artifact


MAX_TOOL_RESULT_CHARS = 100_000
TOOL_RESULT_VIEW_VERSION = "tool-result-v1"


def _render(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def limit_tool_result(
    tool_name: str, arguments: dict[str, Any], value: Any,
    context: dict[str, Any] | None = None, *,
    limit_chars: int = MAX_TOOL_RESULT_CHARS,
    execution_completed: bool = True,
    raw_card_id: str | None = None,
) -> Any:
    """Keep small results unchanged; archive large results and return a receipt.

    Count serialized JSON characters, including escaping and nested values.
    Omission is not an execution failure and must never trigger side-effect replay.
    """
    rendered = _render(value)
    if len(rendered) <= limit_chars:
        return value
    artifact = None
    artifact_error = None
    if context and context.get("root") and context.get("user"):
        if not isinstance(value, dict) or "ok" not in value:
            from run.infra import record_runtime_event
            record_runtime_event(
                Path(context["root"]), str(context["user"]),
                category="backend", name="tool_result_missing_ok", status="failed",
                tool_name=tool_name, session_id=str(context.get("session_id") or ""),
            )
        try:
            artifact = save_result_artifact(rendered, context)
        except (OSError, ValueError, RuntimeError) as error:
            artifact_error = type(error).__name__
    if artifact is None and raw_card_id is None:
        raise ToolResultTooLargeError(
            tool_name, arguments, result_chars=len(rendered), limit_chars=limit_chars,
        )
    receipt: dict[str, Any] = {
        "ok": isinstance(value, dict) and value.get("ok") is True,
        "execution_completed": execution_completed,
        "content_omitted": True,
        "result_chars": len(rendered),
        "limit_chars": limit_chars,
        "preview": rendered[:4000],
        "instruction": (
            "本次工具调用已返回，执行是否成功以 ok 和执行状态为准；不要因为正文省略而原样重跑。"
            if execution_completed else
            "工具仍在运行；本回执仅省略过长内容，不代表执行结束，不要重复启动。"
        ),
    }
    if artifact is not None:
        receipt["artifact"] = artifact
        receipt["instruction"] += (
            "完整 JSON 在 artifact.path。按需提取少量字段或字符；"
            "多行文件可用 file.read_range 小范围读取，"
            "长单行 JSON 请用 shell 解析后限制输出，不要整文件读回上下文。"
        )
    else:
        # The card's RAW is committed with this VIEW. A failed optional export
        # must not discard an already-executed tool's original result.
        receipt["raw_card_id"] = raw_card_id
        receipt["artifact_error"] = artifact_error
        receipt["instruction"] += "完整原文保留在历史 RAW 卡片，结果文件导出失败，请核对原卡片。"
    if len(_render(receipt)) > limit_chars:
        del receipt["preview"]
    if len(_render(receipt)) > limit_chars:
        raise ToolResultTooLargeError(
            tool_name, arguments, result_chars=len(rendered), limit_chars=limit_chars,
        )
    return receipt


def tool_result_card_view(
    payload: dict[str, Any], context: dict[str, Any], *, raw_card_id: str,
) -> dict[str, Any]:
    """Bound the whole card payload, while RAW retains results and call diagnostics."""
    if len(_render(payload)) <= MAX_TOOL_RESULT_CHARS:
        return payload
    original = payload.get("original_call") or {}
    view = {**payload, "result": None}
    # Call arguments/raw parsing diagnostics can themselves dominate a result
    # card. Keep identity/status; native pairing still uses the immutable call.
    if len(_render(view)) > MAX_TOOL_RESULT_CHARS // 2:
        view["original_call"] = {
            "name": original.get("name"), "arguments_omitted": True,
        }
    available = MAX_TOOL_RESULT_CHARS - len(_render(view)) + len("null")
    view["result"] = limit_tool_result(
        str(original.get("name") or "unknown_tool"), original.get("arguments") or {},
        payload.get("result"), context, limit_chars=available,
        execution_completed=not (payload.get("execution") or {}).get("execution_live", False),
        raw_card_id=raw_card_id,
    )
    return view


def limit_tool_result_items(items: list, context: dict[str, Any]) -> None:
    """The subagent request boundary also covers restored and synthetic results."""
    from provider.protocol.models import JsonContent, ToolResultItem

    for index, item in enumerate(items):
        if not isinstance(item, ToolResultItem):
            continue
        if len(item.content) == 1 and isinstance(item.content[0], JsonContent):
            value = item.content[0].data
        else:
            value = {
                "ok": not item.is_error,
                "content": [part.model_dump(mode="json", exclude_none=True) for part in item.content],
            }
        limited = limit_tool_result(item.name, {}, value, context)
        if limited is not value:
            items[index] = item.model_copy(update={"content": [JsonContent(data=limited)]})
