"""Atomic session metadata; historical content exists only in context_cards."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from run.cards import CardConflictError, CardStore, card_connection, json_text, session_key


def session_record(db, row) -> dict[str, Any]:
    record = json.loads(row["record_json"])
    live = db.execute(
        "SELECT count(*) FROM card_integrations i WHERE session_key=? AND "
        "(response_closed=0 OR EXISTS(SELECT 1 FROM card_tool_executions t WHERE "
        "t.integration_id=i.integration_id AND t.execution_live=1 AND NOT "
        "(t.status='interrupted_unknown' AND "
        "coalesce(json_extract(i.record_json,'$.recovery.tools_detached'),0)=1)))",
        (row["session_key"],),
    ).fetchone()[0]
    integrations = db.execute("SELECT count(*) FROM card_integrations WHERE session_key=?",
                              (row["session_key"],)).fetchone()[0]
    return {**record, "source": row["source"], "session_id": row["session_id"],
            "_metadata_token": hashlib.sha256(row["record_json"].encode()).hexdigest(),
            "session_generation": row["generation"], "lifecycle": row["lifecycle"],
            "last_seq": row["last_seq"], "active_tokens": row["active_tokens"],
            "memory_processed_seq": row["memory_processed_seq"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "integration_count": integrations, "run_state": "running" if live else "idle",
            "pinned": bool(record.get("pinned_at")), "favorite": bool(record.get("favorite"))}


def read_registry_record(root: Path, user: str, source: str, session_id: str) -> dict | None:
    store = CardStore(root, user)
    if not store.path.is_file():
        return None
    with store.db() as db:
        row = db.execute("SELECT * FROM card_sessions WHERE session_key=? AND lifecycle!='deleted'",
                         (session_key(source, session_id),)).fetchone()
        return session_record(db, row) if row else None


def write_record(db, row, record: dict, *, updated_at: str = "") -> None:
    if (record.get("source"), record.get("session_id")) != (row["source"], row["session_id"]):
        raise ValueError("Session metadata cannot change its identity")
    if record.get("session_generation", row["generation"]) != row["generation"]:
        raise CardConflictError("Stale session generation")
    token = record.get("_metadata_token")
    if token is not None and token != hashlib.sha256(row["record_json"].encode()).hexdigest():
        raise CardConflictError("Stale metadata snapshot; reread before updating session metadata")
    lifecycle = record.get("lifecycle", row["lifecycle"])
    if lifecycle != row["lifecycle"]:
        CardStore.require_idle(db, row["session_key"])
        if row["lifecycle"] == "deleted":
            raise CardConflictError("Deleted sessions cannot be revived")
    metadata = {key: value for key, value in record.items() if key not in {
        "source", "session_id", "session_generation", "lifecycle", "last_seq",
        "active_tokens", "memory_processed_seq", "created_at", "updated_at",
        "integration_count", "run_state",
        "_metadata_token",
    }}
    db.execute("UPDATE card_sessions SET record_json=?,lifecycle=?,updated_at=? WHERE session_key=?",
               (json_text(metadata), lifecycle, updated_at or row["updated_at"], row["session_key"]))


def mutate_registry_record(root: Path, user: str, source: str, session_id: str,
                           mutator: Callable, *, updated_at: str = "",
                           clear_active: bool = False) -> dict | None:
    store = CardStore(root, user)
    if not store.path.is_file():
        return None
    with store.db(write=True) as db:
        row = db.execute("SELECT * FROM card_sessions WHERE session_key=? AND lifecycle!='deleted'",
                         (session_key(source, session_id),)).fetchone()
        if row is None:
            return None
        old = session_record(db, row)
        rendered = mutator(copy.deepcopy(old))
        if rendered is not None:
            write_record(db, row, rendered, updated_at=updated_at)
        if clear_active:
            db.execute("DELETE FROM card_bindings WHERE session_key=?", (row["session_key"],))
        return session_record(db, db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                                             (row["session_key"],)).fetchone())


def upsert_registry_record(root: Path, user: str, record: dict, *,
                           active_updates: dict | None = None, updated_at: str = "",
                           allow_deleted_reuse: bool = False) -> dict:
    if allow_deleted_reuse:
        raise CardConflictError("RAW identities cannot be reused after deletion")
    source, identity = record["source"], record["session_id"]
    store = CardStore(root, user)
    with store.db(write=True) as db:
        row = db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                         (session_key(source, identity),)).fetchone()
        if row is None or row["lifecycle"] == "deleted":
            raise CardConflictError("Session was not reserved or is deleted")
        write_record(db, row, record, updated_at=updated_at)
        for key, binding in (active_updates or {}).items():
            if binding is None:
                db.execute("DELETE FROM card_bindings WHERE binding_key=?", (key,))
            else:
                target = session_key(binding["source"], binding["session_id"])
                if db.execute("SELECT 1 FROM card_sessions WHERE session_key=? AND lifecycle='open'",
                              (target,)).fetchone() is None:
                    raise CardConflictError("Active binding must reference an open session")
                db.execute("INSERT INTO card_bindings VALUES(?,?) ON CONFLICT(binding_key) "
                           "DO UPDATE SET session_key=excluded.session_key", (key, target))
        return session_record(db, db.execute("SELECT * FROM card_sessions WHERE session_key=?",
                                             (row["session_key"],)).fetchone())


def read_active_binding(root: Path, user: str, active_key: str) -> dict | None:
    store = CardStore(root, user)
    if not store.path.is_file():
        return None
    with card_connection(root, user) as db:
        row = db.execute("SELECT s.* FROM card_sessions s JOIN card_bindings b USING(session_key) "
                         "WHERE b.binding_key=? AND s.lifecycle='open'", (active_key,)).fetchone()
        return session_record(db, row) if row else None
