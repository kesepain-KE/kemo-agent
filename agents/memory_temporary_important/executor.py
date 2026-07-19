from __future__ import annotations

from typing import Any

from plugins.memory_manage.memory_ops import write_important_memory
from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import contains_sensitive_credential


DEFAULT_IMPORTANT_MEMORY_MAX_CHARS = 2000
TRIGGERS = frozenset({"periodic_scan", "daily_consolidate"})


def _important_memory_limit(config: dict[str, Any]) -> int:
    value = (config.get("memory") or {}).get(
        "important_memory_max_chars",
        DEFAULT_IMPORTANT_MEMORY_MAX_CHARS,
    )
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return DEFAULT_IMPORTANT_MEMORY_MAX_CHARS
    return value


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    trigger = input_data.get("trigger")
    if trigger not in TRIGGERS:
        raise AgentOutputError(
            "memory_temporary_important trigger 必须是 periodic_scan 或 daily_consolidate"
        )

    result = context.run_model(input_data)
    content = result.data.get("content")
    if not isinstance(content, str):
        raise AgentOutputError(
            "memory_temporary_important 输出缺少 content 字符串"
        )
    body = content.strip()
    if contains_sensitive_credential(body):
        raise AgentOutputError("临时重要记忆包含疑似敏感凭据，已拒绝持久化")

    if trigger == "daily_consolidate":
        limit = _important_memory_limit(context.runner.config)
        if len(body) > limit:
            raise AgentOutputError(
                f"每日整理后的临时重要记忆超过字符上限：{len(body)} > {limit}"
            )

    write_important_memory(
        context.runner.root,
        context.runner.user,
        body,
    )
    return result
