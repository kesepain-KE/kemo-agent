from __future__ import annotations

import ast
import re
from typing import Any

from run.agents import AgentOutputError, AgentRunResult
from run.memory import contains_sensitive_credential


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TITLE_LABEL_RE = re.compile(
    r"(?im)^\s*(?:#{1,6}\s*)?(?:\*{1,2})?标题(?:\*{1,2})?\s*[:：]\s*(.+?)\s*$"
)
_SUMMARY_LABEL_RE = re.compile(
    r"(?ims)^\s*(?:#{1,6}\s*)?(?:\*{1,2})?摘要(?:\*{1,2})?\s*[:：]\s*(.+?)(?=\n\s*(?:#{1,6}\s*)?(?:\*{1,2})?[\w\u4e00-\u9fff]+(?:\*{1,2})?\s*[:：]|\Z)"
)


def _clean(value: Any, *, field: str, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise AgentOutputError(f"history_summary 输出 {field} 必须是字符串")
    text = " ".join(value.split()).strip()
    if _CONTROL_RE.search(text):
        raise AgentOutputError(f"history_summary 输出 {field} 包含不可见控制字符")
    overflow_tolerance = max(12, maximum // 3)
    if len(text) > maximum:
        if len(text) > maximum + overflow_tolerance:
            raise AgentOutputError(
                f"history_summary 输出 {field} 长度必须为 {minimum}–{maximum} 个可见字符"
            )
        text = text[:maximum].rstrip(" ，。；：、,.!！?？;:")
    if len(text) < minimum:
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


def _result_from_data(
    data: dict[str, Any],
    input_data: dict[str, Any],
    *,
    raw_text: str,
    recovery: str,
) -> AgentRunResult:
    result = AgentRunResult(
        agent="history_summary",
        data=dict(data),
        raw_text=raw_text,
        usage={
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "estimated": True,
        },
        model="local-fallback",
        metadata={"history_summary_recovery": recovery},
    )
    return _normalize_result(result, input_data)


def _recover_text_result(
    raw_text: str,
    input_data: dict[str, Any],
) -> AgentRunResult | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json|text|markdown)?\s*", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        literal = ast.literal_eval(cleaned)
    except (SyntaxError, ValueError):
        literal = None
    candidates: list[tuple[dict[str, Any], str]] = []
    if isinstance(literal, dict):
        candidates.append((literal, "python_literal"))
    title_match = _TITLE_LABEL_RE.search(cleaned)
    summary_match = _SUMMARY_LABEL_RE.search(cleaned)
    if title_match and summary_match:
        candidates.append(
            (
                {
                    "title": title_match.group(1).strip(" *`\"'"),
                    "summary": " ".join(summary_match.group(1).split()).strip(
                        " *`\"'"
                    ),
                },
                "labelled_text",
            )
        )
    for data, recovery in candidates:
        try:
            return _result_from_data(
                data,
                input_data,
                raw_text=text,
                recovery=recovery,
            )
        except AgentOutputError:
            continue
    return None


def _fallback_result(input_data: dict[str, Any], raw_text: str) -> AgentRunResult:
    title = "历史对话内容摘要"
    summary = (
        "用户正文与智能体最终正文已保存在历史记录中；自动摘要格式未能确认，"
        "稍后可以重新生成摘要。"
    )
    return _result_from_data(
        {"title": title, "summary": summary},
        input_data,
        raw_text=raw_text,
        recovery="deterministic_fallback",
    )


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    if input_data.get("trigger") != "session_closed":
        raise AgentOutputError("history_summary trigger 必须是 session_closed")
    if not str(input_data.get("session_id") or "").strip():
        raise AgentOutputError("history_summary 输入缺少 session_id")
    target_seq = input_data.get("target_seq")
    if type(target_seq) is not int or target_seq < 1:
        raise AgentOutputError("history_summary 输入 target_seq 必须是正整数")
    records = input_data.get("records")
    if not isinstance(records, list) or not records:
        raise AgentOutputError("history_summary 输入缺少 records")
    for record in records:
        if (
            not isinstance(record, dict)
            or type(record.get("seq")) is not int
            or not 1 <= record["seq"] <= target_seq
            or record.get("role") not in {"user", "assistant"}
            or not isinstance(record.get("text"), str)
            or not record["text"].strip()
        ):
            raise AgentOutputError("history_summary records 必须是有效的 RAW 正文片段")
    try:
        return _normalize_result(context.run_model(input_data), input_data)
    except AgentOutputError as error:
        recovered = _recover_text_result(error.raw_text, input_data)
        if recovered is not None:
            return recovered
        return _fallback_result(input_data, error.raw_text)
