"""Personal history marks, independent of run-owned registry snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.history.store_core import _object, _session_is_deleted, connection


def marked_record(row: Any) -> dict[str, Any]:
    record = _object(row["record_json"], {})
    if not isinstance(record, dict):
        record = {}
    return {
        **record,
        "pinned": bool(row["pinned_at"]),
        "pinned_at": str(row["pinned_at"] or ""),
        "favorite": bool(row["favorite"]),
    }


def update_session_marks(
    root: Path, user: str, source: str, session_id: str, *,
    pinned: bool | None = None, favorite: bool | None = None,
) -> dict[str, Any] | None:
    """Set (never toggle) marks atomically without touching session timestamps."""
    with connection(root, user, write=True) as database:
        row = database.execute(
            "SELECT record_json FROM history_sessions "
            "WHERE source=? AND session_id=? AND lifecycle!='deleted'",
            (source, session_id),
        ).fetchone()
        if row is None or _session_is_deleted(database, source=source, session_id=session_id):
            return None
        old = database.execute(
            "SELECT pinned_at, favorite FROM history_session_marks WHERE source=? AND session_id=?",
            (source, session_id),
        ).fetchone()
        pinned_at = str(old["pinned_at"]) if old else ""
        favorite_value = bool(old["favorite"]) if old else False
        if pinned is not None:
            pinned_at = (pinned_at or datetime.now(timezone.utc).isoformat()) if pinned else ""
        if favorite is not None:
            favorite_value = favorite
        database.execute(
            """INSERT INTO history_session_marks(source, session_id, pinned_at, favorite)
            VALUES(?, ?, ?, ?) ON CONFLICT(source, session_id) DO UPDATE SET
            pinned_at=excluded.pinned_at, favorite=excluded.favorite""",
            (source, session_id, pinned_at, int(favorite_value)),
        )
        return marked_record({
            "record_json": row["record_json"], "pinned_at": pinned_at,
            "favorite": favorite_value,
        })


def favorite_count(root: Path, user: str) -> int:
    """All channels and all dates for this user, not the loaded page."""
    with connection(root, user) as database:
        return int(database.execute(
            """SELECT COUNT(*) FROM history_session_marks AS m
            JOIN history_sessions AS s USING(source, session_id)
            WHERE m.favorite=1 AND s.lifecycle!='deleted'"""
        ).fetchone()[0])

