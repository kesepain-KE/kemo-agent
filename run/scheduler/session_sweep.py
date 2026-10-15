"""Close long-idle conversation sessions and queue their memory work."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any, Collection

from run.config import list_users
from run.conversation import session_lock
from run.history import (
    close_session,
    connection,
    database_path,
    find_record,
    list_records,
    queue_summary,
    queue_memory_extraction,
)

SESSION_SWEEP_INTERVAL_SECONDS = 3600
SESSION_IDLE_CLOSE_SECONDS = 24 * 3600


def _timestamp(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OverflowError):
        return None


def _session_lease_active(root: Path, user: str, source: str,
                          session_id: str, now: float) -> bool:
    if not database_path(root, user).is_file():
        return False
    try:
        with connection(root, user) as database:
            if source == 'web':
                scope_sql, scope_value = "client_id NOT LIKE ?", '%\x1f%'
            else:
                scope_sql, scope_value = "client_id LIKE ?", f'{source}\x1f%'
            row = database.execute(
                f"SELECT 1 FROM history_web_leases WHERE session_id=? AND expires_at>? AND {scope_sql} LIMIT 1",
                (session_id, now, scope_value),
            ).fetchone()
        return row is not None
    except Exception:
        return True


def sweep_idle_sessions(root: Path, user: str, *, idle_seconds: int | float = SESSION_IDLE_CLOSE_SECONDS,
                        now: float | None = None, limit: int = 50,
                        sources: Collection[str] | None = None,
                        queue_reason: str = "idle_session_sweep") -> dict[str, Any]:
    """Close at most ``limit`` stale sessions for one user."""
    current = time.time() if now is None else float(now)
    cutoff = current - max(1.0, float(idle_seconds))
    result: dict[str, Any] = {"user": user, "scanned": 0, "closed": 0,
                              "requeued_closed": 0, "queued_memory": 0,
                              "queued_summary": 0,
                              "skipped_active": 0,
                              "errors": [], "closed_sessions": []}
    try:
        # Read the complete registry so very old sessions are not hidden behind
        # the newest-page limit; the close batch itself remains capped below.
        records = list_records(root, user, limit=None)
    except Exception as exc:
        result["errors"].append({"exception_type": type(exc).__name__})
        return result
    def count(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError, OverflowError):
            return 0

    def pending_memory(record: dict[str, Any]) -> bool:
        rounds = count(record.get("rounds"))
        processed = count(record.get("memory_processed_round"))
        status = str(record.get("memory_status") or "unknown").casefold()
        return (str(record.get("lifecycle") or "") == "closed"
                and rounds > processed
                and status not in {"queued", "processing", "failed", "completed", "disabled"})

    def pending_summary(record: dict[str, Any]) -> bool:
        rounds = max(
            count(record.get("last_committed_round")),
            count(record.get("rounds")),
        )
        return (
            str(record.get("lifecycle") or "") == "closed"
            and rounds > 0
            and bool(str(record.get("archive_window") or ""))
            and not str(record.get("title") or "").strip()
            and str(record.get("summary_status") or "none").casefold() == "none"
        )

    allowed_sources = {str(value) for value in sources} if sources is not None else None
    candidates = [record for record in records if isinstance(record, dict)
                  and (allowed_sources is None or str(record.get("source") or "") in allowed_sources)
                  and (
        pending_memory(record)
        or pending_summary(record)
        or (str(record.get("lifecycle") or "") not in {"closed", "deleted"}
            and (_timestamp(record.get("updated_at")) or float("inf")) <= cutoff)
    )]
    for record in candidates[:max(1, min(500, int(limit)))]:
        source = str(record.get("source") or "")
        session_id = str(record.get("session_id") or "")
        if not source or not session_id:
            continue
        result["scanned"] += 1
        if str(record.get("run_state") or "idle").casefold() in {"running", "queued", "pending", "starting", "stopping"}:
            result["skipped_active"] += 1
            continue
        if _session_lease_active(root, user, source, session_id, current):
            result["skipped_active"] += 1
            continue
        lock = session_lock(root, user, source, session_id)
        if not lock.acquire(blocking=False):
            result["skipped_active"] += 1
            continue
        try:
            latest = find_record(root, user, source, session_id)
            if not isinstance(latest, dict) or str(latest.get("lifecycle") or "") == "deleted":
                continue
            memory_recovery = pending_memory(latest)
            summary_recovery = pending_summary(latest)
            lifecycle_closed = str(latest.get("lifecycle") or "") == "closed"
            if not memory_recovery and not summary_recovery and lifecycle_closed:
                continue
            if not memory_recovery and not summary_recovery and (_timestamp(latest.get("updated_at")) or float("inf")) > cutoff:
                continue
            if str(latest.get("run_state") or "idle").casefold() in {"running", "queued", "pending", "starting", "stopping"}:
                result["skipped_active"] += 1
                continue
            queued: dict[str, Any] | None = None
            queue_error: Exception | None = None
            if not lifecycle_closed or memory_recovery:
                try:
                    queued = queue_memory_extraction(
                        root, user, source, session_id, reason=queue_reason
                    )
                except Exception as exc:
                    queue_error = exc
            closed = latest
            if not lifecycle_closed:
                try:
                    closed = close_session(root, user, source, session_id)
                except Exception as exc:
                    result["errors"].append({
                        "source": source, "session_id": session_id,
                        "exception_type": type(exc).__name__,
                    })
                    continue
            summary_queued: dict[str, Any] | None = None
            if closed is not None and (not lifecycle_closed or summary_recovery):
                try:
                    summary_queued = queue_summary(
                        root, user, source, session_id
                    )
                except Exception as exc:
                    result["errors"].append({
                        "source": source, "session_id": session_id,
                        "exception_type": type(exc).__name__,
                    })
            if queue_error is not None:
                result["errors"].append({
                    "source": source, "session_id": session_id,
                    "exception_type": type(queue_error).__name__,
                })
            if closed is not None:
                if str((queued or {}).get("status") or "") == "queued":
                    result["queued_memory"] += 1
                if str((summary_queued or {}).get("status") or "") == "queued":
                    result["queued_summary"] += 1
                if memory_recovery:
                    if str((queued or {}).get("status") or "") == "queued":
                        result["requeued_closed"] += 1
                elif not lifecycle_closed:
                    result["closed"] += 1
                if len(result["closed_sessions"]) < 20:
                    result["closed_sessions"].append({"source": source, "session_id": session_id,
                                                       "memory_status": str((queued or {}).get("status") or "unknown"),
                                                       "summary_status": str((summary_queued or {}).get("status") or "unknown")})
        except Exception as exc:
            result["errors"].append({"source": source, "session_id": session_id,
                                      "exception_type": type(exc).__name__})
        finally:
            lock.release()
    return result


def sweep_all_users(root: Path, *, idle_seconds: int | float = SESSION_IDLE_CLOSE_SECONDS,
                    now: float | None = None, limit: int = 50) -> dict[str, Any]:
    results = [sweep_idle_sessions(root, user, idle_seconds=idle_seconds, now=now, limit=limit)
               for user in list_users(root)]
    return {"status": "completed" if not any(item.get("errors") for item in results) else "partial",
            "scanned": sum(int(item.get("scanned") or 0) for item in results),
            "closed": sum(int(item.get("closed") or 0) for item in results),
            "requeued_closed": sum(int(item.get("requeued_closed") or 0) for item in results),
            "queued_memory": sum(int(item.get("queued_memory") or 0) for item in results),
            "queued_summary": sum(int(item.get("queued_summary") or 0) for item in results),
            "skipped_active": sum(int(item.get("skipped_active") or 0) for item in results),
            "errors": [error for item in results for error in item.get("errors", [])],
            "users": results}


__all__ = ["SESSION_IDLE_CLOSE_SECONDS", "SESSION_SWEEP_INTERVAL_SECONDS", "sweep_all_users", "sweep_idle_sessions"]
