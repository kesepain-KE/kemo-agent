"""Conversation-memory analysis and durable extraction coordination."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import threading
from typing import Any

from run.agents import AgentRunner
from run.infra import EngineError
from run.history import commit_window
from run.history import find_record, update_memory_state
from run.memory import (
    MemoryStore,
    memory_extraction_batch_rounds,
    memory_extraction_mode,
)
from run.memory.pipeline import memory_round_payload
from run.conversation import new_usage_total, record_provider_request, usage_from_dict


def memory_round_data(
    *,
    round_number: int,
    prompt: str,
    text: str,
    reasoning: str,
    tool_records: list[dict[str, Any]],
) -> dict[str, Any]:
    round_data: dict[str, Any] = {
        "round": round_number,
        "messages": [
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": text},
        ],
    }
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
) -> dict[str, Any]:
    """Analyze a bounded batch of completed rounds without mutating state."""

    if not rounds:
        raise EngineError("记忆批量分析至少需要一轮对话")
    round_numbers = [max(0, int(item.get("round") or 0)) for item in rounds]
    if any(number < 1 for number in round_numbers):
        raise EngineError("记忆批量分析包含无效轮次")
    effective_source = dict(
        source
        or {
            "source": "round_commit",
            "round_start": min(round_numbers),
            "round_end": max(round_numbers),
        }
    )
    try:
        result = agent_runner.run(
            "self_improve",
            {
                "trigger": "context_compression",
                "rounds": rounds,
                "source": effective_source,
            },
            cancel_event=cancel_event,
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
            "error": None,
        }
    except Exception as exc:
        return {
            "status": "failed",
            "candidate_count": 0,
            "round_start": min(round_numbers),
            "round_end": max(round_numbers),
            "rounds": round_numbers,
            "error": {
                "message": str(exc),
                "exception_type": type(exc).__name__,
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
            )
        ],
        agent_runner=agent_runner,
        cancel_event=cancel_event,
        source={"source": "round_commit", "round": round_number},
    )


def analyze_memory_batch_resilient(
    *,
    rounds: list[dict[str, Any]],
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
    source: dict[str, Any] | None = None,
    retry_once: bool = True,
) -> dict[str, Any]:
    """Retry malformed batch output once, then isolate it by contiguous halves."""

    analysis = analyze_memory_batch(
        rounds=rounds,
        agent_runner=agent_runner,
        cancel_event=cancel_event,
        source=source,
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

    midpoint = max(1, len(rounds) // 2)
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
        "source": dict(source or {}),
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
    try:
        deduplicated: dict[str, dict[str, Any]] = {}
        unkeyed: list[dict[str, Any]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                unkeyed.append(candidate)
                continue
            raw_key = candidate.get("filename") or candidate.get("target")
            key = str(raw_key or "").strip().casefold()
            if key:
                deduplicated[key] = candidate
            else:
                unkeyed.append(candidate)
        persisted_candidates = [*deduplicated.values(), *unkeyed]
        persisted = MemoryStore(root, user, config).upsert_candidates(
            persisted_candidates,
            source=source,
            operation_id=operation_id,
        )
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
    payload_digest = hashlib.sha256(
        json.dumps(
            rounds or [],
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
    )
    return persist_round_memory_analysis(
        root=root,
        user=user,
        config=config,
        analysis=analysis,
    )


def extract_memory_backlog(
    *,
    root: Path,
    user: str,
    source: str,
    session_id: str,
    directory: Path,
    window: dict[str, Any],
    config: dict[str, Any],
    agent_runner: AgentRunner | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Extract every unprocessed committed round and advance one durable cursor."""

    data = window.setdefault("data", {})
    rounds = max(0, int(data.get("rounds") or 0))
    base_result: dict[str, Any] = {
        "user": user,
        "source": source,
        "session_id": session_id,
        "round": rounds,
        "candidates": 0,
        "extraction": None,
        "extractions": [],
        "usage": new_usage_total(),
        "index_error": None,
    }
    if rounds < 1:
        return {**base_result, "status": "skipped", "reason": "no_complete_round"}

    indexed_session = find_record(root, user, source, session_id)
    if isinstance(indexed_session, dict) and (
        indexed_session.get("memory_claim_id")
        or indexed_session.get("memory_queue_reason") == "manual_compression"
    ):
        return {
            **base_result,
            "status": "skipped",
            "reason": "background_extraction_in_progress",
        }

    extraction_mode = memory_extraction_mode(config)
    if extraction_mode == "disabled":
        return {
            **base_result,
            "status": "skipped",
            "reason": "memory_extraction_disabled",
            "mode": extraction_mode,
        }

    raw_cursor = data.get("memory_processed_round")
    processed_round = (
        max(0, int(raw_cursor or 0))
        if raw_cursor is not None
        else max(0, rounds - 1)
    )
    if processed_round >= rounds:
        return {**base_result, "status": "skipped", "reason": "already_processed"}
    if agent_runner is None:
        raise EngineError("记忆提取缺少 AgentRunner")

    data["memory_processed_round"] = processed_round
    data["memory_status"] = "processing"
    data.pop("memory_error", None)
    commit_window(directory, window)
    index_error: dict[str, Any] | None = None
    try:
        update_memory_state(root, user, source, session_id, status="processing")
    except Exception as exc:
        index_error = {"message": str(exc), "exception_type": type(exc).__name__}

    extractions: list[dict[str, Any]] = []
    candidates = 0
    usage = new_usage_total()
    batch_size = memory_extraction_batch_rounds(config)
    non_extractable_rounds = {
        int(item.get("round") or 0): str(item.get("status") or "")
        for item in data.get("round_metrics", [])
        if isinstance(item, dict)
        and item.get("status") in {"cancelled", "failed"}
    }
    batch_start = processed_round + 1
    while batch_start <= rounds:
        batch_end = min(rounds, batch_start + batch_size - 1)
        batch_rounds: list[dict[str, Any]] = []
        skipped_rounds: list[int] = []
        for round_number in range(batch_start, batch_end + 1):
            if round_number in non_extractable_rounds:
                skipped_rounds.append(round_number)
                continue
            payload = memory_round_payload(window, round_number)
            batch_rounds.append(memory_round_data(round_number=round_number, **payload))

        if not batch_rounds:
            skipped_statuses = {
                non_extractable_rounds[number] for number in skipped_rounds
            }
            extraction = {
                "status": "skipped",
                "candidate_count": 0,
                "reason": (
                    "cancelled_rounds"
                    if skipped_statuses == {"cancelled"}
                    else (
                        "failed_rounds"
                        if skipped_statuses == {"failed"}
                        else "non_extractable_rounds"
                    )
                ),
                "round_start": batch_start,
                "round_end": batch_end,
                "rounds": [],
                "skipped_rounds": skipped_rounds,
            }
        else:
            operation_id = memory_batch_operation_id(
                user, source, session_id, batch_start, batch_end, batch_rounds
            )
            batch_source = {
                "source": "round_commit",
                "channel": source,
                "session_id": session_id,
                "round_start": batch_start,
                "round_end": batch_end,
            }
            try:
                analysis = analyze_memory_batch_resilient(
                    rounds=batch_rounds,
                    source=batch_source,
                    agent_runner=agent_runner,
                    cancel_event=cancel_event,
                )
                extraction = persist_round_memory_analysis(
                    root=root,
                    user=user,
                    config=config,
                    analysis=analysis,
                    operation_id=operation_id,
                )
                extraction["skipped_rounds"] = skipped_rounds
            except Exception as exc:
                extraction = {
                    "status": "failed",
                    "candidate_count": 0,
                    "round_start": batch_start,
                    "round_end": batch_end,
                    "rounds": [int(item["round"]) for item in batch_rounds],
                    "skipped_rounds": skipped_rounds,
                    "error": {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    },
                }

        extractions.append(extraction)
        extraction_usage = extraction.get("usage")
        if isinstance(extraction_usage, dict):
            record_provider_request(usage, usage_from_dict(extraction_usage))
        if extraction.get("status") not in {"completed", "skipped"}:
            error = (
                extraction.get("error")
                if isinstance(extraction.get("error"), dict)
                else {"message": "记忆提取失败"}
            )
            data["memory_status"] = "failed"
            data["memory_error"] = error
            commit_window(directory, window)
            try:
                update_memory_state(
                    root, user, source, session_id, status="failed", error=error
                )
            except Exception as exc:
                index_error = index_error or {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            return {
                **base_result,
                "status": "failed",
                "round": batch_start,
                "round_start": batch_start,
                "round_end": batch_end,
                "candidates": candidates,
                "extraction": extraction,
                "extractions": extractions,
                "usage": usage,
                "index_error": index_error,
            }

        candidates += int(extraction.get("candidate_count") or 0)
        data["memory_processed_round"] = batch_end
        data["memory_status"] = "completed" if batch_end >= rounds else "processing"
        data.pop("memory_error", None)
        commit_window(directory, window)
        try:
            update_memory_state(
                root,
                user,
                source,
                session_id,
                processed_round=batch_end,
                status=str(data["memory_status"]),
            )
        except Exception as exc:
            index_error = index_error or {
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
        batch_start = batch_end + 1
    return {
        **base_result,
        "status": "completed",
        "candidates": candidates,
        "extraction": extractions[-1],
        "extractions": extractions,
        "usage": usage,
        "index_error": index_error,
    }
