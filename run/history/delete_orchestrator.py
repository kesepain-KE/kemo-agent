"""One database transaction for transcript, fences, registry and bindings."""
from __future__ import annotations

from pathlib import Path

from run.history.index_core import index_lock
from run.history.store_core import connection
from run.history.window_deletion import _delete_session_windows


def delete_history_sessions(root: Path, user: str, source: str,
                            session_id: str | None = None) -> tuple[int, int]:
    with index_lock(root, user), connection(root, user, write=True) as db:
        if session_id is None:
            targets = [str(row[0]) for row in db.execute(
                "SELECT session_id FROM history_sessions WHERE source=? "
                "UNION SELECT session_id FROM history_windows WHERE source=? "
                "UNION SELECT session_id FROM history_active_sessions WHERE source=?",
                (source, source, source))]
        else:
            targets = [session_id]
        sessions, windows = 0, 0
        for target in targets:
            existed = db.execute(
                "SELECT 1 FROM history_sessions WHERE source=? AND session_id=?",
                (source, target)).fetchone()
            count = _delete_session_windows(db, source, target)
            windows += count
            sessions += int(existed is not None or count > 0)
            db.execute("DELETE FROM history_active_sessions WHERE source=? AND session_id=?", (source, target))
            db.execute("DELETE FROM history_sessions WHERE source=? AND session_id=?", (source, target))
        db.execute("INSERT INTO history_meta VALUES('registry_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
    # Filesystem/cache cleanup is deliberately outside the transaction.
    from run.history import runtime_cache
    if session_id is None:
        runtime_cache.drop_source(root, user, source)
    else:
        runtime_cache.drop_session(root, user, source, session_id)
    return sessions, windows
