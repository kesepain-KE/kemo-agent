"""Active-session, summary-job, and session-list operations."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import uuid

from run.history.index_core import (
    SUMMARY_CLAIM_STALE_SECONDS,
    SUMMARY_DEFAULT_MAX_ATTEMPTS,
    SUMMARY_DEFAULT_RETRY_DELAYS,
    _active_matches,
    _active_record,
    _active_reference,
    _load_index_unlocked,
    _now,
    _timestamp,
    _write_index_unlocked,
    index_lock,
    load_index,
    session_key,
)
from run.history.store import claim_registry_record, connection, query_session_records


def set_active(
    root: Path,
    user: str,
    active_key: str,
    session_id: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        normalized_source = str(source or "")
        if not normalized_source:
            matches = [
                record
                for record in index.setdefault("sessions", {}).values()
                if isinstance(record, dict) and record.get("session_id") == session_id
            ]
            if len(matches) != 1:
                raise ValueError("设置活跃会话时必须提供可唯一定位的 source")
            normalized_source = str(matches[0].get("source") or "")
        index.setdefault("active", {})[active_key] = _active_reference(
            normalized_source, session_id
        )
        return _write_index_unlocked(root, user, index)


def get_active(root: Path, user: str, active_key: str) -> dict[str, Any] | None:
    index = load_index(root, user)
    record = _active_record(
        index.get("sessions") or {},
        (index.get("active") or {}).get(active_key),
    )
    return copy.deepcopy(record) if isinstance(record, dict) else None


def close_session(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        record["lifecycle"] = "closed"
        record["run_state"] = "idle"
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if _active_matches(active_session, source, session_id):
                index["active"].pop(active_key, None)
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def update_title(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    title: str,
) -> dict[str, Any] | None:
    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        record["title"] = title
        record["title_source"] = "manual"
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def queue_summary(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any]:
    """Queue card metadata generation after a session is durably closed."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return {"status": "skipped", "reason": "session_not_found", "rounds": 0}
        target_round = max(
            0, int(record.get("last_committed_round") or record.get("rounds") or 0)
        )
        if record.get("lifecycle") != "closed":
            return {
                "status": "skipped",
                "reason": "session_not_closed",
                "rounds": target_round,
            }
        if target_round < 1 or not record.get("archive_window"):
            record["summary_status"] = "none"
            record["summary_target_round"] = target_round
            index["sessions"][key] = record
            _write_index_unlocked(root, user, index)
            return {
                "status": "skipped",
                "reason": "no_archive_rounds",
                "rounds": target_round,
            }
        completed_round = max(0, int(record.get("summary_completed_round") or 0))
        if completed_round >= target_round and str(record.get("summary") or "").strip():
            return {
                "status": "completed",
                "reason": "already_current",
                "rounds": target_round,
            }
        record["summary_status"] = "queued"
        record["summary_target_round"] = target_round
        record["summary_state_updated_at"] = _now()
        record["summary_retry_count"] = 0
        record["summary_consecutive_failures"] = 0
        for field in (
            "summary_claim_id",
            "summary_claimed_at",
            "summary_retry_at",
            "summary_error",
            "summary_checkpoint",
            "summary_checkpoint_next_chunk",
            "summary_checkpoint_total_chunks",
        ):
            record.pop(field, None)
        index["sessions"][key] = record
        _write_index_unlocked(root, user, index)
        return {"status": "queued", "reason": "session_closed", "rounds": target_round}


def claim_pending_summary(
    root: Path,
    user: str,
    *,
    worker_id: str | None = None,
    stale_after_seconds: float = SUMMARY_CLAIM_STALE_SECONDS,
) -> dict[str, Any] | None:
    """Atomically lease one closed-session summary job."""

    now = datetime.now(timezone.utc)
    claim_id = worker_id or f"summary_{uuid.uuid4().hex}"

    def eligible(record: dict[str, Any]) -> bool:
        if record.get("lifecycle") != "closed":
            return False
        target_round = max(0, int(record.get("summary_target_round") or 0))
        completed_round = max(0, int(record.get("summary_completed_round") or 0))
        if (
            target_round < 1
            or completed_round >= target_round
            or not record.get("archive_window")
        ):
            return False
        status = str(record.get("summary_status") or "none")
        if status == "processing":
            claimed_at = _timestamp(record.get("summary_claimed_at"))
            return claimed_at is None or (now - claimed_at).total_seconds() >= max(
                1.0, stale_after_seconds
            )
        if status in {"failed", "retry_wait"}:
            retry_at = _timestamp(record.get("summary_retry_at"))
            return retry_at is None or now >= retry_at
        return status == "queued"

    def claim(record: dict[str, Any]) -> dict[str, Any]:
        record["summary_status"] = "processing"
        record["summary_claim_id"] = claim_id
        record["summary_claimed_at"] = now.isoformat()
        record["summary_last_attempt_at"] = now.isoformat()
        record["summary_attempt_count"] = (
            max(0, int(record.get("summary_attempt_count") or 0)) + 1
        )
        record["summary_state_updated_at"] = now.isoformat()
        return record

    return claim_registry_record(
        root,
        user,
        status_column="summary_status",
        statuses={"queued", "processing", "failed", "retry_wait"},
        predicate=eligible,
        mutator=claim,
        updated_at=now.isoformat(),
    )


def finish_summary_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    title: str | None = None,
    summary: str | None = None,
    completed_round: int | None = None,
    error: dict[str, Any] | None = None,
    max_attempts: int = SUMMARY_DEFAULT_MAX_ATTEMPTS,
    retry_delays: tuple[int, ...] = SUMMARY_DEFAULT_RETRY_DELAYS,
) -> dict[str, Any] | None:
    """Finish a summary lease without allowing stale workers to overwrite data."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        for field in ("summary_claim_id", "summary_claimed_at"):
            record.pop(field, None)
        now = datetime.now(timezone.utc)
        if error is not None:
            retry_count = max(0, int(record.get("summary_retry_count") or 0)) + 1
            record["summary_max_attempts"] = max(1, int(max_attempts))
            record["summary_retry_count"] = retry_count
            record["summary_consecutive_failures"] = retry_count
            record["summary_error"] = copy.deepcopy(error)
            record["summary_last_error"] = copy.deepcopy(error)
            if retry_count >= max(1, int(max_attempts)):
                record["summary_status"] = "exhausted"
                record.pop("summary_retry_at", None)
            else:
                delays = tuple(max(1, int(value)) for value in retry_delays) or (30,)
                delay = delays[min(retry_count - 1, len(delays) - 1)]
                record["summary_status"] = "retry_wait"
                record["summary_retry_at"] = (
                    now + timedelta(seconds=delay)
                ).isoformat()
        else:
            normalized_title = str(title or "").strip()
            normalized_summary = str(summary or "").strip()
            if (
                not normalized_title
                or not normalized_summary
                or completed_round is None
            ):
                return None
            if (
                not str(record.get("title") or "").strip()
                or record.get("title_source") == "auto"
            ):
                record["title"] = normalized_title
                record["title_source"] = "auto"
            record["summary"] = normalized_summary
            record["summary_status"] = "completed"
            record["summary_max_attempts"] = max(1, int(max_attempts))
            record["summary_completed_round"] = max(0, int(completed_round))
            record["summary_updated_at"] = now.isoformat()
            if max(0, int(record.get("summary_attempt_count") or 0)) > 1:
                record["summary_recovered_at"] = now.isoformat()
            record["summary_retry_count"] = 0
            record["summary_consecutive_failures"] = 0
            record.pop("summary_retry_at", None)
            record.pop("summary_error", None)
            record.pop("summary_checkpoint", None)
            record.pop("summary_checkpoint_next_chunk", None)
            record.pop("summary_checkpoint_total_chunks", None)
        record["summary_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def defer_summary_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    error: dict[str, Any],
    delay_seconds: int = 30,
) -> dict[str, Any] | None:
    """Release a lease without consuming an automatic retry attempt."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        record.pop("summary_claim_id", None)
        record.pop("summary_claimed_at", None)
        record["summary_attempt_count"] = max(
            0, int(record.get("summary_attempt_count") or 0) - 1
        )
        now = datetime.now(timezone.utc)
        record["summary_status"] = "retry_wait"
        record["summary_retry_at"] = (
            now + timedelta(seconds=max(1, int(delay_seconds)))
        ).isoformat()
        record["summary_error"] = copy.deepcopy(error)
        record["summary_last_error"] = copy.deepcopy(error)
        record["summary_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def update_summary_checkpoint(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    next_chunk: int,
    total_chunks: int,
    title: str,
    summary: str,
) -> dict[str, Any] | None:
    """Persist rolling summary progress while the current lease is valid."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        record["summary_checkpoint"] = {
            "title": str(title).strip(),
            "summary": str(summary).strip(),
            "target_round": max(0, int(record.get("summary_target_round") or 0)),
        }
        record["summary_checkpoint_next_chunk"] = max(0, int(next_chunk))
        record["summary_checkpoint_total_chunks"] = max(0, int(total_chunks))
        record["summary_state_updated_at"] = _now()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def retry_summary(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Immediately requeue one incomplete closed-session summary."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        target_round = max(0, int(record.get("summary_target_round") or 0))
        completed_round = max(0, int(record.get("summary_completed_round") or 0))
        status = str(record.get("summary_status") or "none")
        if (
            record.get("lifecycle") != "closed"
            or target_round < 1
            or completed_round >= target_round
            or not record.get("archive_window")
            or status not in {"failed", "retry_wait", "exhausted"}
        ):
            return None
        record["summary_status"] = "queued"
        record["summary_retry_count"] = 0
        record["summary_consecutive_failures"] = 0
        record["summary_state_updated_at"] = _now()
        for field in (
            "summary_claim_id",
            "summary_claimed_at",
            "summary_retry_at",
            "summary_error",
        ):
            record.pop(field, None)
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def remove_session(root: Path, user: str, source: str, session_id: str) -> None:
    with index_lock(root, user):
        # Keep a durable delete fence so an in-flight terminal writer cannot
        # recreate this session after the registry row is removed.
        from run.history.store import connection

        with connection(root, user, write=True) as database:
            database.execute(
                """
                INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
                VALUES(?, ?, datetime('now'))
                ON CONFLICT(source, session_id) DO UPDATE SET
                    deleted_at=excluded.deleted_at
                """,
                (str(source), str(session_id)),
            )
        index = _load_index_unlocked(root, user)
        index.setdefault("sessions", {}).pop(session_key(source, session_id), None)
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if _active_matches(active_session, source, session_id):
                index["active"].pop(active_key, None)
        _write_index_unlocked(root, user, index)


def remove_all_sessions(root: Path, user: str, source: str) -> int:
    with index_lock(root, user):
        from run.history.store import connection

        index = _load_index_unlocked(root, user)
        sessions = index.setdefault("sessions", {})
        targets = [
            (key, record)
            for key, record in sessions.items()
            if isinstance(record, dict) and record.get("source") == source
        ]
        target_ids = {str(record.get("session_id") or "") for _, record in targets}
        with connection(root, user, write=True) as database:
            database.executemany(
                """
                INSERT INTO history_deleted_sessions(source, session_id, deleted_at)
                VALUES(?, ?, datetime('now'))
                ON CONFLICT(source, session_id) DO UPDATE SET
                    deleted_at=excluded.deleted_at
                """,
                [(str(source), session_id) for session_id in target_ids if session_id],
            )
        for key, _ in targets:
            sessions.pop(key, None)
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if any(
                _active_matches(active_session, source, session_id)
                for session_id in target_ids
            ):
                index["active"].pop(active_key, None)
        if targets:
            _write_index_unlocked(root, user, index)
        return len(targets)


def list_records(
    root: Path,
    user: str,
    *,
    source: str | None = None,
    query: str = "",
    limit: int | None = None,
    before_updated_at: str = "",
) -> list[dict[str, Any]]:
    records, _ = query_session_records(
        root,
        user,
        source=source,
        query=query,
        limit=limit,
        before_updated_at=before_updated_at,
    )
    return [copy.deepcopy(record) for record in records]


def list_records_page(
    root: Path,
    user: str,
    *,
    source: str | None = None,
    query: str = "",
    limit: int = 50,
    before_updated_at: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    records, has_more = query_session_records(
        root,
        user,
        source=source,
        query=query,
        limit=max(1, min(100, int(limit))),
        before_updated_at=before_updated_at,
    )
    return [copy.deepcopy(record) for record in records], has_more
