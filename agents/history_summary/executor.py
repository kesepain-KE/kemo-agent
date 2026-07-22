from __future__ import annotations

import re
from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import contains_sensitive_credential


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(value: Any, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise AgentOutputError(f"history_summary 输出 {field} 必须是字符串")
    text = " ".join(value.split()).strip()
    if _CONTROL_RE.search(text) or not minimum <= len(text) <= maximum:
        raise AgentOutputError(
            f"history_summary 输出 {field} 长度必须为 {minimum}–{maximum} 个可见字符"
        )
    return text


def _normalize_result(result: AgentRunResult, input_data: dict[str, Any]) -> AgentRunResult:
    try:
        result.data["title"] = _clean(
            result.data.get("title"), field="title", minimum=8, maximum=24
        )
        result.data["summary"] = _clean(
            result.data.get("summary"), field="summary", minimum=30, maximum=120
        )
        session_id = str(input_data["session_id"]).strip().casefold()
        if (
            "conv_" in result.data["title"].casefold()
            or session_id in result.data["title"].casefold()
        ):
            raise AgentOutputError("history_summary 标题不得包含 session ID")
        if contains_sensitive_credential(
            result.data["title"]
        ) or contains_sensitive_credential(result.data["summary"]):
            raise AgentOutputError("history_summary 输出包含疑似敏感凭据")
    except AgentOutputError as exc:
        raise AgentOutputError(str(exc), raw_text=result.raw_text) from exc
    return result


def _repair_input(input_data: dict[str, Any], error: AgentOutputError) -> dict[str, Any]:
    raw_text = error.raw_text.strip()
    return {
        **input_data,
        "_format_repair": {
            "required": True,
            "previous_error": str(error),
            "previous_output": raw_text[-4000:] if raw_text else "（上一轮没有生成最终正文）",
            "instruction": (
                "上一轮输出未通过格式校验。重新完成同一摘要任务，只输出一个包含 "
                "title 和 summary 的合法 JSON 对象；不要输出思考、Markdown 或解释。"
            ),
        },
    }


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    if input_data.get("trigger") != "session_closed":
        raise AgentOutputError("history_summary trigger 必须是 session_closed")
    if not str(input_data.get("session_id") or "").strip():
        raise AgentOutputError("history_summary 输入缺少 session_id")
    if int(input_data.get("target_round") or 0) < 1:
        raise AgentOutputError("history_summary 输入 target_round 必须大于 0")
    if not isinstance(input_data.get("rounds"), list) or not input_data["rounds"]:
        raise AgentOutputError("history_summary 输入缺少 rounds")
    try:
        return _normalize_result(context.run_model(input_data), input_data)
    except AgentOutputError as first_error:
        try:
            return _normalize_result(
                context.run_model(_repair_input(input_data, first_error)),
                input_data,
            )
        except AgentOutputError as repair_error:
            raw_text = repair_error.raw_text or first_error.raw_text
            raise AgentOutputError(
                f"history_summary JSON 修复失败：{repair_error}",
                raw_text=raw_text,
            ) from repair_error
