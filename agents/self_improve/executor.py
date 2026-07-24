from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import MemoryStore, memory_extraction_candidate_limit


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
    if trigger == "context_compression":
        rounds = input_data.get("rounds") or []
        runner = getattr(context, "runner", None)
        runtime_config = getattr(runner, "config", {}) if runner is not None else {}
        candidate_limit = memory_extraction_candidate_limit(runtime_config, len(rounds))
        accepted: list[dict[str, Any]] = []
        rejected = 0
        for candidate in result.data["candidates"]:
            if not isinstance(candidate, dict):
                rejected += 1
                continue
            action = str(candidate.get("action") or "upsert").strip().casefold()
            if action == "forget":
                if candidate.get("explicit") is True:
                    accepted.append(candidate)
                else:
                    rejected += 1
                continue
            evidence = str(candidate.get("evidence") or "").strip()
            if (
                action != "upsert"
                or candidate.get("durable") is not True
                or not evidence
            ):
                rejected += 1
                continue
            accepted.append(candidate)
        result.data["candidates"] = accepted[:candidate_limit]
        result.metadata["candidate_filter"] = {
            "accepted": len(result.data["candidates"]),
            "rejected": rejected + max(0, len(accepted) - candidate_limit),
            "limit": candidate_limit,
            "fail_closed": True,
        }
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
