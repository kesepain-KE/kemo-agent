"""Document-scoped, ordered presence transitions across Web worker processes."""
from __future__ import annotations

import math
from pathlib import Path
import time

from run.history.session_leases import lease_client_key, lease_scope_sql
from run.history.store_core import connection, database_path
from run.history.web_lifecycle import WEB_LEASE_SECONDS

MAX_LEASE_SEQUENCE = 9_007_199_254_740_991


def valid_sequence(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_LEASE_SEQUENCE


def update_presence(root: Path, user: str, source: str, session_id: str,
                    client_id: str, sequence: int, *, release: bool = False,
                    now: float | None = None) -> dict[str, bool]:
    """A late release/heartbeat never overwrites a newer transition.

    Released rows are short-lived fences, not live leases. A release may arrive
    before the first heartbeat, so it must fence even a currently missing row.
    Document IDs are never reused by another document; legacy clients keep
    their conservative expiry behavior and cannot remove these fences.
    """
    if not valid_sequence(sequence):
        raise ValueError("lease_sequence 必须为 1..9007199254740991 的整数")
    result = {"exists": False, "accepted": False, "active": False}
    if not database_path(root, user).is_file():
        return result
    current = time.time() if now is None else now
    key = lease_client_key(source, client_id)
    with connection(root, user, write=True) as db:
        if db.execute(
            "SELECT 1 FROM history_sessions WHERE source=? AND session_id=? AND lifecycle='open'",
            (source, session_id),
        ).fetchone() is None:
            return result
        result["exists"] = True
        row = db.execute(
            "SELECT sequence, released, expires_at FROM history_web_leases WHERE session_id=? AND client_id=?",
            (session_id, key),
        ).fetchone()
        if row is not None and row["sequence"] >= sequence:
            result["active"] = not row["released"] and row["expires_at"] > current
            return result
        db.execute(
            """INSERT INTO history_web_leases(session_id,client_id,expires_at,sequence,released)
            VALUES(?,?,?,?,?) ON CONFLICT(session_id,client_id) DO UPDATE SET
            expires_at=excluded.expires_at,sequence=excluded.sequence,released=excluded.released""",
            (session_id, key, current + WEB_LEASE_SECONDS, sequence, int(release)),
        )
        return {**result, "accepted": True, "active": not release}


def other_presence(root: Path, user: str, source: str, session_id: str,
                   client_id: str = "", *, now: float | None = None) -> dict[str, int]:
    """Bounded occupancy metadata only; never disclose other client identities."""
    if not database_path(root, user).is_file():
        return {"active_clients": 0, "retry_after_seconds": 0}
    current = time.time() if now is None else now
    scope, value = lease_scope_sql(source)
    key = lease_client_key(source, client_id) if client_id else ""
    with connection(root, user) as db:
        count, latest = db.execute(
            f"""SELECT COUNT(*), MAX(l.expires_at) FROM history_web_leases l
            JOIN history_sessions s ON s.session_id=l.session_id
            WHERE s.source=? AND s.session_id=? AND s.lifecycle='open'
            AND l.released=0 AND l.expires_at>? AND l.client_id!=? AND {scope}""",
            (source, session_id, current, key, value),
        ).fetchone()
    return {"active_clients": count,
            "retry_after_seconds": min(WEB_LEASE_SECONDS, max(0, math.ceil((latest or current) - current)))}
