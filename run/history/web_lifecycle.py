"""Cross-process presence and lifecycle in CardStream session metadata."""
from __future__ import annotations

from datetime import datetime, timezone
import time
from typing import Any

from run.cards import CardConflictError, CardStore, json_text, session_key
from run.history.registry_store import mutate_registry_record, session_record
from run.history.memory_claims import maintenance_claim_active
from run.history.session_api import _hide

WEB_LEASE_SECONDS = 90
WEB_LEASE_CHECKPOINT_SECONDS = 30
WEB_EMPTY_GRACE_SECONDS = 90
WEB_STARTUP_INSPECTION_DELAY_SECONDS = 90


def touch_session_lease(root, user, source, session_id, client_id, *, now=None):
    store, key = CardStore(root, user), session_key(source, session_id)
    if not client_id or not store.path.is_file():
        return 0
    current = time.time() if now is None else float(now)
    with store.db(write=True) as db:
        if not db.execute("SELECT 1 FROM card_sessions WHERE session_key=? AND lifecycle='open'",
                          (key,)).fetchone():
            return 0
        old = db.execute("SELECT * FROM card_leases WHERE session_key=? AND client_id=?",
                         (key, client_id)).fetchone()
        # Unordered old clients cannot undo a sequenced release fence.
        if old is None:
            db.execute("INSERT INTO card_leases(session_key,client_id,expires_at) VALUES(?,?,?)",
                       (key, client_id, current + WEB_LEASE_SECONDS))
        elif old["sequence"] == 0 and (
                old["released"] or old["expires_at"] <= current + WEB_LEASE_SECONDS - WEB_LEASE_CHECKPOINT_SECONDS):
            db.execute("UPDATE card_leases SET expires_at=?,released=0 WHERE session_key=? AND client_id=?",
                       (current + WEB_LEASE_SECONDS, key, client_id))
        return db.execute("SELECT count(*) FROM card_leases WHERE session_key=? "
                          "AND expires_at>? AND released=0", (key, current)).fetchone()[0]


def touch_web_session_lease(root, user, session_id, client_id, *, now=None):
    return touch_session_lease(root, user, "web", session_id, client_id, now=now)


def active_session_lease_clients(root, user, source, session_id, *, now=None,
                                 exclude_client_id=""):
    store = CardStore(root, user)
    if not store.path.is_file():
        return []
    current = time.time() if now is None else float(now)
    with store.db() as db:
        return [row[0] for row in db.execute("SELECT client_id FROM card_leases "
            "WHERE session_key=? AND expires_at>? AND released=0 AND client_id!=? "
            "ORDER BY client_id",
            (session_key(source, session_id), current, exclude_client_id))]


def release_durable_session_lease(root, user, source, session_id, client_id, *, now=None):
    store = CardStore(root, user)
    if not store.path.is_file():
        return False
    with store.db(write=True) as db:
        return bool(db.execute("UPDATE card_leases SET released=1 WHERE session_key=? AND client_id=? "
                               "AND sequence=0", (session_key(source, session_id), client_id)).rowcount)


def _candidates(root, user, *, now, idle_seconds, limit, after=None, empty_only=False):
    store = CardStore(root, user)
    if not store.path.is_file():
        return []
    cutoff = datetime.fromtimestamp(now - idle_seconds, timezone.utc).isoformat()
    empty_cutoff = datetime.fromtimestamp(now - WEB_EMPTY_GRACE_SECONDS, timezone.utc).isoformat()
    clauses, args = [
        "source='web'",
        "(lifecycle='open' OR lifecycle='closed' AND json_extract(record_json,'$.offline_inspection_pending')=1)",
        "((last_seq=0 AND updated_at<=?) OR (last_seq>0 AND updated_at<=?))",
        "NOT EXISTS(SELECT 1 FROM card_leases l WHERE l.session_key=s.session_key "
        "AND l.expires_at>? AND l.released=0)",
    ], [empty_cutoff, cutoff, now]
    if empty_only:
        clauses.append("last_seq=0")
    if after:
        clauses.append("(updated_at,session_key)>(?,?)")
        args.extend(after)
    with store.db() as db:
        return [dict(row) for row in db.execute(
            f"SELECT * FROM card_sessions s WHERE {' AND '.join(clauses)} "
            "ORDER BY updated_at,session_key LIMIT ?", (*args, max(1, min(500, limit))))]


def _retire(root, user, candidate, *, now, protected, empty_only=False):
    from run.conversation import session_lock
    store, key = CardStore(root, user), candidate["session_key"]
    if candidate["session_id"] in protected:
        return "preserved"
    lock = session_lock(root, user, "web", candidate["session_id"])
    if not lock.acquire(blocking=False):
        return "busy"
    try:
        with store.db(write=True) as db:
            row = db.execute("SELECT * FROM card_sessions WHERE session_key=?", (key,)).fetchone()
            if row is None or row["lifecycle"] == "deleted" or row["updated_at"] != candidate["updated_at"]:
                return "preserved"
            if db.execute("SELECT 1 FROM card_leases WHERE session_key=? AND expires_at>? AND released=0",
                          (key, now)).fetchone():
                return "busy"
            try:
                CardStore.require_idle(db, key)
            except CardConflictError:
                return "busy"
            record = session_record(db, row)
            if maintenance_claim_active(record, datetime.fromtimestamp(now, timezone.utc)):
                return "busy"
            if not row["last_seq"]:
                _hide(db, key)
                return "deleted"
            if empty_only:
                return "preserved"
            import json
            metadata = {**json.loads(row["record_json"]), "offline_inspection_pending": True}
            db.execute("UPDATE card_sessions SET lifecycle='closed',record_json=? WHERE session_key=?",
                       (json_text(metadata), key))
            db.execute("DELETE FROM card_bindings WHERE session_key=?", (key,))
            return "closed"
    finally:
        lock.release()


def cleanup_empty_web_sessions(root, user, *, now=None, protected_sessions=(), limit=100):
    current = time.time() if now is None else float(now)
    result = {"deleted_sessions": [], "skipped_busy": 0}
    for candidate in _candidates(root, user, now=current, idle_seconds=WEB_EMPTY_GRACE_SECONDS,
                                 limit=limit, empty_only=True):
        status = _retire(root, user, candidate, now=current, protected=protected_sessions, empty_only=True)
        if status == "deleted":
            result["deleted_sessions"].append(candidate["session_id"])
        elif status == "busy":
            result["skipped_busy"] += 1
    return result


def inspect_stale_web_sessions(root, user, *, now=None, protected_sessions=(), stop=None,
                               batch_size=100, idle_seconds=90, queue_memory=None,
                               queue_summary=None, queue_reason="startup_offline_session"):
    from run.history.session_api import queue_memory_extraction
    from run.history.session_ops import queue_summary as default_summary
    memory, summary = queue_memory or queue_memory_extraction, queue_summary or default_summary
    current = time.time() if now is None else float(now)
    result: dict[str, Any] = {
        key: [] for key in ("closed_sessions", "queued_memory", "queued_summary",
                           "deleted_sessions", "preserved_sessions", "errors")
    }
    result.update(inspected=0, skipped_busy=0, error_count=0)
    for key in ("closed", "deleted", "preserved", "queued_memory", "queued_summary"):
        result[f"{key}_count"] = 0
    def sample(key, value, counter):
        result[counter] += 1
        if len(result[key]) < 20:
            result[key].append(value)
    after = None
    while stop is None or not stop.is_set():
        candidates = _candidates(root, user, now=current, idle_seconds=idle_seconds,
                                  limit=batch_size, after=after)
        if not candidates:
            break
        for candidate in candidates:
            if stop is not None and stop.is_set():
                break
            after = candidate["updated_at"], candidate["session_key"]
            sid = candidate["session_id"]
            result["inspected"] += 1
            try:
                status = _retire(root, user, candidate, now=current, protected=protected_sessions)
                if status == "busy":
                    result["skipped_busy"] += 1
                    continue
                if status == "preserved":
                    sample("preserved_sessions", {"session_id": sid, "reason": "protected_or_changed"}, "preserved_count")
                    continue
                sample(f"{status}_sessions", sid, f"{status}_count")
                if status == "closed":
                    queued = memory(root, user, "web", sid, reason=queue_reason)
                    if queued.get("status") == "queued":
                        sample("queued_memory", sid, "queued_memory_count")
                    elif queued.get("reason"):
                        sample("preserved_sessions", {"session_id": sid, "reason": queued["reason"]}, "preserved_count")
                    queued = summary(root, user, "web", sid)
                    if queued and queued.get("summary_status") == "queued":
                        sample("queued_summary", sid, "queued_summary_count")
                    mutate_registry_record(root, user, "web", sid,
                        lambda record: {**record, "offline_inspection_pending": False})
            except Exception as error:
                sample("errors", {"session_id": sid, "exception_type": type(error).__name__}, "error_count")
    return result
