"""Session identity and explicit reservation; no window discovery or migration."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
from uuid import uuid4

from run.cards import CardConflictError, CardStore
from run.config import user_dir
from run.history.registry_store import (
    mutate_registry_record, read_active_binding, read_registry_record, upsert_registry_record,
)
from run.history.session_listing import query_session_records

_LOCKS: dict[str, threading.RLock] = {}
_GUARD = threading.Lock()
_LOCAL = threading.local()
MEMORY_CLAIM_STALE_SECONDS = 600
MEMORY_RETRY_DELAY_SECONDS = 60
SUMMARY_CLAIM_STALE_SECONDS = 600


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (TypeError, ValueError):
        return None


def new_conversation_id() -> str:
    return "conv_" + uuid4().hex


def chain_for_source(source: str) -> str:
    return ("cron" if source.startswith(("cron", "background:cron:"))
            else "message" if source.startswith("message:") else "interactive")


@contextmanager
def index_lock(root: Path, user: str):
    """Reentrant process/file lock for multi-step metadata callers only."""
    directory = user_dir(user, root)
    identity = os.path.normcase(str(directory.resolve()))
    with _GUARD:
        lock = _LOCKS.setdefault(identity, threading.RLock())
    with lock:
        held = getattr(_LOCAL, "held", set())
        if identity in held:
            yield
            return
        path = directory / "cards.lock"
        from plugins._creator_security import reject_link_components
        reject_link_components(root.resolve(), path, label="CardStream metadata lock")
        if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
            raise ValueError("Unsafe CardStream lock file")
        with path.open("a+b") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _LOCAL.held = held | {identity}
            try:
                yield
            finally:
                _LOCAL.held = held
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def find_record(root, user, source, session_id):
    return read_registry_record(root, user, source, session_id)


def reserve_session(root: Path, user: str, source: str, session_id: str, *,
                    active_key: str | None = None, **metadata) -> dict:
    with index_lock(root, user):
        store = CardStore(root, user)
        state = store.ensure_session(source, session_id)
        record = find_record(root, user, source, session_id)
        if record is None:
            raise CardConflictError("Session was deleted before reservation completed")
        record.setdefault("conversation_id", session_id)
        record.setdefault("chain", chain_for_source(source))
        record.update(metadata)
        record["session_generation"] = state["generation"]
        return upsert_registry_record(root, user, record,
            active_updates={active_key: {"source": source, "session_id": session_id}} if active_key else None)


def get_or_reserve_active(root, user, source, active_key, *,
                          preferred_session_id=None, new_session_id=None, reuse_latest=False,
                          **metadata):
    with index_lock(root, user):
        record = read_active_binding(root, user, active_key)
        if record is not None and record["source"] == source:
            return record, False
        if preferred_session_id:
            record = find_record(root, user, source, preferred_session_id)
        if record is None and reuse_latest:
            records, _ = query_session_records(root, user, source=source)
            record = next((item for item in records if item["lifecycle"] == "open"), None)
        if record is not None and record["lifecycle"] == "open":
            return upsert_registry_record(root, user, record,
                active_updates={active_key: {"source": source, "session_id": record["session_id"]}}), False
        identity = new_session_id or preferred_session_id or new_conversation_id()
        if CardStore(root, user).session(source, identity) is not None:
            identity = new_conversation_id()
        return reserve_session(root, user, source, identity, active_key=active_key, **metadata), True


def update_run_state(root, user, source, session_id, *, run_state=None, status=None, **metadata):
    return mutate_registry_record(root, user, source, session_id,
                                  lambda record: {**record, **metadata}, updated_at=_now())
