from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import MemoryStore


TRIGGERS = frozenset({"context_compression", "memory_promotion", "manual_review"})


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    trigger = input_data.get("trigger")
    if trigger not in TRIGGERS:
        raise AgentOutputError(
            "self_improve trigger 必须是 context_compression、memory_promotion 或 manual_review"
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
    if trigger == "manual_review" and not str(input_data.get("request") or "").strip():
        raise AgentOutputError("manual_review 输入缺少 request 字符串")

    result = context.run_model(input_data)
    required = "promotions" if trigger == "memory_promotion" else "candidates"
    if not isinstance(result.data.get(required), list):
        raise AgentOutputError(f"self_improve 输出缺少 {required} 数组")
    if trigger == "manual_review":
        persisted = MemoryStore(
            context.runner.root,
            context.runner.user,
            context.runner.config,
        ).upsert_candidates(
            result.data["candidates"],
            source={"source": "manual_review", "request": input_data["request"]},
        )
        result.metadata["memory_update"] = persisted
    return result
