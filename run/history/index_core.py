"""Durable SQLite registry for logical conversations and background jobs.

The registry maps logical sessions to archive/runtime window rows, tracks
active entry points, and coordinates memory and summary leases. Complete
transcripts and this registry share the per-user history database.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import os
from pathlib import Path
import threading
import uuid
from typing import Any, Iterator

from run.history.store import (
    claim_registry_record,
    database_path as history_database_path,
    list_windows as list_stored_windows,
    query_session_records,
    read_active_binding,
    read_latest_registry_record,
    read_registry,
    read_registry_metadata,
    read_registry_record,
    upsert_registry_record,
    write_registry,
)
from run.config import user_dir


INDEX_SCHEMA_VERSION = 3
INDEX_FILENAME = "history.sqlite3"
INDEX_LOCK_FILENAME = ".history.index.lock"
MEMORY_CLAIM_STALE_SECONDS = 15 * 60
MEMORY_RETRY_DELAY_SECONDS = 30
SUMMARY_CLAIM_STALE_SECONDS = 15 * 60
SUMMARY_DEFAULT_MAX_ATTEMPTS = 5
SUMMARY_DEFAULT_RETRY_DELAYS = (30, 120, 600, 1800)
_KEY_SEPARATOR = "\x1f"
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def new_conversation_id() -> str:
    """Return an opaque, non-user-derived logical conversation identifier."""

    return f"conv_{uuid.uuid4().hex}"


def chain_for_source(source: str) -> str:
    value = str(source or "")
    if value in {"web", "app", "cli", "interactive", "direct_api"}:
        return "interactive"
    if value.startswith("message:") or value in {"telegram", "onebot"}:
        return "message"
    return "background"


def session_key(source: str, session_id: str) -> str:
    return f"{source}{_KEY_SEPARATOR}{session_id}"


def history_directory(root: Path, user: str) -> Path:
    return user_dir(user, root) / "history"


def index_path(root: Path, user: str) -> Path:
    return history_database_path(root, user)


def _lock_path(root: Path, user: str) -> Path:
    return history_directory(root, user) / INDEX_LOCK_FILENAME


def _thread_lock(root: Path, user: str) -> threading.RLock:
    key = str(index_path(root, user).resolve()).casefold()
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Best-effort inter-process lock for the per-user registry.

    The project runs on Windows as well as POSIX hosts.  The small adapter
    keeps the lock implementation dependency-free; the in-process RLock is
    still required because ``flock``/``msvcrt`` semantics differ for threads.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    locked = False
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        locked = True

        # Initialize the lock byte only after ownership is established.  On
        # Windows, writing or flushing byte zero while another handle has it
        # locked raises PermissionError.  Locking an empty file is supported,
        # so first-open races do not need an unlocked initialization write.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        yield
    finally:
        try:
            if locked and os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif locked:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def index_lock(root: Path, user: str) -> Iterator[None]:
    with _thread_lock(root, user):
        with _file_lock(_lock_path(root, user)):
            yield


def empty_index() -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "revision": 0,
        "sessions": {},
        "active": {},
        "updated_at": _now(),
    }


def _record_key(source: str, session_id: str) -> str:
    return session_key(source, session_id)


def _derived_conversation_id(source: str, session_id: str, directory: Path) -> str:
    digest = hashlib.sha256(
        f"{source}\0{session_id}\0{directory.name}".encode("utf-8")
    ).hexdigest()[:24]
    return f"conv_{digest}"


def _platform_binding(source: str) -> str | None:
    if source.startswith("message:"):
        return source.split(":", 1)[1]
    if source in {"telegram", "onebot"}:
        return source
    return None


def _record_from_data(
    *,
    source: str,
    session_id: str,
    directory: Path,
    data: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    old = previous if isinstance(previous, dict) else {}
    archive_memory_round: int | None = None
    if data.get("memory_processed_round") is not None:
        try:
            archive_memory_round = max(0, int(data.get("memory_processed_round") or 0))
        except (TypeError, ValueError):
            archive_memory_round = 0
    try:
        indexed_memory_round = max(0, int(old.get("memory_processed_round") or 0))
    except (TypeError, ValueError):
        indexed_memory_round = 0
    archive_memory_status = str(data.get("memory_status") or "").strip()
    indexed_memory_status = str(old.get("memory_status") or "").strip()
    memory_claim_active = bool(old.get("memory_claim_id"))
    session_generation = (
        str(data.get("session_generation") or "").strip()
        or str(old.get("session_generation") or "").strip()
        or uuid.uuid4().hex
    )
    memory_status = (
        "processing"
        if memory_claim_active
        else archive_memory_status or indexed_memory_status or "unknown"
    )
    record = {
        **old,
        "conversation_id": str(
            old.get("conversation_id")
            or (
                session_id
                if session_id.startswith("conv_")
                else _derived_conversation_id(source, session_id, directory)
            )
        ),
        "session_id": session_id,
        "source": source,
        "session_generation": session_generation,
        "chain": chain_for_source(source),
        "origins": sorted(
            {
                *(item for item in old.get("origins", []) if isinstance(item, str)),
                source,
            }
        ),
        "title": str(data.get("title") or old.get("title") or ""),
        "summary": str(old.get("summary") or ""),
        "bound_platform": old.get("bound_platform") or _platform_binding(source),
        "lifecycle": str(old.get("lifecycle") or "open"),
        "run_state": str(old.get("run_state") or "idle"),
        "archive_window": directory.name,
        "runtime_window": f"temp/{directory.name}",
        "rounds": max(0, int(data.get("rounds") or 0)),
        "token_usage": copy.deepcopy(data.get("token_usage") or {}),
        "created_at": str(data.get("created_at") or old.get("created_at") or _now()),
        "updated_at": str(data.get("updated_at") or old.get("updated_at") or _now()),
        "last_committed_round": max(
            0,
            int(old.get("last_committed_round") or 0),
            int(data.get("rounds") or 0),
        ),
        "memory_processed_round": (
            archive_memory_round
            if archive_memory_round is not None
            else indexed_memory_round
        ),
        "memory_status": memory_status,
    }
    memory_error = data.get("memory_error") or old.get("memory_error")
    if memory_status == "failed" and isinstance(memory_error, dict):
        record["memory_error"] = copy.deepcopy(memory_error)
    elif memory_status != "failed":
        record.pop("memory_error", None)
    memory_last_error = data.get("memory_last_error") or old.get("memory_last_error")
    if isinstance(memory_last_error, dict):
        record["memory_last_error"] = copy.deepcopy(memory_last_error)
    for field in ("memory_queue_reason", "memory_queued_at"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            record[field] = value
        else:
            record.pop(field, None)
    try:
        memory_target_round = max(0, int(data.get("memory_target_round") or 0))
    except (TypeError, ValueError):
        memory_target_round = 0
    if memory_target_round > record["memory_processed_round"]:
        record["memory_target_round"] = memory_target_round
    else:
        record.pop("memory_target_round", None)
        if not memory_claim_active:
            record.pop("memory_queue_reason", None)
            record.pop("memory_queued_at", None)
    if record["lifecycle"] not in {"open", "closed", "deleted"}:
        record["lifecycle"] = "open"
    return record


def build_window_record(
    *,
    source: str,
    session_id: str,
    directory: Path,
    data: dict[str, Any],
    previous: dict[str, Any] | None = None,
    run_state: str | None = None,
) -> dict[str, Any]:
    """Build the registry row used by a window/registry transaction bundle."""

    record = _record_from_data(
        source=source,
        session_id=session_id,
        directory=directory,
        data=data,
        previous=previous,
    )
    if run_state is not None:
        record["run_state"] = run_state
    return record


def _scan_archive_windows(root: Path, user: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for stored in list_stored_windows(root, user):
        data = stored.get("data")
        if not isinstance(data, dict) or data.get("complete") is not True:
            continue
        child = history_directory(root, user) / str(stored.get("window_name") or "")
        source = str(data.get("source") or "")
        session_id = str(data.get("session_id") or "")
        user_value = str(data.get("user") or user)
        if not source or not session_id or user_value != user:
            continue
        key = _record_key(source, session_id)
        previous = result.get(key)
        if previous is not None:
            previous_updated = str(previous.get("updated_at") or "")
            current_updated = str(data.get("updated_at") or "")
            if current_updated <= previous_updated:
                continue
        record = build_window_record(
            source=source,
            session_id=session_id,
            directory=child,
            data=data,
            previous=previous,
        )
        if previous is None and data.get("memory_processed_round") is None:
            # A registry rebuilt from archive rows must not unexpectedly replay
            # an unbounded number of historical LLM extraction jobs.
            record["memory_processed_round"] = record["rounds"]
            record["memory_status"] = "completed"
        result[key] = record
    return result


def _active_reference(source: str, session_id: str) -> dict[str, str]:
    return {"source": source, "session_id": session_id}


def _active_record(sessions: dict[str, Any], value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    source = str(value.get("source") or "")
    session_id = str(value.get("session_id") or "")
    record = sessions.get(session_key(source, session_id))
    if not isinstance(record, dict) or record.get("lifecycle") == "deleted":
        return None
    return record


def _active_matches(value: Any, source: str, session_id: str) -> bool:
    return bool(
        isinstance(value, dict)
        and value.get("source") == source
        and value.get("session_id") == session_id
    )


def _reconcile_metadata_unlocked(index: dict[str, Any]) -> bool:
    """Repair registry-only state without opening archive data files."""

    changed = False
    sessions = index.setdefault("sessions", {})
    for record in sessions.values():
        if not isinstance(record, dict):
            continue
        try:
            processed_round = max(0, int(record.get("memory_processed_round") or 0))
            committed_round = max(0, int(record.get("last_committed_round") or 0))
        except (TypeError, ValueError):
            continue
        try:
            target_round = max(0, int(record.get("memory_target_round") or 0))
        except (TypeError, ValueError):
            target_round = 0
        claim_limit = (
            min(committed_round, target_round) if target_round else committed_round
        )
        if processed_round < claim_limit or not record.get("memory_claim_id"):
            continue
        for field in (
            "memory_claim_id",
            "memory_claimed_at",
            "memory_claim_round",
            "memory_claim_start_round",
            "memory_claim_end_round",
        ):
            record.pop(field, None)
        if record.get("memory_status") == "processing":
            record["memory_status"] = "completed"
        changed = True
    for active_key, value in list(index.setdefault("active", {}).items()):
        record = _active_record(sessions, value)
        if not isinstance(record, dict) or record.get("lifecycle") in {
            "closed",
            "deleted",
        }:
            index["active"].pop(active_key, None)
            changed = True
            continue
        canonical = _active_reference(
            str(record.get("source") or ""),
            str(record.get("session_id") or ""),
        )
        if value != canonical:
            index["active"][active_key] = canonical
            changed = True
    return changed


def _reconcile_unlocked(
    root: Path,
    user: str,
    index: dict[str, Any],
) -> bool:
    changed = False
    scanned = _scan_archive_windows(root, user)
    sessions = index.setdefault("sessions", {})
    for key, record in scanned.items():
        old = sessions.get(key)
        if old != record:
            # Preserve lifecycle, title, summary, bindings and cursors from an
            # existing registry record while refreshing durable window counters.
            sessions[key] = _record_from_data(
                source=str(record.get("source") or ""),
                session_id=str(record.get("session_id") or ""),
                directory=history_directory(root, user)
                / str(record.get("archive_window") or ""),
                data=record,
                previous=old,
            )
            changed = True
    existing_keys = set(sessions)
    scanned_keys = set(scanned)
    for key in existing_keys - scanned_keys:
        record = sessions.get(key) or {}
        if record.get("run_state") == "running" or not record.get("archive_window"):
            continue
        sessions.pop(key, None)
        changed = True
    changed = _reconcile_metadata_unlocked(index) or changed
    return changed


def load_index(root: Path, user: str, *, reconcile: bool = True) -> dict[str, Any]:
    """Load the per-user SQLite registry atomically."""

    with index_lock(root, user):
        index = _load_index_unlocked(root, user)
        changed = False
        # A missing registry can be rebuilt from SQLite windows without any
        # filesystem scan. Normal reads only repair registry-local metadata.
        if reconcile and not index.get("sessions"):
            changed = _reconcile_unlocked(root, user, index) or changed
        elif reconcile:
            changed = _reconcile_metadata_unlocked(index) or changed
        if changed:
            _write_index_unlocked(root, user, index)
        return copy.deepcopy(index)


def _load_index_unlocked(root: Path, user: str) -> dict[str, Any]:
    sessions, active = read_registry(root, user)
    metadata = read_registry_metadata(root, user)
    index = empty_index()
    index["sessions"] = sessions
    index["active"] = active
    try:
        index["revision"] = max(0, int(metadata.get("registry_revision") or 0))
    except (TypeError, ValueError):
        index["revision"] = 0
    index["updated_at"] = str(metadata.get("registry_updated_at") or _now())
    return index


def _write_index_unlocked(
    root: Path, user: str, index: dict[str, Any]
) -> dict[str, Any]:
    index["schema_version"] = INDEX_SCHEMA_VERSION
    index["revision"] = max(0, int(index.get("revision") or 0)) + 1
    index["updated_at"] = _now()
    write_registry(
        root,
        user,
        index.setdefault("sessions", {}),
        index.setdefault("active", {}),
        revision=int(index["revision"]),
        updated_at=str(index["updated_at"]),
    )
    return copy.deepcopy(index)


def upsert_window(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    directory: Path,
    data: dict[str, Any],
    *,
    run_state: str | None = None,
) -> dict[str, Any]:
    """Insert/update one committed archive window in the registry."""

    with index_lock(root, user):
        previous = read_registry_record(root, user, source, session_id)
        incoming_generation = str(data.get("session_generation") or "").strip()
        current_generation = str(
            (previous or {}).get("session_generation") or ""
        ).strip()
        if (
            incoming_generation
            and current_generation
            and incoming_generation != current_generation
        ):
            # A terminal writer from an older reservation must not move the
            # registry back to a session generation that has already been
            # replaced.
            return copy.deepcopy(previous)
        record = build_window_record(
            source=source,
            session_id=session_id,
            directory=directory,
            data=data,
            previous=previous,
            run_state=run_state,
        )
        return upsert_registry_record(root, user, record, updated_at=_now())


def _reserved_record(
    previous: dict[str, Any] | None,
    *,
    source: str,
    session_id: str,
    title: str,
) -> dict[str, Any]:
    old = previous if isinstance(previous, dict) else {}
    now = _now()
    return {
        **old,
        "conversation_id": session_id,
        "session_id": session_id,
        "source": source,
        "session_generation": (
            str(old.get("session_generation") or "").strip() or uuid.uuid4().hex
        ),
        "chain": chain_for_source(source),
        "origins": sorted(
            {
                source,
                *(item for item in old.get("origins", []) if isinstance(item, str)),
            }
        ),
        "title": title or str(old.get("title") or ""),
        "summary": str(old.get("summary") or ""),
        "bound_platform": old.get("bound_platform") or _platform_binding(source),
        "lifecycle": "open",
        "run_state": "idle",
        "archive_window": str(old.get("archive_window") or ""),
        "runtime_window": str(old.get("runtime_window") or ""),
        "rounds": max(0, int(old.get("rounds") or 0)),
        "token_usage": copy.deepcopy(old.get("token_usage") or {}),
        "created_at": str(old.get("created_at") or now),
        "updated_at": now,
        "last_committed_round": max(0, int(old.get("last_committed_round") or 0)),
        "memory_processed_round": max(0, int(old.get("memory_processed_round") or 0)),
        "memory_status": str(old.get("memory_status") or "pending"),
    }


def reserve_session(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    active_key: str | None = None,
    title: str = "",
) -> dict[str, Any]:
    """Reserve a logical session without creating an empty archive window."""

    with index_lock(root, user):
        previous = read_registry_record(root, user, source, session_id)
        record = _reserved_record(
            previous,
            source=source,
            session_id=session_id,
            title=title,
        )
        active_updates = (
            {active_key: _active_reference(source, session_id)} if active_key else None
        )
        return upsert_registry_record(
            root,
            user,
            record,
            active_updates=active_updates,
            updated_at=_now(),
            allow_deleted_reuse=True,
        )


def get_or_reserve_active(
    root: Path,
    user: str,
    source: str,
    active_key: str,
    *,
    preferred_session_id: str | None = None,
    new_session_id: str | None = None,
    reuse_latest: bool = False,
    title: str = "",
) -> tuple[dict[str, Any], bool]:
    """Atomically resolve an active binding or reserve its next session."""

    with index_lock(root, user):
        binding = read_active_binding(root, user, active_key)
        record = None
        if isinstance(binding, dict):
            record = read_registry_record(
                root,
                user,
                str(binding.get("source") or ""),
                str(binding.get("session_id") or ""),
            )
        if (
            isinstance(record, dict)
            and record.get("source") == source
            and record.get("lifecycle") not in {"closed", "deleted"}
        ):
            return copy.deepcopy(record), False

        preferred = str(preferred_session_id or "").strip()
        if preferred:
            candidate = read_registry_record(root, user, source, preferred)
            if isinstance(candidate, dict) and candidate.get("lifecycle") not in {
                "closed",
                "deleted",
            }:
                written = upsert_registry_record(
                    root,
                    user,
                    candidate,
                    active_updates={active_key: _active_reference(source, preferred)},
                    updated_at=_now(),
                )
                return written, False

        if reuse_latest:
            latest = read_latest_registry_record(root, user, source)
            if isinstance(latest, dict) and latest.get("lifecycle") not in {
                "closed",
                "deleted",
            }:
                latest_session = str(latest.get("session_id") or "")
                written = upsert_registry_record(
                    root,
                    user,
                    latest,
                    active_updates={
                        active_key: _active_reference(source, latest_session)
                    },
                    updated_at=_now(),
                )
                return written, False

        session_id = str(new_session_id or "").strip() or new_conversation_id()
        record = _reserved_record(
            None,
            source=source,
            session_id=session_id,
            title=title,
        )
        written = upsert_registry_record(
            root,
            user,
            record,
            active_updates={active_key: _active_reference(source, session_id)},
            updated_at=_now(),
            allow_deleted_reuse=True,
        )
        return written, True


def find_record(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    record = read_registry_record(root, user, source, session_id)
    return copy.deepcopy(record) if isinstance(record, dict) else None


def update_run_state(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    run_state: str,
    run_id: str | None = None,
    directory: Path | None = None,
    session_generation: str = "",
) -> dict[str, Any] | None:
    with index_lock(root, user):
        record = read_registry_record(root, user, source, session_id)
        if not isinstance(record, dict):
            if directory is None:
                return None
            record = _record_from_data(
                source=source,
                session_id=session_id,
                directory=directory,
                data={
                    "user": user,
                    "source": source,
                    "session_id": session_id,
                    "session_generation": str(session_generation or "").strip(),
                },
            )
        record["run_state"] = run_state
        record["run_state_updated_at"] = _now()
        if run_id:
            record["last_run_id"] = run_id
        return upsert_registry_record(root, user, record, updated_at=_now())


def update_memory_state(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    processed_round: int | None = None,
    status: str,
    error: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    with index_lock(root, user):
        record = read_registry_record(root, user, source, session_id)
        if not isinstance(record, dict):
            return None
        if processed_round is not None:
            record["memory_processed_round"] = max(
                int(record.get("memory_processed_round") or 0), int(processed_round)
            )
        if record.get("memory_claim_id") and status in {"pending", "processing"}:
            record["memory_status"] = "processing"
        else:
            record["memory_status"] = status
        record["memory_state_updated_at"] = _now()
        if error is not None:
            record["memory_error"] = copy.deepcopy(error)
        else:
            record.pop("memory_error", None)
        return upsert_registry_record(root, user, record, updated_at=_now())
