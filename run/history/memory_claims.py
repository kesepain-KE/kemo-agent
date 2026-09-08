"""Memory-extraction claim lifecycle for the history registry."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from run.history.index_core import (
    MEMORY_CLAIM_STALE_SECONDS,
    MEMORY_RETRY_DELAY_SECONDS,
    _load_index_unlocked,
    _now,
    _timestamp,
    _write_index_unlocked,
    index_lock,
    session_key,
)
from run.history.store import claim_registry_record


def _claim_is_stale(
    record: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    timestamp = _timestamp(
        record.get("memory_claimed_at")
        or record.get("memory_state_updated_at")
        or record.get("run_state_updated_at")
        or record.get("updated_at")
    )
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() >= max(1.0, stale_after_seconds)


def _run_is_stale(
    record: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    timestamp = _timestamp(
        record.get("run_state_updated_at") or record.get("updated_at")
    )
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() >= max(1.0, stale_after_seconds)


def claim_pending_memory(
    root: Path,
    user: str,
    *,
    worker_id: str | None = None,
    stale_after_seconds: float = MEMORY_CLAIM_STALE_SECONDS,
    statuses: set[str] | frozenset[str] | None = None,
    max_rounds: int = 1,
) -> dict[str, Any] | None:
    """Atomically lease the next contiguous range of committed rounds."""

    claimable_statuses = set(statuses or {"pending", "failed", "processing"})
    if isinstance(max_rounds, bool):
        raise ValueError("max_rounds 必须是正整数")
    try:
        batch_size = max(1, min(20, int(max_rounds)))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_rounds 必须是正整数") from exc
    now = datetime.now(timezone.utc)
    claim_id = worker_id or f"memory_{uuid.uuid4().hex}"

    def eligible(record: dict[str, Any]) -> bool:
        try:
            processed_round = max(0, int(record.get("memory_processed_round") or 0))
            committed_round = max(0, int(record.get("last_committed_round") or 0))
        except (TypeError, ValueError):
            return False
        try:
            target_round = max(0, int(record.get("memory_target_round") or 0))
        except (TypeError, ValueError):
            target_round = 0
        claim_limit = (
            min(committed_round, target_round) if target_round else committed_round
        )
        if processed_round >= claim_limit or not record.get("archive_window"):
            return False
        status = str(record.get("memory_status") or "pending")
        if status == "processing" and not _claim_is_stale(
            record, now=now, stale_after_seconds=stale_after_seconds
        ):
            return False
        if status == "failed" and not _claim_is_stale(
            record,
            now=now,
            stale_after_seconds=min(stale_after_seconds, MEMORY_RETRY_DELAY_SECONDS),
        ):
            return False
        if record.get("run_state") == "running" and not _run_is_stale(
            record, now=now, stale_after_seconds=stale_after_seconds
        ):
            return False
        return True

    def claim(record: dict[str, Any]) -> dict[str, Any]:
        next_round = max(0, int(record.get("memory_processed_round") or 0)) + 1
        committed_round = max(0, int(record.get("last_committed_round") or 0))
        target_round = max(0, int(record.get("memory_target_round") or 0))
        claim_limit = (
            min(committed_round, target_round) if target_round else committed_round
        )
        end_round = min(claim_limit, next_round + batch_size - 1)
        record["memory_status"] = "processing"
        record["memory_claim_id"] = claim_id
        record["memory_claimed_at"] = now.isoformat()
        # Keep the legacy field pinned to the first round so an older worker
        # cannot accidentally skip the beginning of a range claim.
        record["memory_claim_round"] = next_round
        record["memory_claim_start_round"] = next_round
        record["memory_claim_end_round"] = end_round
        record["memory_state_updated_at"] = now.isoformat()
        return record

    return claim_registry_record(
        root,
        user,
        status_column="memory_status",
        statuses=claimable_statuses,
        predicate=eligible,
        mutator=claim,
        updated_at=now.isoformat(),
    )


def finish_memory_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    processed_round: int | None = None,
    error: dict[str, Any] | None = None,
    remaining_status: str = "pending",
) -> dict[str, Any] | None:
    """Finish a memory lease; stale workers cannot overwrite a newer claim."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("memory_claim_id") != claim_id:
            return None
        if processed_round is not None:
            record["memory_processed_round"] = max(
                int(record.get("memory_processed_round") or 0),
                int(processed_round),
            )
        claimed_round = max(0, int(record.get("memory_claim_round") or 0))
        claimed_start = max(
            0,
            int(record.get("memory_claim_start_round") or claimed_round),
        )
        claimed_end = max(
            claimed_start,
            int(record.get("memory_claim_end_round") or claimed_round),
        )
        for field in (
            "memory_claim_id",
            "memory_claimed_at",
            "memory_claim_round",
            "memory_claim_start_round",
            "memory_claim_end_round",
        ):
            record.pop(field, None)
        if error is not None:
            previous_error = record.get("memory_last_error")
            retry_count = (
                max(0, int(previous_error.get("retry_count") or 0)) + 1
                if isinstance(previous_error, dict)
                else 1
            )
            diagnostic = {
                **copy.deepcopy(error),
                "round": claimed_start,
                "round_start": claimed_start,
                "round_end": claimed_end,
                "occurred_at": _now(),
                "retry_count": retry_count,
            }
            record["memory_status"] = "failed"
            record["memory_error"] = diagnostic
            record["memory_last_error"] = copy.deepcopy(diagnostic)
        else:
            record.pop("memory_error", None)
            current_round = max(0, int(record.get("memory_processed_round") or 0))
            committed_round = max(0, int(record.get("last_committed_round") or 0))
            target_round = max(0, int(record.get("memory_target_round") or 0))
            if target_round and current_round >= target_round:
                for field in (
                    "memory_queue_reason",
                    "memory_target_round",
                    "memory_queued_at",
                ):
                    record.pop(field, None)
                record["memory_status"] = (
                    "completed" if current_round >= committed_round else "deferred"
                )
            else:
                record["memory_status"] = (
                    "completed"
                    if current_round >= committed_round
                    else remaining_status
                )
        record["memory_state_updated_at"] = _now()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]
