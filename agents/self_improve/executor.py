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
from run.memory import assistant_backing, extraction_rounds


TRIGGERS = frozenset({"context_compression", "memory_promotion", "manual_review"})


def _user_only_rounds(rounds: list[Any]) -> list[dict[str, Any]]:
    """Compatibility name; extraction now includes safe assistant narrative."""
    return extraction_rounds(rounds)


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

    user_rounds: list[dict[str, Any]] = []
    model_input = input_data
    if trigger == "context_compression":
        user_rounds = _user_only_rounds(input_data.get("rounds") or [])
        model_input = {**input_data, "rounds": user_rounds}

    if trigger == "context_compression":
        from run.memory import run_extraction_pages
        result = run_extraction_pages(context, model_input)
    else:
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
            original_rounds = candidate.get("evidence_rounds")
            evidence_rounds = quoted_rounds(candidate, user_rounds)
            evidence_is_user_quote = bool(evidence_rounds)
            candidate = dict(candidate)
            candidate.pop("expected_content_hash", None)
            candidate.pop("evidence_dates", None)
            candidate["evidence_rounds"] = evidence_rounds
            candidate.pop("evidence_source", None)
            candidate.pop("evidence_sources", None)
            if store is not None:
                try:
                    candidate = bind_reference(candidate, receipts, store)
                except (ValueError, RuntimeError) as exc:
                    rejected += 1
                    reason = str(exc)
                    reasons[reason] = reasons.get(reason, 0) + 1
                    continue
            action = candidate.get("action", action)
            if not evidence_is_user_quote and action == "reinforce" and store is not None:
                assistant_rounds = quoted_rounds(
                    {**candidate, "evidence_rounds": original_rounds}
                    if original_rounds is not None else
                    {k: v for k, v in candidate.items() if k != "evidence_rounds"},
                    user_rounds, role="assistant")
                backing = assistant_backing(
                    {**candidate, "evidence_rounds": assistant_rounds}, user_rounds)
                evidence_rounds = sorted(set(assistant_rounds) & set(backing))
                candidate["evidence_rounds"] = evidence_rounds
                candidate["evidence_source"] = "assistant"
                candidate["explicit"] = False
            else:
                candidate["evidence_source"] = "user"
            candidate["evidence_sources"] = {
                str(n): candidate["evidence_source"] for n in evidence_rounds}
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
                or not evidence_rounds
            ):
                rejected += 1
                reason = "invalid_action" if action not in {"upsert", "create", "reinforce", "revise"} else "not_durable" if candidate.get("durable") is not True else "invalid_user_quote"
                reasons[reason] = reasons.get(reason, 0) + 1
                continue
            accepted.append(candidate)
        result.data["candidates"] = accepted
        result.metadata["candidate_filter"] = {
            "accepted": len(result.data["candidates"]),
            "rejected": rejected,
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
                item = bind_reference(candidate, receipts, store)
                action = item["action"]
                if action in {"create", "revise", "forget"}:
                    evidence = quoted_rounds(item, [{"round": 1, "messages": [
                        {"role": "user", "content": input_data["request"]}]}])
                    if not evidence:
                        raise ValueError("manual_write_requires_user_quote")
                bound.append(item)
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
