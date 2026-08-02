from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult
from run.memory import MemoryStore, memory_extraction_candidate_limit


TRIGGERS = frozenset({"context_compression", "memory_promotion", "manual_review"})


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    return "\n".join(
        str(block.get("text") or "")
        for block in value
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _normalise_evidence(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _user_only_rounds(rounds: list[Any]) -> tuple[list[dict[str, Any]], str]:
    """Remove assistant-derived state before background memory extraction."""

    sanitized: list[dict[str, Any]] = []
    evidence_parts: list[str] = []
    for raw_round in rounds:
        if not isinstance(raw_round, dict):
            continue
        user_messages: list[dict[str, Any]] = []
        for raw_message in raw_round.get("messages") or []:
            if not isinstance(raw_message, dict) or raw_message.get("role") != "user":
                continue
            content = raw_message.get("content")
            text = _content_text(content).strip()
            if text:
                evidence_parts.append(text)
            user_messages.append({"role": "user", "content": content})
        sanitized.append(
            {
                "round": raw_round.get("round"),
                "messages": user_messages,
            }
        )
    return sanitized, _normalise_evidence("\n".join(evidence_parts))


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

    model_input = input_data
    user_evidence = ""
    if trigger == "context_compression":
        user_rounds, user_evidence = _user_only_rounds(input_data.get("rounds") or [])
        model_input = {**input_data, "rounds": user_rounds}

    result = context.run_model(model_input)
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
            evidence = str(candidate.get("evidence") or "").strip()
            evidence_is_user_quote = bool(
                evidence and _normalise_evidence(evidence) in user_evidence
            )
            if action == "forget":
                if candidate.get("explicit") is True and evidence_is_user_quote:
                    accepted.append(candidate)
                else:
                    rejected += 1
                continue
            if (
                action != "upsert"
                or candidate.get("durable") is not True
                or not evidence_is_user_quote
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
