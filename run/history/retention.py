"""Bounded, transactional retention of finished Cron conversation history only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from run.history.store_core import connection, database_path
from run.history.window_store import _delete_session_windows


def cleanup_cron_history(
    root: Path, user: str, *, retention_days: int = 7,
    now: datetime | None = None, limit: int = 100,
) -> dict[str, Any]:
    """Keep running/claimed sessions; recheck expiry under SQLite's write lease.

    New records use execution completion time, not metadata edits. Legacy rows
    without that timestamp conservatively use their last registry update.
    """
    from run.config import cron_history_retention_days
    from run.conversation import session_lock

    days = cron_history_retention_days({"cron": {"history_retention_days": retention_days}})
    result = {"retention_days": days, "deleted_sessions": 0, "deleted_windows": 0, "skipped_busy": 0}
    if days == 0 or not database_path(root, user).is_file():
        return result
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cutoff = (current.astimezone(timezone.utc) - timedelta(days=days)).isoformat()
    # Glob is case-sensitive and requires a nonempty task id; other background
    # sources, interactive sessions and message sessions cannot match.
    eligible = """source GLOB 'background:cron:?*'
        AND run_state != 'running' AND memory_status != 'processing'
        AND summary_status != 'processing'
        AND julianday(COALESCE(json_extract(record_json, '$.cron_finished_at'), updated_at)) <= julianday(?)"""
    with connection(root, user) as database:
        candidates = database.execute(
            f"SELECT source, session_id FROM history_sessions WHERE {eligible} ORDER BY updated_at, source, session_id LIMIT ?",
            (cutoff, max(1, min(500, int(limit)))),
        ).fetchall()
    for candidate in candidates:
        source, session_id = candidate['source'], candidate['session_id']
        lock = session_lock(root, user, source, session_id)
        if not lock.acquire(blocking=False):
            result['skipped_busy'] += 1
            continue
        try:
            with connection(root, user, write=True) as database:
                # Protect against another process starting a run, renewing the
                # conversation, or claiming its memory/summary after selection.
                exists = database.execute(
                    f"SELECT 1 FROM history_sessions WHERE source=? AND session_id=? AND {eligible}",
                    (source, session_id, cutoff),
                ).fetchone()
                if exists is None:
                    result['skipped_busy'] += 1
                    continue
                windows = _delete_session_windows(database, source, session_id)
                database.execute('DELETE FROM history_active_sessions WHERE source=? AND session_id=?', (source, session_id))
                database.execute('DELETE FROM history_sessions WHERE source=? AND session_id=?', (source, session_id))
                database.execute("""INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
                    ON CONFLICT(key) DO UPDATE SET value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1""")
                database.execute("INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)", (current.isoformat(),))
            result['deleted_sessions'] += 1
            result['deleted_windows'] += windows
        finally:
            lock.release()
    return result
