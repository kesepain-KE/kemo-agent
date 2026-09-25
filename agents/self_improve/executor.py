from __future__ import annotations

from typing import Any

from run.agents import AgentOutputError, AgentRunResult
from run.memory import (
    MemoryStore,
    bind_reference,
    memory_extraction_candidate_limit,
    quoted_rounds,
    search_receipts,
)


TRIGGERS = frozenset({"context_compression", "memory_promotion", "manual_review"})


def _user_only_rounds(rounds: list[Any]) -> list[dict[str, Any]]:
    """Remove assistant-derived state before background memory extraction."""

    sanitized: list[dict[str, Any]] = []
    for raw_round in rounds:
        if not isinstance(raw_round, dict):
            continue
        user_messages: list[dict[str, Any]] = []
        for raw_message in raw_round.get("messages") or []:
            if not isinstance(raw_message, dict) or raw_message.get("role") != "user":
                continue
            content = raw_message.get("content")
            user_messages.append({"role": "user", "content": content})
        sanitized.append(
            {
                "round": raw_round.get("round"),
                "messages": user_messages,
            }
        )
    return sanitized


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
    if trigger == "context_compression":
        user_rounds = _user_only_rounds(input_data.get("rounds") or [])
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
        reasons: dict[str, int] = {}
        receipts = search_receipts(result.metadata)
        store = (MemoryStore(runner.root, runner.user, runtime_config)
                 if runner is not None and hasattr(runner, "root") else None)
        for candidate in result.data["candidates"]:
            if not isinstance(candidate, dict):
                rejected += 1
                reasons["invalid_candidate"] = reasons.get("invalid_candidate", 0) + 1
                continue
            action = str(candidate.get("action") or "upsert").strip().casefold()
            evidence_rounds = quoted_rounds(candidate, user_rounds)
            evidence_is_user_quote = bool(evidence_rounds)
            candidate = dict(candidate)
            candidate.pop("expected_content_hash", None)
            candidate.pop("evidence_dates", None)
            candidate["evidence_rounds"] = evidence_rounds
            if store is not None:
                try:
                    candidate = bind_reference(candidate, receipts, store)
                except (ValueError, RuntimeError) as exc:
                    rejected += 1
                    reason = str(exc)
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
            if action == "forget":
                if candidate.get("explicit") is True and evidence_is_user_quote:
                    accepted.append(candidate)
                else:
                    rejected += 1
                    reasons["forget_requires_explicit_quote"] = reasons.get("forget_requires_explicit_quote", 0) + 1
                continue
            if (
                action not in {"upsert", "create", "reinforce", "revise"}
                or candidate.get("durable") is not True
                or not evidence_is_user_quote
            ):
                rejected += 1
                reason = "invalid_action" if action not in {"upsert", "create", "reinforce", "revise"} else "not_durable" if candidate.get("durable") is not True else "invalid_user_quote"
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            accepted.append(candidate)
        if len(accepted) > candidate_limit:
            reasons["candidate_limit"] = len(accepted) - candidate_limit
        result.data["candidates"] = accepted[:candidate_limit]
        result.metadata["candidate_filter"] = {
            "accepted": len(result.data["candidates"]),
            "rejected": rejected + max(0, len(accepted) - candidate_limit),
            "limit": candidate_limit,
            "fail_closed": True,
            "reasons": reasons,
        }
    if trigger == "manual_review":
        store = MemoryStore(
            context.runner.root,
            context.runner.user,
            context.runner.config,
        )
        receipts = search_receipts(result.metadata)
        bound = []
        rejections = []
        for candidate in result.data["candidates"]:
            if not isinstance(candidate, dict):
                rejections.append({"reason": "invalid_candidate"})
                continue
            try:
                bound.append(bind_reference(candidate, receipts, store))
            except (ValueError, RuntimeError) as exc:
                rejections.append({"reason": str(exc)})
        result.data["candidates"] = bound
        persisted = store.upsert_candidates(
            bound,
            source={"source": "manual_review", "request": input_data["request"]},
        )
        persisted["rejected"] += len(rejections)
        persisted["rejection_reasons"].extend(rejections)
        result.metadata["memory_update"] = persisted
    return result
