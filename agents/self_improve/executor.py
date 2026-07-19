from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult


TRIGGERS = frozenset({"context_compression", "memory_promotion"})


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    trigger = input_data.get("trigger")
    if trigger not in TRIGGERS:
        raise AgentOutputError(
            "self_improve trigger 必须是 context_compression 或 memory_promotion"
        )
    if trigger == "context_compression" and not isinstance(
        input_data.get("rounds"),
        list,
    ):
        raise AgentOutputError("context_compression 输入缺少 rounds 数组")
    if trigger == "memory_promotion" and not isinstance(
        input_data.get("promotions"),
        list,
    ):
        raise AgentOutputError("memory_promotion 输入缺少 promotions 数组")

    result = context.run_model(input_data)
    required = "candidates" if trigger == "context_compression" else "promotions"
    if not isinstance(result.data.get(required), list):
        raise AgentOutputError(f"self_improve 输出缺少 {required} 数组")
    return result
