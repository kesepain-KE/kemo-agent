"""Ordered document presence; source is part of the durable session key."""
from __future__ import annotations

import math
import time
from run.cards import CardStore, session_key
from run.history.web_lifecycle import WEB_LEASE_SECONDS

MAX_LEASE_SEQUENCE = 9_007_199_254_740_991


def valid_sequence(value):
    return type(value) is int and 1 <= value <= MAX_LEASE_SEQUENCE


def update_presence(root, user, source, session_id, client_id, sequence, *, release=False, now=None):
    if not valid_sequence(sequence):
        raise ValueError("lease_sequence 必须为 1..9007199254740991 的整数")
    result = {"exists": False, "accepted": False, "active": False}
    store = CardStore(root, user)
    if not store.path.is_file():
        return result
    current, key = time.time() if now is None else float(now), session_key(source, session_id)
    with store.db(write=True) as db:
        if not db.execute("SELECT 1 FROM card_sessions WHERE session_key=? AND lifecycle='open'",
                          (key,)).fetchone():
            return result
        result["exists"] = True
        old = db.execute("SELECT * FROM card_leases WHERE session_key=? AND client_id=?",
                         (key, client_id)).fetchone()
        if old is not None and old["sequence"] >= sequence:
            return {**result, "active": not old["released"] and old["expires_at"] > current}
        db.execute("INSERT INTO card_leases VALUES(?,?,?,?,?) ON CONFLICT(session_key,client_id) DO UPDATE "
                   "SET expires_at=excluded.expires_at,sequence=excluded.sequence,released=excluded.released",
                   (key, client_id, current + WEB_LEASE_SECONDS, sequence, int(release)))
        return {**result, "accepted": True, "active": not release}


def other_presence(root, user, source, session_id, client_id="", *, now=None):
    store = CardStore(root, user)
    if not store.path.is_file():
        return {"active_clients": 0, "retry_after_seconds": 0}
    current = time.time() if now is None else float(now)
    with store.db() as db:
        count, latest = db.execute("SELECT count(*),max(l.expires_at) FROM card_leases l "
            "JOIN card_sessions s USING(session_key) WHERE s.session_key=? AND s.lifecycle='open' "
            "AND l.released=0 AND l.expires_at>? AND l.client_id!=?",
            (session_key(source, session_id), current, client_id)).fetchone()
    return {"active_clients": count, "retry_after_seconds":
            min(WEB_LEASE_SECONDS, max(0, math.ceil((latest or current) - current)))}
