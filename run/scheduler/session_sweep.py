"""Close long-idle conversation sessions and queue their memory work."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import time
from typing import Any

from run.config import list_users
from run.conversation import session_lock
from run.history import (
    close_session,
    connection,
    database_path,
    find_record,
    list_records,
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


def _web_lease_active(root: Path, user: str, session_id: str, now: float) -> bool:
    if not database_path(root, user).is_file():
        return False
    try:
        with connection(root, user) as database:
            row = database.execute(
                "SELECT 1 FROM history_web_leases WHERE session_id=? AND expires_at>? LIMIT 1",
                (session_id, now),
            ).fetchone()
        return row is not None
    except Exception:
        return True


def sweep_idle_sessions(root: Path, user: str, *, idle_seconds: int | float = SESSION_IDLE_CLOSE_SECONDS,
                        now: float | None = None, limit: int = 50) -> dict[str, Any]:
    """Close at most ``limit`` stale sessions for one user."""
    current = time.time() if now is None else float(now)
    cutoff = current - max(1.0, float(idle_seconds))
    result: dict[str, Any] = {"user": user, "scanned": 0, "closed": 0,
                              "skipped_active": 0, "errors": [], "closed_sessions": []}
    try:
        # Read the complete registry so very old sessions are not hidden behind
        # the newest-page limit; the close batch itself remains capped below.
        records = list_records(root, user, limit=None)
    except Exception as exc:
        result["errors"].append({"exception_type": type(exc).__name__})
        return result
    candidates = [record for record in records if isinstance(record, dict)
                  and str(record.get("lifecycle") or "") not in {"closed", "deleted"}
                  and (_timestamp(record.get("updated_at")) or float("inf")) <= cutoff]
    for record in candidates[:max(1, min(500, int(limit)))]:
        source = str(record.get("source") or "")
        session_id = str(record.get("session_id") or "")
        if not source or not session_id:
            continue
        result["scanned"] += 1
        if str(record.get("run_state") or "idle").casefold() in {"running", "queued", "pending", "starting", "stopping"}:
            result["skipped_active"] += 1
            continue
        if source == "web" and _web_lease_active(root, user, session_id, current):
            result["skipped_active"] += 1
            continue
        lock = session_lock(root, user, source, session_id)
        if not lock.acquire(blocking=False):
            result["skipped_active"] += 1
            continue
        try:
            latest = find_record(root, user, source, session_id)
            if not isinstance(latest, dict) or str(latest.get("lifecycle") or "") in {"closed", "deleted"}:
                continue
            if (_timestamp(latest.get("updated_at")) or float("inf")) > cutoff:
                continue
            if str(latest.get("run_state") or "idle").casefold() in {"running", "queued", "pending", "starting", "stopping"}:
                result["skipped_active"] += 1
                continue
            queued: dict[str, Any] | None = None
            queue_error: Exception | None = None
            try:
                queued = queue_memory_extraction(
                    root, user, source, session_id, reason="idle_session_sweep"
                )
            except Exception as exc:
                queue_error = exc
            try:
                closed = close_session(root, user, source, session_id)
            except Exception as exc:
                result["errors"].append({
                    "source": source, "session_id": session_id,
                    "exception_type": type(exc).__name__,
                })
                continue
            if queue_error is not None:
                result["errors"].append({
                    "source": source, "session_id": session_id,
                    "exception_type": type(queue_error).__name__,
                })
            if closed is not None:
                result["closed"] += 1
                if len(result["closed_sessions"]) < 20:
                    result["closed_sessions"].append({"source": source, "session_id": session_id,
                                                       "memory_status": str((queued or {}).get("status") or "unknown")})
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
            "skipped_active": sum(int(item.get("skipped_active") or 0) for item in results),
            "errors": [error for item in results for error in item.get("errors", [])],
            "users": results}


__all__ = ["SESSION_IDLE_CLOSE_SECONDS", "SESSION_SWEEP_INTERVAL_SECONDS", "sweep_all_users", "sweep_idle_sessions"]
