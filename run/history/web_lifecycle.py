"""Cross-process browser presence, startup inspection, and empty cleanup."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any, Callable, Collection

from run.history.store_core import _object, connection, database_path
from run.history.registry_store import _upsert_session_row
from run.history.window_store import _delete_session_windows

WEB_LEASE_SECONDS = 90
WEB_LEASE_CHECKPOINT_SECONDS = 30
WEB_EMPTY_GRACE_SECONDS = 90
WEB_STARTUP_INSPECTION_DELAY_SECONDS = 90
WEB_INSPECTION_RESULT_SAMPLE_LIMIT = 20

_HAS_DATA = """(
    s.rounds>0
    OR EXISTS (SELECT 1 FROM history_messages m WHERE m.source='web' AND m.session_id=s.session_id)
    OR EXISTS (SELECT 1 FROM history_windows w WHERE w.source='web' AND w.session_id=s.session_id
        AND (w.rounds!=0 OR json_array_length(w.text_json, '$.messages')>0
             OR json_array_length(w.think_json, '$.rounds')>0
             OR json_array_length(w.tool_json, '$.rounds')>0
             OR json_array_length(w.items_json, '$.items')>0
             OR EXISTS (SELECT 1 FROM history_rounds r WHERE r.window_kind=w.window_kind AND r.window_name=w.window_name)))
    OR EXISTS (SELECT 1 FROM history_context_summaries c WHERE c.source='web' AND c.session_id=s.session_id)
)"""

_STALE_OFFLINE = """s.source='web' AND s.lifecycle!='deleted'
    AND s.run_state NOT IN ('running', 'queued', 'pending')
    AND s.memory_status!='processing' AND s.summary_status!='processing'
    AND julianday(s.updated_at)<=julianday(?)
    AND NOT EXISTS (SELECT 1 FROM history_web_leases l WHERE l.session_id=s.session_id AND l.expires_at>?)
"""

_STARTUP_NEEDS_REVIEW = f"""{_STALE_OFFLINE} AND (
    s.lifecycle!='closed'
    OR NOT {_HAS_DATA}
    OR (
        s.memory_status NOT IN ('queued', 'processing')
        AND COALESCE(CAST(json_extract(s.record_json, '$.memory_processed_round') AS INTEGER), 0)
            < MAX(s.rounds, COALESCE(CAST(json_extract(s.record_json, '$.last_committed_round') AS INTEGER), 0))
    )
)"""


def touch_web_session_lease(root: Path, user: str, session_id: str, client_id: str,
                            *, now: float | None = None) -> bool:
    """Small durable presence checkpoint; a late heartbeat never recreates history.

    Read-only fast path avoids a write transaction for each 15-second heartbeat.
    All processes still recheck session existence inside any renewal transaction.
    """
    current = time.time() if now is None else now
    with connection(root, user) as db:
        row = db.execute("""SELECT l.expires_at FROM history_sessions s
            LEFT JOIN history_web_leases l ON l.session_id=s.session_id AND l.client_id=?
            WHERE s.source='web' AND s.session_id=?""", (client_id, session_id)).fetchone()
        if row is None:
            return False
        if row['expires_at'] is not None and row['expires_at'] > current + WEB_LEASE_SECONDS - WEB_LEASE_CHECKPOINT_SECONDS:
            return True
    with connection(root, user, write=True) as db:
        if db.execute("SELECT 1 FROM history_sessions WHERE source='web' AND session_id=?", (session_id,)).fetchone() is None:
            return False
        db.execute("""INSERT INTO history_web_leases(session_id, client_id, expires_at) VALUES(?, ?, ?)
            ON CONFLICT(session_id, client_id) DO UPDATE SET expires_at=MAX(expires_at, excluded.expires_at)""",
            (session_id, client_id, current + WEB_LEASE_SECONDS))
    return True


_EMPTY = f"{_STALE_OFFLINE} AND NOT {_HAS_DATA}"


def _bump_registry(database, timestamp: str) -> None:
    database.execute("""INSERT INTO history_meta(key, value) VALUES('registry_revision', '1')
        ON CONFLICT(key) DO UPDATE SET value=CAST(COALESCE(NULLIF(history_meta.value, ''), '0') AS INTEGER) + 1""")
    database.execute("INSERT OR REPLACE INTO history_meta(key, value) VALUES('registry_updated_at', ?)",
                     (timestamp,))


def inspect_stale_web_sessions(
    root: Path,
    user: str,
    *,
    now: float | None = None,
    protected_sessions: Collection[str] = (),
    stop: threading.Event | None = None,
    batch_size: int = 100,
    queue_memory: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """One startup pass: retire offline data sessions and delete empty ones.

    Candidate pages are read in bounded batches. Every session is rechecked
    under SQLite's write lease before it is closed or deleted. Memory work is
    only registered here; the maintenance worker owns model execution.
    """
    result: dict[str, Any] = {
        'inspected': 0, 'closed_sessions': [], 'queued_memory': [],
        'deleted_sessions': [], 'preserved_sessions': [], 'skipped_busy': 0,
        'closed_count': 0, 'queued_memory_count': 0, 'deleted_count': 0,
        'preserved_count': 0, 'error_count': 0, 'errors': [],
    }

    def sample(name: str, value: Any) -> None:
        values = result[name]
        if len(values) < WEB_INSPECTION_RESULT_SAMPLE_LIMIT:
            values.append(value)
    if not database_path(root, user).is_file():
        return result
    if queue_memory is None:
        from run.history.session_api import queue_memory_extraction
        queue_memory = queue_memory_extraction
    from run.conversation import session_lock

    current = time.time() if now is None else now
    timestamp = datetime.fromtimestamp(current, timezone.utc).isoformat()
    cutoff = datetime.fromtimestamp(current - WEB_EMPTY_GRACE_SECONDS, timezone.utc).isoformat()
    protected = set(protected_sessions)
    cursor: tuple[str, str] | None = None
    page_size = max(1, min(500, int(batch_size)))
    while stop is None or not stop.is_set():
        cursor_sql = ''
        params: list[Any] = [cutoff, current]
        if cursor is not None:
            cursor_sql = ' AND (s.updated_at>? OR (s.updated_at=? AND s.session_id>?))'
            params.extend((cursor[0], cursor[0], cursor[1]))
        with connection(root, user) as database:
            rows = database.execute(
                f"SELECT s.updated_at, s.session_id FROM history_sessions s WHERE {_STARTUP_NEEDS_REVIEW}{cursor_sql} "
                "ORDER BY s.updated_at, s.session_id LIMIT ?",
                (*params, page_size),
            ).fetchall()
        if not rows:
            break
        for candidate in rows:
            if stop is not None and stop.is_set():
                break
            session_id = str(candidate['session_id'])
            cursor = (str(candidate['updated_at']), session_id)
            if session_id in protected:
                continue
            lock = session_lock(root, user, 'web', session_id)
            if not lock.acquire(blocking=False):
                result['skipped_busy'] += 1
                continue
            action = ''
            try:
                with connection(root, user, write=True) as database:
                    row = database.execute(
                        f"SELECT s.record_json, s.lifecycle, CASE WHEN {_HAS_DATA} THEN 1 ELSE 0 END AS has_data "
                        f"FROM history_sessions s WHERE s.session_id=? AND {_STARTUP_NEEDS_REVIEW}",
                        (session_id, cutoff, current),
                    ).fetchone()
                    if row is None:
                        continue
                    result['inspected'] += 1
                    if not bool(row['has_data']):
                        _delete_session_windows(database, 'web', session_id)
                        database.execute("DELETE FROM history_active_sessions WHERE source='web' AND session_id=?", (session_id,))
                        database.execute("DELETE FROM history_sessions WHERE source='web' AND session_id=?", (session_id,))
                        database.execute('DELETE FROM history_web_leases WHERE session_id=?', (session_id,))
                        _bump_registry(database, timestamp)
                        action = 'deleted'
                    else:
                        record = _object(row['record_json'], {})
                        if not isinstance(record, dict):
                            raise ValueError('历史会话索引不是有效对象')
                        changed = (
                            str(row['lifecycle']) != 'closed'
                            or str(record.get('lifecycle') or '') != 'closed'
                            or str(record.get('run_state') or '') != 'idle'
                        )
                        if changed:
                            record['lifecycle'] = 'closed'
                            record['run_state'] = 'idle'
                            record['startup_offline_closed_at'] = timestamp
                            _upsert_session_row(database, record)
                        unbound = database.execute(
                            "DELETE FROM history_active_sessions WHERE source='web' AND session_id=?", (session_id,)
                        ).rowcount > 0
                        if changed or unbound:
                            _bump_registry(database, timestamp)
                        action = 'data'
                if action == 'deleted':
                    result['deleted_count'] += 1
                    sample('deleted_sessions', session_id)
                    continue
                if action == 'data':
                    original_record = _object(row['record_json'], {})
                    if (
                        str(row['lifecycle']) != 'closed'
                        or not isinstance(original_record, dict)
                        or str(original_record.get('lifecycle') or '') != 'closed'
                    ):
                        result['closed_count'] += 1
                        sample('closed_sessions', session_id)
                    queued = queue_memory(
                        root, user, 'web', session_id,
                        reason='startup_offline_session',
                    )
                    status = str((queued or {}).get('status') or 'unknown')
                    if status == 'queued':
                        result['queued_memory_count'] += 1
                        sample('queued_memory', session_id)
                    else:
                        result['preserved_count'] += 1
                        sample('preserved_sessions', {
                            'session_id': session_id,
                            'reason': str((queued or {}).get('reason') or status),
                        })
            except Exception as exc:
                result['error_count'] += 1
                sample('errors', {
                    'session_id': session_id,
                    'exception_type': type(exc).__name__,
                })
            finally:
                lock.release()
        if len(rows) < page_size:
            break
    return result


def cleanup_empty_web_sessions(root: Path, user: str, *, now: float | None = None,
                               protected_sessions: Collection[str] = (), limit: int = 100) -> dict[str, Any]:
    from run.conversation import session_lock

    result: dict[str, Any] = {'deleted_sessions': [], 'skipped_busy': 0}
    if not database_path(root, user).is_file():
        return result
    current = time.time() if now is None else now
    cutoff = datetime.fromtimestamp(current - WEB_EMPTY_GRACE_SECONDS, timezone.utc).isoformat()
    args = (cutoff, current)
    with connection(root, user) as db:
        candidates = db.execute(f"SELECT s.session_id FROM history_sessions s WHERE {_EMPTY} ORDER BY s.updated_at, s.session_id LIMIT ?",
                                (*args, max(1, min(500, limit)))).fetchall()
        expired = db.execute('SELECT 1 FROM history_web_leases WHERE expires_at<=? LIMIT 1', (current,)).fetchone()
    for row in candidates:
        sid = row['session_id']
        if sid in protected_sessions:
            continue
        lock = session_lock(root, user, 'web', sid)
        if not lock.acquire(blocking=False):
            result['skipped_busy'] += 1
            continue
        try:
            with connection(root, user, write=True) as db:
                # Serialize with heartbeat, a new round, memory/summary claims,
                # another cleanup process and registry changes before deleting.
                if db.execute(f"SELECT 1 FROM history_sessions s WHERE s.session_id=? AND {_EMPTY}", (sid, *args)).fetchone() is None:
                    continue
                _delete_session_windows(db, 'web', sid)
                db.execute("DELETE FROM history_active_sessions WHERE source='web' AND session_id=?", (sid,))
                db.execute("DELETE FROM history_sessions WHERE source='web' AND session_id=?", (sid,))
                db.execute('DELETE FROM history_web_leases WHERE session_id=?', (sid,))
                _bump_registry(db, datetime.fromtimestamp(current, timezone.utc).isoformat())
            result['deleted_sessions'].append(sid)
        finally:
            lock.release()
    if expired is not None:
        with connection(root, user, write=True) as db:
            db.execute('DELETE FROM history_web_leases WHERE rowid IN (SELECT rowid FROM history_web_leases WHERE expires_at<=? LIMIT 1000)', (current,))
    return result
