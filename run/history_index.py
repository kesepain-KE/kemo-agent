"""Durable logical conversation registry.

The history directory remains the source of truth for the complete transcript;
this module stores the small, rebuildable registry that maps logical sessions
to archive/runtime windows and to the active entry point of each chain.

The registry deliberately accepts legacy timestamp-named windows.  New
windows may use opaque ``conv_`` identifiers, while the public ``session_id``
continues to be accepted during the migration period.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid
from typing import Any, Iterator

from run.users import user_dir


INDEX_SCHEMA_VERSION = 2
INDEX_FILENAME = "data.json"
INDEX_LOCK_FILENAME = ".data.index.lock"
MEMORY_CLAIM_STALE_SECONDS = 15 * 60
MEMORY_RETRY_DELAY_SECONDS = 30
SUMMARY_CLAIM_STALE_SECONDS = 15 * 60
SUMMARY_DEFAULT_MAX_ATTEMPTS = 5
SUMMARY_DEFAULT_RETRY_DELAYS = (30, 120, 600, 1800)
_KEY_SEPARATOR = "\x1f"
_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_ATOMIC_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2)
_TRANSIENT_REPLACE_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})
_TRANSIENT_WINDOWS_REPLACE_ERRORS = frozenset({5, 32, 33})


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
    if value in {"web", "cli", "interactive", "direct_api"}:
        return "interactive"
    if value.startswith("message:") or value in {"telegram", "onebot"}:
        return "message"
    return "background"


def session_key(source: str, session_id: str) -> str:
    return f"{source}{_KEY_SEPARATOR}{session_id}"


def history_directory(root: Path, user: str) -> Path:
    return user_dir(user, root) / "history"


def index_path(root: Path, user: str) -> Path:
    return history_directory(root, user) / INDEX_FILENAME


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
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


@contextmanager
def index_lock(root: Path, user: str) -> Iterator[None]:
    with _thread_lock(root, user):
        with _file_lock(_lock_path(root, user)):
            yield


def _replace_with_retry(source: Path, target: Path) -> None:
    """Atomically replace a file, tolerating brief cross-platform file locks."""

    for attempt in range(len(_ATOMIC_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            transient = (
                exc.errno in _TRANSIENT_REPLACE_ERRNOS
                or getattr(exc, "winerror", None)
                in _TRANSIENT_WINDOWS_REPLACE_ERRORS
            )
            if not transient or attempt >= len(_ATOMIC_REPLACE_RETRY_DELAYS):
                raise
            time.sleep(_ATOMIC_REPLACE_RETRY_DELAYS[attempt])


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def empty_index() -> dict[str, Any]:
    return {
        "schema_version": INDEX_SCHEMA_VERSION,
        "revision": 0,
        "sessions": {},
        "active": {},
        "updated_at": _now(),
    }


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _record_key(source: str, session_id: str) -> str:
    return session_key(source, session_id)


def _legacy_index_id(source: str, session_id: str, directory: Path) -> str:
    digest = hashlib.sha256(
        f"{source}\0{session_id}\0{directory.name}".encode("utf-8")
    ).hexdigest()[:24]
    return f"legacy_{digest}"


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
            archive_memory_round = max(
                0, int(data.get("memory_processed_round") or 0)
            )
        except (TypeError, ValueError):
            archive_memory_round = 0
    try:
        indexed_memory_round = max(0, int(old.get("memory_processed_round") or 0))
    except (TypeError, ValueError):
        indexed_memory_round = 0
    archive_memory_status = str(data.get("memory_status") or "").strip()
    indexed_memory_status = str(old.get("memory_status") or "").strip()
    memory_claim_active = bool(old.get("memory_claim_id"))
    memory_status = (
        "processing"
        if memory_claim_active
        else archive_memory_status or indexed_memory_status or "unknown"
    )
    record = {
        **old,
        "conversation_id": str(
            old.get("conversation_id")
            or (session_id if session_id.startswith("conv_") else _legacy_index_id(source, session_id, directory))
        ),
        "session_id": session_id,
        "source": source,
        "chain": chain_for_source(source),
        "origins": sorted(
            {
                *(
                    item
                    for item in old.get("origins", [])
                    if isinstance(item, str)
                ),
                source,
            }
        ),
        "title": str(data.get("title") or old.get("title") or ""),
        "summary": str(old.get("summary") or ""),
        "bound_platform": old.get("bound_platform") or _platform_binding(source),
        "lifecycle": str(old.get("lifecycle") or old.get("state") or "open"),
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


def _scan_archive_windows(root: Path, user: str) -> dict[str, dict[str, Any]]:
    directory = history_directory(root, user)
    result: dict[str, dict[str, Any]] = {}
    if not directory.is_dir():
        return result
    for child in directory.iterdir():
        if not child.is_dir() or child.name == "temp" or child.is_symlink():
            continue
        data = _read_json(child / "data.json")
        if not isinstance(data, dict) or data.get("complete") is not True:
            continue
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
        record = _record_from_data(
            source=source,
            session_id=session_id,
            directory=child,
            data=data,
            previous=previous,
        )
        if previous is None and data.get("memory_processed_round") is None:
            # Legacy archives predate durable extraction cursors.  Treat their
            # existing rounds as migrated instead of unexpectedly replaying an
            # unbounded number of historical LLM extraction jobs.
            record["memory_processed_round"] = record["rounds"]
            record["memory_status"] = "completed"
        result[key] = record
    return result


def _normalize_index(value: dict[str, Any] | None) -> dict[str, Any]:
    base = empty_index()
    if not isinstance(value, dict):
        return base
    sessions = value.get("sessions")
    if isinstance(sessions, dict):
        base["sessions"] = {
            str(key): copy.deepcopy(record)
            for key, record in sessions.items()
            if isinstance(record, dict)
        }
    active = value.get("active")
    if isinstance(active, dict):
        base["active"] = copy.deepcopy(active)
    try:
        base["revision"] = max(0, int(value.get("revision") or 0))
    except (TypeError, ValueError):
        base["revision"] = 0
    base["updated_at"] = str(value.get("updated_at") or _now())
    return base


def _active_reference(source: str, session_id: str) -> dict[str, str]:
    return {"source": source, "session_id": session_id}


def _active_record(
    sessions: dict[str, Any], value: Any
) -> dict[str, Any] | None:
    if isinstance(value, dict):
        source = str(value.get("source") or "")
        session_id = str(value.get("session_id") or "")
        record = sessions.get(session_key(source, session_id))
        return record if isinstance(record, dict) else None
    if not isinstance(value, str):
        return None
    matches = [
        record
        for record in sessions.values()
        if isinstance(record, dict) and record.get("session_id") == value
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return matches[0]


def _active_matches(value: Any, source: str, session_id: str) -> bool:
    if isinstance(value, dict):
        return (
            value.get("source") == source and value.get("session_id") == session_id
        )
    return value == session_id


def _reconcile_unlocked(root: Path, user: str, index: dict[str, Any]) -> bool:
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
                directory=history_directory(root, user) / str(record.get("archive_window") or ""),
                data=record,
                previous=old,
            )
            changed = True
    existing_keys = set(sessions)
    scanned_keys = set(scanned)
    for key in existing_keys - scanned_keys:
        record = sessions.get(key) or {}
        if (
            record.get("lifecycle") == "closed"
            or record.get("run_state") == "running"
            or not record.get("archive_window")
        ):
            continue
        sessions.pop(key, None)
        changed = True
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
        claim_limit = min(committed_round, target_round) if target_round else committed_round
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
        if not isinstance(record, dict) or record.get("lifecycle") == "closed":
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


def load_index(root: Path, user: str, *, reconcile: bool = True) -> dict[str, Any]:
    """Load and, when needed, rebuild the per-user index atomically."""

    path = index_path(root, user)
    with index_lock(root, user):
        raw = _read_json(path) if path.is_file() else None
        index = _normalize_index(raw)
        changed = raw is None or raw.get("schema_version") != INDEX_SCHEMA_VERSION if isinstance(raw, dict) else True
        if reconcile:
            changed = _reconcile_unlocked(root, user, index) or changed
        if changed:
            index["schema_version"] = INDEX_SCHEMA_VERSION
            index["revision"] = max(0, int(index.get("revision") or 0)) + 1
            index["updated_at"] = _now()
            _atomic_write(path, index)
        return copy.deepcopy(index)


def _write_index_unlocked(root: Path, user: str, index: dict[str, Any]) -> dict[str, Any]:
    index["schema_version"] = INDEX_SCHEMA_VERSION
    index["revision"] = max(0, int(index.get("revision") or 0)) + 1
    index["updated_at"] = _now()
    _atomic_write(index_path(root, user), index)
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
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = _record_key(source, session_id)
        previous = index.setdefault("sessions", {}).get(key)
        record = _record_from_data(
            source=source,
            session_id=session_id,
            directory=directory,
            data=data,
            previous=previous,
        )
        if run_state is not None:
            record["run_state"] = run_state
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


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
        "last_committed_round": max(
            0, int(old.get("last_committed_round") or 0)
        ),
        "memory_processed_round": max(
            0, int(old.get("memory_processed_round") or 0)
        ),
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
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        previous = index.setdefault("sessions", {}).get(key)
        record = _reserved_record(
            previous,
            source=source,
            session_id=session_id,
            title=title,
        )
        index["sessions"][key] = record
        if active_key:
            index.setdefault("active", {})[active_key] = _active_reference(
                source, session_id
            )
        return _write_index_unlocked(root, user, index)["sessions"][key]


def get_or_reserve_active(
    root: Path,
    user: str,
    source: str,
    active_key: str,
    *,
    preferred_session_id: str | None = None,
    reuse_latest: bool = False,
    title: str = "",
) -> tuple[dict[str, Any], bool]:
    """Atomically resolve an active binding or reserve its next session."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        reconciled = _reconcile_unlocked(root, user, index)
        sessions = index.setdefault("sessions", {})
        record = _active_record(sessions, index.setdefault("active", {}).get(active_key))
        if (
            isinstance(record, dict)
            and record.get("source") == source
            and record.get("lifecycle") != "closed"
        ):
            if reconciled:
                _write_index_unlocked(root, user, index)
            return copy.deepcopy(record), False

        preferred = str(preferred_session_id or "").strip()
        if preferred:
            candidate = sessions.get(session_key(source, preferred))
            if isinstance(candidate, dict) and candidate.get("lifecycle") != "closed":
                index["active"][active_key] = _active_reference(source, preferred)
                written = _write_index_unlocked(root, user, index)
                return copy.deepcopy(written["sessions"][session_key(source, preferred)]), False

        if reuse_latest:
            candidates = [
                item
                for item in sessions.values()
                if isinstance(item, dict) and item.get("source") == source
            ]
            candidates.sort(
                key=lambda item: str(item.get("updated_at") or ""), reverse=True
            )
            if candidates and candidates[0].get("lifecycle") != "closed":
                latest = candidates[0]
                latest_session = str(latest.get("session_id") or "")
                index["active"][active_key] = _active_reference(
                    source, latest_session
                )
                written = _write_index_unlocked(root, user, index)
                return copy.deepcopy(
                    written["sessions"][session_key(source, latest_session)]
                ), False

        session_id = new_conversation_id()
        key = session_key(source, session_id)
        sessions[key] = _reserved_record(
            None,
            source=source,
            session_id=session_id,
            title=title,
        )
        index["active"][active_key] = _active_reference(source, session_id)
        written = _write_index_unlocked(root, user, index)
        return copy.deepcopy(written["sessions"][key]), True


def find_record(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    index = load_index(root, user)
    record = (index.get("sessions") or {}).get(session_key(source, session_id))
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
) -> dict[str, Any] | None:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = _record_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            if directory is None:
                return None
            record = _record_from_data(
                source=source,
                session_id=session_id,
                directory=directory,
                data={"user": user, "source": source, "session_id": session_id},
            )
        record["run_state"] = run_state
        record["run_state_updated_at"] = _now()
        if run_id:
            record["last_run_id"] = run_id
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


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
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        record = index.setdefault("sessions", {}).get(_record_key(source, session_id))
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
        index["sessions"][session_key(source, session_id)] = record
        return _write_index_unlocked(root, user, index)["sessions"][session_key(source, session_id)]


def _claim_is_stale(
    record: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    timestamp = _timestamp(
        record.get("memory_claimed_at")
        or record.get("memory_state_updated_at")
        or record.get("run_state_updated_at")
        or record.get("updated_at")
    )
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() >= max(1.0, stale_after_seconds)


def _run_is_stale(
    record: dict[str, Any],
    *,
    now: datetime,
    stale_after_seconds: float,
) -> bool:
    timestamp = _timestamp(
        record.get("run_state_updated_at") or record.get("updated_at")
    )
    if timestamp is None:
        return True
    return (now - timestamp).total_seconds() >= max(1.0, stale_after_seconds)


def claim_pending_memory(
    root: Path,
    user: str,
    *,
    worker_id: str | None = None,
    stale_after_seconds: float = MEMORY_CLAIM_STALE_SECONDS,
    statuses: set[str] | frozenset[str] | None = None,
    max_rounds: int = 1,
) -> dict[str, Any] | None:
    """Atomically lease the next contiguous range of committed rounds."""

    claimable_statuses = set(statuses or {"pending", "failed", "processing"})
    if isinstance(max_rounds, bool):
        raise ValueError("max_rounds 必须是正整数")
    try:
        batch_size = max(1, min(20, int(max_rounds)))
    except (TypeError, ValueError) as exc:
        raise ValueError("max_rounds 必须是正整数") from exc
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        reconciled = _reconcile_unlocked(root, user, index)
        now = datetime.now(timezone.utc)
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        for key, record in index.setdefault("sessions", {}).items():
            if not isinstance(record, dict) or record.get("lifecycle") == "deleted":
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
            claim_limit = min(committed_round, target_round) if target_round else committed_round
            if processed_round >= claim_limit or not record.get("archive_window"):
                continue
            status = str(record.get("memory_status") or "pending")
            stale = _claim_is_stale(
                record,
                now=now,
                stale_after_seconds=stale_after_seconds,
            )
            if status == "processing" and not stale:
                continue
            if status == "failed" and not _claim_is_stale(
                record,
                now=now,
                stale_after_seconds=min(
                    stale_after_seconds,
                    MEMORY_RETRY_DELAY_SECONDS,
                ),
            ):
                continue
            if status not in claimable_statuses:
                continue
            if record.get("run_state") == "running" and not _run_is_stale(
                record,
                now=now,
                stale_after_seconds=stale_after_seconds,
            ):
                continue
            candidates.append((str(record.get("updated_at") or ""), str(key), record))
        if not candidates:
            if reconciled:
                _write_index_unlocked(root, user, index)
            return None
        _, key, record = min(candidates, key=lambda item: (item[0], item[1]))
        claim_id = worker_id or f"memory_{uuid.uuid4().hex}"
        next_round = max(0, int(record.get("memory_processed_round") or 0)) + 1
        committed_round = max(0, int(record.get("last_committed_round") or 0))
        target_round = max(0, int(record.get("memory_target_round") or 0))
        claim_limit = min(committed_round, target_round) if target_round else committed_round
        end_round = min(claim_limit, next_round + batch_size - 1)
        record["memory_status"] = "processing"
        record["memory_claim_id"] = claim_id
        record["memory_claimed_at"] = now.isoformat()
        # Keep the legacy field pinned to the first round so an older worker
        # cannot accidentally skip the beginning of a range claim.
        record["memory_claim_round"] = next_round
        record["memory_claim_start_round"] = next_round
        record["memory_claim_end_round"] = end_round
        record["memory_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        written = _write_index_unlocked(root, user, index)
        result = copy.deepcopy(written["sessions"][key])
        result["memory_claim_id"] = claim_id
        result["memory_claim_round"] = next_round
        result["memory_claim_start_round"] = next_round
        result["memory_claim_end_round"] = end_round
        return result


def finish_memory_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    processed_round: int | None = None,
    error: dict[str, Any] | None = None,
    remaining_status: str = "pending",
) -> dict[str, Any] | None:
    """Finish a memory lease; stale workers cannot overwrite a newer claim."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("memory_claim_id") != claim_id:
            return None
        if processed_round is not None:
            record["memory_processed_round"] = max(
                int(record.get("memory_processed_round") or 0),
                int(processed_round),
            )
        claimed_round = max(0, int(record.get("memory_claim_round") or 0))
        claimed_start = max(
            0,
            int(record.get("memory_claim_start_round") or claimed_round),
        )
        claimed_end = max(
            claimed_start,
            int(record.get("memory_claim_end_round") or claimed_round),
        )
        for field in (
            "memory_claim_id",
            "memory_claimed_at",
            "memory_claim_round",
            "memory_claim_start_round",
            "memory_claim_end_round",
        ):
            record.pop(field, None)
        if error is not None:
            previous_error = record.get("memory_last_error")
            retry_count = (
                max(0, int(previous_error.get("retry_count") or 0)) + 1
                if isinstance(previous_error, dict)
                else 1
            )
            diagnostic = {
                **copy.deepcopy(error),
                "round": claimed_start,
                "round_start": claimed_start,
                "round_end": claimed_end,
                "occurred_at": _now(),
                "retry_count": retry_count,
            }
            record["memory_status"] = "failed"
            record["memory_error"] = diagnostic
            record["memory_last_error"] = copy.deepcopy(diagnostic)
        else:
            record.pop("memory_error", None)
            current_round = max(0, int(record.get("memory_processed_round") or 0))
            committed_round = max(0, int(record.get("last_committed_round") or 0))
            target_round = max(0, int(record.get("memory_target_round") or 0))
            if target_round and current_round >= target_round:
                for field in (
                    "memory_queue_reason",
                    "memory_target_round",
                    "memory_queued_at",
                ):
                    record.pop(field, None)
                record["memory_status"] = (
                    "completed" if current_round >= committed_round else "deferred"
                )
            else:
                record["memory_status"] = (
                    "completed"
                    if current_round >= committed_round
                    else remaining_status
                )
        record["memory_state_updated_at"] = _now()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def set_active(
    root: Path,
    user: str,
    active_key: str,
    session_id: str,
    *,
    source: str | None = None,
) -> dict[str, Any]:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        normalized_source = str(source or "")
        if not normalized_source:
            matches = [
                record
                for record in index.setdefault("sessions", {}).values()
                if isinstance(record, dict) and record.get("session_id") == session_id
            ]
            if len(matches) != 1:
                raise ValueError("设置活跃会话时必须提供可唯一定位的 source")
            normalized_source = str(matches[0].get("source") or "")
        index.setdefault("active", {})[active_key] = _active_reference(
            normalized_source, session_id
        )
        return _write_index_unlocked(root, user, index)


def get_active(root: Path, user: str, active_key: str) -> dict[str, Any] | None:
    index = load_index(root, user)
    record = _active_record(
        index.get("sessions") or {},
        (index.get("active") or {}).get(active_key),
    )
    return copy.deepcopy(record) if isinstance(record, dict) else None


def close_session(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        record["lifecycle"] = "closed"
        record["run_state"] = "idle"
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if _active_matches(active_session, source, session_id):
                index["active"].pop(active_key, None)
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def update_title(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    title: str,
) -> dict[str, Any] | None:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        record["title"] = title
        record["title_source"] = "manual"
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def queue_summary(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any]:
    """Queue card metadata generation after a session is durably closed."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return {"status": "skipped", "reason": "session_not_found", "rounds": 0}
        target_round = max(0, int(record.get("last_committed_round") or record.get("rounds") or 0))
        if record.get("lifecycle") != "closed":
            return {"status": "skipped", "reason": "session_not_closed", "rounds": target_round}
        if target_round < 1 or not record.get("archive_window"):
            record["summary_status"] = "none"
            record["summary_target_round"] = target_round
            index["sessions"][key] = record
            _write_index_unlocked(root, user, index)
            return {"status": "skipped", "reason": "no_archive_rounds", "rounds": target_round}
        completed_round = max(0, int(record.get("summary_completed_round") or 0))
        if completed_round >= target_round and str(record.get("summary") or "").strip():
            return {"status": "completed", "reason": "already_current", "rounds": target_round}
        record["summary_status"] = "queued"
        record["summary_target_round"] = target_round
        record["summary_state_updated_at"] = _now()
        record["summary_retry_count"] = 0
        record["summary_consecutive_failures"] = 0
        for field in (
            "summary_claim_id",
            "summary_claimed_at",
            "summary_retry_at",
            "summary_error",
            "summary_checkpoint",
            "summary_checkpoint_next_chunk",
            "summary_checkpoint_total_chunks",
        ):
            record.pop(field, None)
        index["sessions"][key] = record
        _write_index_unlocked(root, user, index)
        return {"status": "queued", "reason": "session_closed", "rounds": target_round}


def claim_pending_summary(
    root: Path,
    user: str,
    *,
    worker_id: str | None = None,
    stale_after_seconds: float = SUMMARY_CLAIM_STALE_SECONDS,
) -> dict[str, Any] | None:
    """Atomically lease one closed-session summary job."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        reconciled = _reconcile_unlocked(root, user, index)
        now = datetime.now(timezone.utc)
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        for key, record in index.setdefault("sessions", {}).items():
            if not isinstance(record, dict) or record.get("lifecycle") != "closed":
                continue
            target_round = max(0, int(record.get("summary_target_round") or 0))
            completed_round = max(0, int(record.get("summary_completed_round") or 0))
            if target_round < 1 or completed_round >= target_round or not record.get("archive_window"):
                continue
            status = str(record.get("summary_status") or "none")
            if status == "processing":
                claimed_at = _timestamp(record.get("summary_claimed_at"))
                if claimed_at is not None and (now - claimed_at).total_seconds() < max(1.0, stale_after_seconds):
                    continue
            elif status in {"failed", "retry_wait"}:
                retry_at = _timestamp(record.get("summary_retry_at"))
                if retry_at is not None and now < retry_at:
                    continue
            elif status != "queued":
                continue
            candidates.append((str(record.get("updated_at") or ""), str(key), record))
        if not candidates:
            if reconciled:
                _write_index_unlocked(root, user, index)
            return None
        _, key, record = min(candidates, key=lambda item: (item[0], item[1]))
        claim_id = worker_id or f"summary_{uuid.uuid4().hex}"
        record["summary_status"] = "processing"
        record["summary_claim_id"] = claim_id
        record["summary_claimed_at"] = now.isoformat()
        record["summary_last_attempt_at"] = now.isoformat()
        record["summary_attempt_count"] = max(
            0, int(record.get("summary_attempt_count") or 0)
        ) + 1
        record["summary_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        written = _write_index_unlocked(root, user, index)
        result = copy.deepcopy(written["sessions"][key])
        result["summary_claim_id"] = claim_id
        return result


def finish_summary_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    title: str | None = None,
    summary: str | None = None,
    completed_round: int | None = None,
    error: dict[str, Any] | None = None,
    max_attempts: int = SUMMARY_DEFAULT_MAX_ATTEMPTS,
    retry_delays: tuple[int, ...] = SUMMARY_DEFAULT_RETRY_DELAYS,
) -> dict[str, Any] | None:
    """Finish a summary lease without allowing stale workers to overwrite data."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        for field in ("summary_claim_id", "summary_claimed_at"):
            record.pop(field, None)
        now = datetime.now(timezone.utc)
        if error is not None:
            retry_count = max(0, int(record.get("summary_retry_count") or 0)) + 1
            record["summary_max_attempts"] = max(1, int(max_attempts))
            record["summary_retry_count"] = retry_count
            record["summary_consecutive_failures"] = retry_count
            record["summary_error"] = copy.deepcopy(error)
            record["summary_last_error"] = copy.deepcopy(error)
            if retry_count >= max(1, int(max_attempts)):
                record["summary_status"] = "exhausted"
                record.pop("summary_retry_at", None)
            else:
                delays = tuple(max(1, int(value)) for value in retry_delays) or (30,)
                delay = delays[min(retry_count - 1, len(delays) - 1)]
                record["summary_status"] = "retry_wait"
                record["summary_retry_at"] = (
                    now + timedelta(seconds=delay)
                ).isoformat()
        else:
            normalized_title = str(title or "").strip()
            normalized_summary = str(summary or "").strip()
            if not normalized_title or not normalized_summary or completed_round is None:
                return None
            if not str(record.get("title") or "").strip() or record.get("title_source") == "auto":
                record["title"] = normalized_title
                record["title_source"] = "auto"
            record["summary"] = normalized_summary
            record["summary_status"] = "completed"
            record["summary_max_attempts"] = max(1, int(max_attempts))
            record["summary_completed_round"] = max(0, int(completed_round))
            record["summary_updated_at"] = now.isoformat()
            if max(0, int(record.get("summary_attempt_count") or 0)) > 1:
                record["summary_recovered_at"] = now.isoformat()
            record["summary_retry_count"] = 0
            record["summary_consecutive_failures"] = 0
            record.pop("summary_retry_at", None)
            record.pop("summary_error", None)
            record.pop("summary_checkpoint", None)
            record.pop("summary_checkpoint_next_chunk", None)
            record.pop("summary_checkpoint_total_chunks", None)
        record["summary_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def defer_summary_claim(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    error: dict[str, Any],
    delay_seconds: int = 30,
) -> dict[str, Any] | None:
    """Release a lease without consuming an automatic retry attempt."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        record.pop("summary_claim_id", None)
        record.pop("summary_claimed_at", None)
        record["summary_attempt_count"] = max(
            0, int(record.get("summary_attempt_count") or 0) - 1
        )
        now = datetime.now(timezone.utc)
        record["summary_status"] = "retry_wait"
        record["summary_retry_at"] = (
            now + timedelta(seconds=max(1, int(delay_seconds)))
        ).isoformat()
        record["summary_error"] = copy.deepcopy(error)
        record["summary_last_error"] = copy.deepcopy(error)
        record["summary_state_updated_at"] = now.isoformat()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def update_summary_checkpoint(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    claim_id: str,
    next_chunk: int,
    total_chunks: int,
    title: str,
    summary: str,
) -> dict[str, Any] | None:
    """Persist rolling summary progress while the current lease is valid."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict) or record.get("summary_claim_id") != claim_id:
            return None
        record["summary_checkpoint"] = {
            "title": str(title).strip(),
            "summary": str(summary).strip(),
            "target_round": max(0, int(record.get("summary_target_round") or 0)),
        }
        record["summary_checkpoint_next_chunk"] = max(0, int(next_chunk))
        record["summary_checkpoint_total_chunks"] = max(0, int(total_chunks))
        record["summary_state_updated_at"] = _now()
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def retry_summary(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> dict[str, Any] | None:
    """Immediately requeue one incomplete closed-session summary."""

    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        key = session_key(source, session_id)
        record = index.setdefault("sessions", {}).get(key)
        if not isinstance(record, dict):
            return None
        target_round = max(0, int(record.get("summary_target_round") or 0))
        completed_round = max(0, int(record.get("summary_completed_round") or 0))
        status = str(record.get("summary_status") or "none")
        if (
            record.get("lifecycle") != "closed"
            or target_round < 1
            or completed_round >= target_round
            or not record.get("archive_window")
            or status not in {"failed", "retry_wait", "exhausted"}
        ):
            return None
        record["summary_status"] = "queued"
        record["summary_retry_count"] = 0
        record["summary_consecutive_failures"] = 0
        record["summary_state_updated_at"] = _now()
        for field in (
            "summary_claim_id",
            "summary_claimed_at",
            "summary_retry_at",
            "summary_error",
        ):
            record.pop(field, None)
        index["sessions"][key] = record
        return _write_index_unlocked(root, user, index)["sessions"][key]


def remove_session(root: Path, user: str, source: str, session_id: str) -> None:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        index.setdefault("sessions", {}).pop(session_key(source, session_id), None)
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if _active_matches(active_session, source, session_id):
                index["active"].pop(active_key, None)
        _write_index_unlocked(root, user, index)


def remove_all_sessions(root: Path, user: str, source: str) -> int:
    with index_lock(root, user):
        path = index_path(root, user)
        index = _normalize_index(_read_json(path))
        sessions = index.setdefault("sessions", {})
        targets = [
            (key, record)
            for key, record in sessions.items()
            if isinstance(record, dict) and record.get("source") == source
        ]
        target_ids = {
            str(record.get("session_id") or "")
            for _, record in targets
        }
        for key, _ in targets:
            sessions.pop(key, None)
        for active_key, active_session in list(index.setdefault("active", {}).items()):
            if any(
                _active_matches(active_session, source, session_id)
                for session_id in target_ids
            ):
                index["active"].pop(active_key, None)
        if targets:
            _write_index_unlocked(root, user, index)
        return len(targets)


def list_records(
    root: Path,
    user: str,
    *,
    source: str | None = None,
    query: str = "",
) -> list[dict[str, Any]]:
    index = load_index(root, user)
    needle = query.strip().casefold()
    records = []
    for record in (index.get("sessions") or {}).values():
        if not isinstance(record, dict) or record.get("lifecycle") == "deleted":
            continue
        if source is not None and record.get("source") != source:
            continue
        if needle:
            searchable = " ".join(
                str(record.get(key) or "")
                for key in ("session_id", "conversation_id", "title", "summary", "archive_window")
            ).casefold()
            if needle not in searchable:
                continue
        records.append(copy.deepcopy(record))
    records.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return records
