"""Conversation-memory analysis and durable extraction coordination."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from run.agents import AgentRunner
from run.infra import EngineError
from run.memory import (
    MemoryStore,
    memory_extraction_mode,
    memory_profile,
    memory_domains_for_profile,
)
from run.memory.evidence import trusted_dates, text as evidence_text
from run.conversation import new_usage_total, record_provider_request, usage_from_dict


def memory_round_data(
    *,
    round_number: int,
    prompt: str,
    text: str,
    reasoning: str,
    tool_records: list[dict[str, Any]],
    committed_at: str | None = None,
) -> dict[str, Any]:
    round_data: dict[str, Any] = {
        "round": round_number,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text},
        ],
    }
    if committed_at:
        round_data["committed_at"] = committed_at
    if reasoning:
        round_data["think"] = {"content": reasoning}
    if tool_records:
        round_data["tools"] = [
            {
                "name": str(record.get("name") or ""),
                "status": str(record.get("status") or ""),
                "result": record.get("result"),
            }
            for record in tool_records
            if isinstance(record, dict)
        ]
    return round_data


def analyze_memory_batch(
    *,
    rounds: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
    source: dict[str, Any] | None = None,
    agent_source: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Analyze a bounded batch of completed rounds without mutating state."""

    if not rounds:
        raise EngineError("记忆批量分析至少需要一轮对话")
    round_numbers = [max(0, int(item.get("round") or 0)) for item in rounds]
    if any(number < 1 for number in round_numbers):
        raise EngineError("记忆批量分析包含无效轮次")
    effective_source: dict[str, Any] = dict(
        source
        or {
            "source": "round_commit",
            "round_start": min(round_numbers),
            "round_end": max(round_numbers),
        }
    )
    effective_source["evidence_dates"] = trusted_dates(rounds)
    config = getattr(agent_runner, "config", {})
    try:
        runner_kwargs: dict[str, Any] = {"cancel_event": cancel_event}
        if str(agent_source or "").strip():
            runner_kwargs["source"] = str(agent_source).strip()
        if str(session_id or "").strip():
            runner_kwargs["session_id"] = str(session_id).strip()
        result = agent_runner.run(
            "self_improve",
            {
                "trigger": "context_compression",
                "rounds": rounds,
                "source": effective_source,
                "memory_profile": memory_profile(config),
                "memory_domains": list(memory_domains_for_profile(config)),
            },
            **runner_kwargs,
        )
        candidates = result.data.get("candidates")
        if not isinstance(candidates, list):
            raise EngineError("self_improve 输出缺少 candidates 数组")
        return {
            "status": "completed",
            "candidate_count": len(candidates),
            "candidates": copy.deepcopy(candidates),
            "round_start": min(round_numbers),
            "round_end": max(round_numbers),
            "rounds": round_numbers,
            "source": effective_source,
            "agent": result.agent,
            "usage": dict(result.usage),
            "candidate_filter": copy.deepcopy(result.metadata.get("candidate_filter", {})),
            "error": None,
        }
    except Exception as exc:
        capacity_blocked = (
            getattr(exc, "category", "") == "execution_capacity"
            or type(exc).__name__ == "AgentExecutionCapacityError"
        )
        return {
            "status": "failed",
            "candidate_count": 0,
            "round_start": min(round_numbers),
            "round_end": max(round_numbers),
            "rounds": round_numbers,
            "error": {
                "message": str(exc),
                "exception_type": type(exc).__name__,
                "capacity_blocked": capacity_blocked,
                "work_pending": type(exc).__name__ == "MemoryWorkPending",
            },
        }


def analyze_round_memory(
    *,
    round_number: int,
    prompt: str,
    text: str,
    reasoning: str,
    tool_records: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
    agent_source: str = "",
    session_id: str = "",
    committed_at: str | None = None,
) -> dict[str, Any]:
    """Compatibility wrapper for callers that intentionally process one round."""

    return analyze_memory_batch(
        rounds=[
            memory_round_data(
                round_number=round_number,
                prompt=prompt,
                text=text,
                reasoning=reasoning,
                tool_records=tool_records,
                committed_at=committed_at,
            )
        ],
        agent_runner=agent_runner,
        cancel_event=cancel_event,
        source={"source": "round_commit", "round": round_number},
        agent_source=agent_source,
        session_id=session_id,
    )


def analyze_memory_batch_resilient(
    *,
    rounds: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
    source: dict[str, Any] | None = None,
    retry_once: bool = True,
    agent_source: str = "",
    session_id: str = "",
) -> dict[str, Any]:
    """Retry malformed batch output once, then isolate it by contiguous halves."""

    analysis = analyze_memory_batch(
        rounds=rounds,
        agent_runner=agent_runner,
        cancel_event=cancel_event,
        source=source,
        agent_source=agent_source,
        session_id=session_id,
    )
    if analysis.get("status") == "completed":
        return analysis
    error = analysis.get("error")
    exception_type = (
        str(error.get("exception_type") or "") if isinstance(error, dict) else ""
    )
    if exception_type not in {"AgentOutputError", "EngineError"}:
        return analysis
    if retry_once:
        retried = analyze_memory_batch(
            rounds=rounds,
            agent_runner=agent_runner,
            cancel_event=cancel_event,
            source=source,
            agent_source=agent_source,
            session_id=session_id,
        )
        if retried.get("status") == "completed":
            return retried
        retry_error = retried.get("error")
        retry_type = (
            str(retry_error.get("exception_type") or "")
            if isinstance(retry_error, dict)
            else ""
        )
        if retry_type not in {"AgentOutputError", "EngineError"}:
            return retried
        analysis = retried
    if len(rounds) <= 1:
        return analysis

    # Malformed-output isolation must not cut the user away from that same
    # integration's guidance/final. Huge integrations are already token-paged.
    boundaries = [index for index in range(1, len(rounds))
                  if not rounds[index].get("integration_id")
                  or rounds[index].get("integration_id") != rounds[index - 1].get("integration_id")]
    if not boundaries:
        return analysis
    midpoint = min(boundaries, key=lambda index: abs(index - len(rounds) / 2))
    parts: list[dict[str, Any]] = []
    for subset in (rounds[:midpoint], rounds[midpoint:]):
        numbers = [int(item.get("round") or 0) for item in subset]
        subset_source = {
            **dict(source or {}),
            "round_start": min(numbers),
            "round_end": max(numbers),
        }
        part = analyze_memory_batch_resilient(
            rounds=subset,
            agent_runner=agent_runner,
            cancel_event=cancel_event,
            source=subset_source,
            retry_once=False,
            agent_source=agent_source,
            session_id=session_id,
        )
        if part.get("status") != "completed":
            return {
                **part,
                "failed_batch": {
                    "round_start": min(numbers),
                    "round_end": max(numbers),
                },
            }
        parts.append(part)

    combined_usage = new_usage_total()
    candidates: list[dict[str, Any]] = []
    round_numbers: list[int] = []
    for part in parts:
        candidates.extend(copy.deepcopy(part.get("candidates") or []))
        round_numbers.extend(int(value) for value in part.get("rounds") or [])
        part_usage = part.get("usage")
        if isinstance(part_usage, dict):
            record_provider_request(combined_usage, usage_from_dict(part_usage))
    return {
        "status": "completed",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "round_start": min(round_numbers),
        "round_end": max(round_numbers),
        "rounds": round_numbers,
        "source": {**dict(source or {}), "evidence_dates": trusted_dates(rounds)},
        "candidate_filter": {"batches": [part.get("candidate_filter", {}) for part in parts]},
        "agent": str(parts[-1].get("agent") or "self_improve"),
        "usage": combined_usage,
        "error": None,
        "fallback_split": True,
    }


def persist_round_memory_analysis(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    analysis: dict[str, Any],
    operation_id: str | None = None,
) -> dict[str, Any]:
    """Persist a successful analysis after its owning cursor is validated."""

    if analysis.get("status") != "completed":
        return dict(analysis)
    candidates = analysis.get("candidates")
    if not isinstance(candidates, list):
        return {
            "status": "failed",
            "candidate_count": 0,
            "error": {
                "message": "记忆分析结果缺少 candidates 数组",
                "exception_type": "EngineError",
            },
        }
    source = analysis.get("source")
    if not isinstance(source, dict):
        source = {"source": "round_commit"}
    source = {**source, "evidence_dates": source.get("evidence_dates", {})}
    try:
        deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
        unkeyed: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                unkeyed.append(candidate)
                continue
            raw_key = candidate.get("filename") or candidate.get("target")
            filename_key = str(raw_key or "").strip().casefold()
            from run.memory import normalize_memory_domain, MemoryError
            try:
                domain = normalize_memory_domain(candidate.get("memory_domain"))
            except MemoryError:
                unkeyed.append(candidate)
                continue
            key = (domain, filename_key)
            if filename_key:
                previous = deduplicated.get(key)
                merged = dict(candidate)
                if previous is not None:
                    merged["evidence_rounds"] = sorted(set(
                        previous.get("evidence_rounds", []) + candidate.get("evidence_rounds", [])))
                    roles = {**previous.get("evidence_sources", {}),
                             **candidate.get("evidence_sources", {})}
                    for number, role in previous.get("evidence_sources", {}).items():
                        if role == "user":
                            roles[number] = "user"
                    merged["evidence_sources"] = roles
                    if previous.get("action") == "revise" and candidate.get("action") == "reinforce":
                        merged = {**previous, "evidence_rounds": merged["evidence_rounds"],
                                  "evidence_sources": merged["evidence_sources"]}
                deduplicated[key] = merged
            else:
                unkeyed.append(candidate)
        persisted_candidates = [*deduplicated.values(), *unkeyed]
        from run.memory.candidate_work import apply_candidate_work
        work = apply_candidate_work(MemoryStore(root, user, config),
                                    persisted_candidates, source, operation_id)
        if work["status"] != "completed":
            return work
        persisted = work["persisted"]
    except Exception as exc:
        return {
            "status": "failed",
            "candidate_count": 0,
            "usage": dict(analysis.get("usage") or {}),
            "error": {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            },
        }
    result = {
        key: copy.deepcopy(value)
        for key, value in analysis.items()
        if key not in {"candidates", "source"}
    }
    result["persisted"] = persisted
    result["persisted_candidate_count"] = len(persisted_candidates)
    return result


def memory_batch_operation_id(
    user: str,
    source: str,
    session_id: str,
    round_start: int,
    round_end: int,
    rounds: list[dict[str, Any]] | None = None,
) -> str:
    # Assistant/tool serialization differs between on_commit and archive recovery.
    # Identity belongs to the user evidence, not those derived representations.
    evidence = [{"round": item.get("round"), "committed_at": item.get("committed_at"),
                 "messages": [evidence_text(message.get("content"))
                              for message in item.get("messages", [])
                              if isinstance(message, dict)
                              and message.get("role", "user") == "user"]}
                for item in rounds or []]
    payload_digest = hashlib.sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    identity = "\x1f".join(
        (
            user,
            source,
            session_id,
            str(round_start),
            str(round_end),
            payload_digest,
        )
    )
    return "memory_batch_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def extract_round_memory(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    round_number: int,
    prompt: str,
    text: str,
    reasoning: str,
    tool_records: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
    agent_source: str = "",
    session_id: str = "",
    committed_at: str | None = None,
) -> dict[str, Any]:
    """Analyze and persist one completed round synchronously."""

    analysis = analyze_round_memory(
        round_number=round_number,
        prompt=prompt,
        text=text,
        reasoning=reasoning,
        tool_records=tool_records,
        agent_runner=agent_runner,
        cancel_event=cancel_event,
        agent_source=agent_source,
        session_id=session_id,
        committed_at=committed_at,
    )
    rounds = [memory_round_data(round_number=round_number, prompt=prompt, text=text,
                               reasoning=reasoning, tool_records=tool_records, committed_at=committed_at)]
    return persist_round_memory_analysis(
        root=root,
        user=user,
        config=config,
        analysis=analysis,
        operation_id=memory_batch_operation_id(user, agent_source, session_id, round_number, round_number, rounds),
    )


def extract_card_memory(*, root, user, source, session_id, config, agent_runner,
                        cancel_event=None, claim=None):
    """Extract a bounded immutable RAW seq range; compaction never sets coverage.

    The self_improve schema retains the label ``round`` for an evidence record
    ID, but here that ID is the original RAW seq, not a conversational round.
    No window, summary, thinking, or external tool text becomes user evidence.
    """
    from run.cards import CardConflictError, CardStore, session_key
    from run.history import (claim_pending_memory, defer_memory_claim, finish_memory_claim,
                             queue_memory_extraction)
    store = CardStore(root, user)
    base = {"user": user, "source": source, "session_id": session_id,
            "candidates": 0, "cursor_unit": "raw_seq"}
    if memory_extraction_mode(config) == "disabled":
        return {**base, "status": "skipped", "reason": "memory_extraction_disabled"}
    if claim is None:
        queue_memory_extraction(root, user, source, session_id, reason="manual_memory")
        from run.memory import memory_extraction_input_tokens
        claim = claim_pending_memory(root, user, source=source, session_id=session_id,
                                     input_tokens=memory_extraction_input_tokens(config))
    if claim is None:
        return {**base, "status": "skipped", "reason": "no_claimable_raw"}
    start, end = claim["memory_claim_start_seq"], claim["memory_claim_end_seq"]
    claim_id = claim["memory_claim_id"]
    identity = {**base, "seq_start": start, "seq_end": end, "claim_id": claim_id}
    try:
        from run.cards.models import Card
        from run.memory.evidence import narrative_record, narrative_rows
        with store.db() as db:
            count = db.execute("SELECT COUNT(*) FROM context_cards WHERE session_key=? AND seq BETWEEN ? AND ?",
                               (session_key(source, session_id), start, end)).fetchone()[0]
            if count != end - start + 1:
                raise CardConflictError("RAW 提取范围不完整，拒绝推进记忆游标")
            evidence = [item for row in narrative_rows(db, session_key(source, session_id), start, end)
                        if (item := narrative_record(Card.from_row(row))) is not None]
            # An already checkpointed partial integration may resume after many
            # tools. Supply its user context, but do not re-extract old evidence.
            integrations = {item["integration_id"] for item in evidence}
            previous = [item for row in narrative_rows(db, session_key(source, session_id), 1, start - 1)
                        if row["integration_id"] in integrations and row["raw_kind"] == "user_text"
                        and (item := narrative_record(Card.from_row(row))) is not None]
            evidence = [{**item, "context_only": True} for item in previous] + evidence
        evidence_source = {"source": "cardstream_raw", "channel": source,
            "session_id": session_id, "generation": claim["session_generation"],
            "seq_start": start, "seq_end": end, "evidence_unit": "raw_seq"}
        operation = memory_batch_operation_id(user, source, session_id, start, end, evidence)
        from run.memory.work import WorkLedger
        candidate_work = WorkLedger(root, user).get("candidate:" + operation)
        if candidate_work is not None:
            # A bounded local persistence pause must not generate a new model
            # analysis or change the already accepted candidate set.
            accepted_candidates = candidate_work["payload"]["candidates"]
            analysis = {"status": "completed", "candidate_count": len(accepted_candidates),
                        "candidates": accepted_candidates, "source": candidate_work["source"]}
        else:
            analysis = (analyze_memory_batch_resilient(rounds=evidence, agent_runner=agent_runner,
                    cancel_event=cancel_event, source=evidence_source,
                    agent_source=source, session_id=session_id) if evidence else
                    {"status": "completed", "candidate_count": 0, "candidates": [], "source": evidence_source})
        if analysis.get("status") != "completed":
            error = analysis.get("error") or {"message": "记忆提取未完成"}
            finish = defer_memory_claim if error.get("work_pending") or error.get("capacity_blocked") else finish_memory_claim
            finish(root, user, source, session_id, claim_id=claim_id, error=error)
            return {**identity, "status": "failed", "error": error}
        # Hold only the short control-plane transaction during idempotent local
        # memory persistence. A deleted/stolen/expired claim cannot apply a late
        # model result or move a different owner's cursor.
        with store.db(write=True) as db:
            state = db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                               (session_key(source, session_id),)).fetchone()
            record = json.loads(state["record_json"]) if state else {}
            expires = datetime.fromisoformat(record.get("memory_claim_expires_at") or "0001-01-01T00:00:00+00:00")
            if (state is None or state["lifecycle"] == "deleted"
                    or state["generation"] != claim["session_generation"]
                    or record.get("memory_claim_id") != claim_id
                    or state["memory_processed_seq"] != start - 1
                    or expires.tzinfo is None or expires <= datetime.now(timezone.utc)):
                raise CardConflictError("记忆认领已经失效，未写入过期结果")
            result = persist_round_memory_analysis(root=root, user=user, config=config,
                                                   analysis=analysis, operation_id=operation)
            error = result.get("error") if result.get("status") != "completed" else None
            finish = (defer_memory_claim
                      if error and (error.get("work_pending") or error.get("capacity_blocked"))
                      else finish_memory_claim)
            finished = finish(root, user, source, session_id, claim_id=claim_id,
                              error=error, transaction=db)
        return {**identity, **result, "candidates": result.get("candidate_count", 0),
                "processed_seq": finished["memory_processed_seq"] if finished else start - 1}
    except Exception as error:
        detail = {"message": str(error), "exception_type": type(error).__name__}
        finish_memory_claim(root, user, source, session_id, claim_id=claim_id, error=detail)
        return {**identity, "status": "failed", "error": detail}
