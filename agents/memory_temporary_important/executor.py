from __future__ import annotations

from typing import Any

from plugins.memory_manage.memory_ops import apply_important_memory_view
from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import contains_sensitive_credential


DEFAULT_IMPORTANT_MEMORY_OUTPUT_MAX_CHARS = 20000
TRIGGERS = frozenset({"periodic_scan", "daily_consolidate"})


def _important_output_limit(config: dict[str, Any]) -> int:
    value = (config.get("memory") or {}).get(
        "important_memory_output_max_chars",
        DEFAULT_IMPORTANT_MEMORY_OUTPUT_MAX_CHARS,
    )
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_IMPORTANT_MEMORY_OUTPUT_MAX_CHARS
    return value


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    trigger = input_data.get("trigger")
    if trigger not in TRIGGERS:
        raise AgentOutputError(
            "memory_temporary_important trigger 必须是 periodic_scan 或 daily_consolidate"
        )

    result = context.run_model(input_data)
    limit = _important_output_limit(context.runner.config)
    content = result.data.get("content")
    if not isinstance(content, str):
        raise AgentOutputError("memory_temporary_important 输出缺少 content 字符串")
    body = content.strip()
    if contains_sensitive_credential(body):
        raise AgentOutputError("临时重要记忆包含疑似敏感凭据，已拒绝持久化")

    featured = result.data.get("featured")
    reconciliations = result.data.get("permanent_reconciliations")
    if not isinstance(featured, list):
        raise AgentOutputError("memory_temporary_important 输出缺少 featured 数组")
    if not isinstance(reconciliations, list):
        raise AgentOutputError(
            "memory_temporary_important 输出缺少 permanent_reconciliations 数组"
        )
    if trigger == "daily_consolidate" and reconciliations:
        raise AgentOutputError("每日整理不得执行永久记忆协调")

    if len(body) > limit:
        raise AgentOutputError(
            f"临时重要记忆超过输出上限：{len(body)} > {limit}；"
            "请精简合并后重试。正文超过 Prompt 注入预算不会影响完整落盘"
        )

    try:
        update = apply_important_memory_view(
            context.runner.root,
            context.runner.user,
            context.runner.config,
            body,
            featured,
            reconciliations,
        )
    except (ValueError, RuntimeError) as exc:
        raise AgentOutputError(f"临时重要记忆更新失败：{exc}") from exc
    result.metadata["important_memory_update"] = update
    return result
