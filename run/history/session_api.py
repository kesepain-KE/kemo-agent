"""Conversation discovery, memory queueing, and user-facing session operations."""

from __future__ import annotations

import copy
from pathlib import Path
import shutil
from typing import Any

from run.config import user_dir
from run.history.commit_ops import commit_window, patch_archive_metadata
from run.history.history_models import HistoryError, _lock, _now, _window_name, empty_window
from run.history.index import (
    find_record as find_index_record,
    list_records as list_index_records,
    list_records_page as list_index_records_page,
    remove_all_sessions as remove_all_index_sessions,
    remove_session as remove_index_session,
    update_title as update_index_title,
)
from run.history.runtime_window import load_window, runtime_window_path
from run.history.store import (
    delete_session_windows,
    delete_source_windows,
    find_window_name,
    list_windows as list_stored_windows,
    rename_windows,
    window_exists,
)


def find_window(root: Path, user: str, source: str, session_id: str) -> Path | None:
    history_dir = user_dir(user, root) / "history"
    # The registry normally knows the exact logical window identifier.
    indexed = find_index_record(root, user, source, session_id)
    archive_window = str((indexed or {}).get("archive_window") or "")
    if archive_window and Path(archive_window).name == archive_window:
        indexed_directory = history_dir / archive_window
        if window_exists(indexed_directory):
            return indexed_directory
    stored_name = find_window_name(root, user, source, session_id)
    return history_dir / stored_name if stored_name else None


def queue_memory_extraction(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    target_round: int | None = None,
    reason: str = "session_closed",
) -> dict[str, Any]:
    """Durably queue a bounded set of committed rounds for memory extraction."""

    directory = find_window(root, user, source, session_id)
    if directory is None:
        return {
            "status": "skipped",
            "reason": "no_archive",
            "rounds": 0,
            "processed_round": 0,
        }
    window = load_window(directory)
    data = window.setdefault("data", {})
    rounds = max(0, int(data.get("rounds") or 0))
    processed_round = max(0, int(data.get("memory_processed_round") or 0))
    requested_target = rounds if target_round is None else max(0, int(target_round))
    requested_target = min(rounds, requested_target)
    existing_target = max(0, int(data.get("memory_target_round") or 0))
    bounded_target = max(requested_target, existing_target)
    if rounds < 1 or processed_round >= bounded_target:
        return {
            "status": "skipped",
            "reason": (
                "already_processed"
                if reason == "manual_compression"
                else "no_pending_rounds"
            ),
            "rounds": rounds,
            "processed_round": processed_round,
            "target_round": bounded_target,
            "pending_rounds": 0,
        }
    from run.config import load_config
    from run.memory import memory_extraction_mode

    if memory_extraction_mode(load_config(user, root)) == "disabled":
        return {
            "status": "skipped",
            "reason": "memory_extraction_disabled",
            "rounds": rounds,
            "processed_round": processed_round,
            "target_round": bounded_target,
            "pending_rounds": 0,
        }
    data["memory_status"] = "queued"
    data["memory_queue_reason"] = str(reason or "session_closed")
    data["memory_target_round"] = bounded_target
    data["memory_queued_at"] = _now()
    data.pop("memory_error", None)
    patch_archive_metadata(
        directory,
        window,
        updates={
            "memory_status": "queued",
            "memory_queue_reason": data["memory_queue_reason"],
            "memory_target_round": bounded_target,
            "memory_queued_at": data["memory_queued_at"],
        },
        removals=("memory_error",),
    )
    indexed = find_index_record(root, user, source, session_id)
    if (
        not isinstance(indexed, dict)
        or str(indexed.get("memory_status") or "") not in {"queued", "processing"}
        or int(indexed.get("memory_target_round") or 0) != bounded_target
        or int(indexed.get("memory_processed_round") or 0) != processed_round
    ):
        raise HistoryError("记忆后台队列未能同步写入历史索引")
    return {
        "status": "queued",
        "reason": data["memory_queue_reason"],
        "rounds": rounds,
        "processed_round": processed_round,
        "target_round": bounded_target,
        "pending_rounds": bounded_target - processed_round,
    }


def prepare_window(
    root: Path, user: str, source: str, session_id: str
) -> tuple[Path, dict[str, Any], bool]:
    """Load a committed window or prepare a new in-memory window.

    A new directory is not written until ``commit_window`` succeeds after the
    provider response.  This prevents failed requests from creating empty
    conversation windows.
    """

    indexed = find_index_record(root, user, source, session_id)
    existing = find_window(root, user, source, session_id)
    if existing is not None:
        window = load_window(existing)
        generation = str((indexed or {}).get("session_generation") or "").strip()
        if generation:
            data = window.get("data")
            if isinstance(data, dict) and not str(
                data.get("session_generation") or ""
            ).strip():
                data["session_generation"] = generation
        return existing, window, False
    history_dir = user_dir(user, root) / "history"
    generation = str((indexed or {}).get("session_generation") or "").strip()
    return (
        history_dir / _window_name(session_id),
        empty_window(
            user,
            source,
            session_id,
            session_generation=generation,
        ),
        True,
    )


def get_or_create_window(
    root: Path, user: str, source: str, session_id: str
) -> tuple[Path, dict[str, Any]]:
    directory, window, is_new = prepare_window(root, user, source, session_id)
    if is_new:
        commit_window(directory, window)
        commit_window(runtime_window_path(directory), copy.deepcopy(window))
    return directory, window


def _session_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(record.get("source") or ""),
        "bound_platform": str(record.get("bound_platform") or ""),
        "session_id": str(record.get("session_id") or ""),
        "conversation_id": str(record.get("conversation_id") or ""),
        "window": str(record.get("archive_window") or ""),
        "title": str(record.get("title") or ""),
        "summary": str(record.get("summary") or ""),
        "summary_status": str(record.get("summary_status") or "none"),
        "summary_target_round": int(record.get("summary_target_round") or 0),
        "summary_completed_round": int(record.get("summary_completed_round") or 0),
        "summary_retry_at": str(record.get("summary_retry_at") or ""),
        "summary_retry_count": max(0, int(record.get("summary_retry_count") or 0)),
        "summary_attempt_count": max(0, int(record.get("summary_attempt_count") or 0)),
        "summary_consecutive_failures": max(
            0, int(record.get("summary_consecutive_failures") or 0)
        ),
        "summary_max_attempts": max(1, int(record.get("summary_max_attempts") or 5)),
        "summary_last_attempt_at": str(record.get("summary_last_attempt_at") or ""),
        "summary_recovered_at": str(record.get("summary_recovered_at") or ""),
        "summary_last_error": copy.deepcopy(
            record.get("summary_last_error")
            if isinstance(record.get("summary_last_error"), dict)
            else None
        ),
        "summary_checkpoint_next_chunk": max(
            0, int(record.get("summary_checkpoint_next_chunk") or 0)
        ),
        "summary_checkpoint_total_chunks": max(
            0, int(record.get("summary_checkpoint_total_chunks") or 0)
        ),
        "state": str(record.get("lifecycle") or "open"),
        "run_state": str(record.get("run_state") or "idle"),
        "chain": str(record.get("chain") or ""),
        "memory_status": str(record.get("memory_status") or "unknown"),
        "memory_processed_round": max(
            0, int(record.get("memory_processed_round") or 0)
        ),
        "memory_target_round": max(0, int(record.get("memory_target_round") or 0)),
        "memory_queue_reason": str(record.get("memory_queue_reason") or ""),
        "memory_queued_at": str(record.get("memory_queued_at") or ""),
        "memory_last_error": copy.deepcopy(
            record.get("memory_last_error")
            if isinstance(record.get("memory_last_error"), dict)
            else None
        ),
        "rounds": int(record.get("rounds") or 0),
        "updated_at": str(record.get("updated_at") or ""),
    }


def list_sessions(
    root: Path,
    user: str,
    source: str | None,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    return [
        _session_payload(record)
        for record in list_index_records(root, user, source=source, query=query)
    ]


def list_sessions_page(
    root: Path,
    user: str,
    source: str | None,
    *,
    query: str = "",
    limit: int = 50,
    before_updated_at: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    records, has_more = list_index_records_page(
        root,
        user,
        source=source,
        query=query,
        limit=limit,
        before_updated_at=before_updated_at,
    )
    return [_session_payload(record) for record in records], has_more


def _source_windows(root: Path, user: str, source: str) -> list[tuple[Path, str]]:
    """Return committed logical windows for one user/source pair."""

    history_dir = user_dir(user, root) / "history"
    return [
        (history_dir / str(item["window_name"]), str(item["session_id"]))
        for item in list_stored_windows(root, user, source=source)
        if str(item.get("session_id") or "")
    ]


def _matching_windows(
    root: Path, user: str, source: str, session_id: str
) -> list[Path]:
    """Return only committed windows whose stored identity matches exactly."""

    return [
        directory
        for directory, stored_session_id in _source_windows(root, user, source)
        if stored_session_id == session_id
    ]


def rename_session(
    root: Path, user: str, source: str, session_id: str, title: str
) -> int:
    """Persist a display title without changing chronological ordering."""

    changed_windows = rename_windows(root, user, source, session_id, title)
    indexed = update_index_title(root, user, source, session_id, title)
    return max(changed_windows, 1 if indexed is not None else 0)


def _remove_window_cache(directory: Path) -> None:
    for candidate in (runtime_window_path(directory), directory):
        if candidate.is_dir():
            with _lock(candidate):
                shutil.rmtree(candidate)


def delete_session(root: Path, user: str, source: str, session_id: str) -> int:
    """Delete every verified history window belonging to a session."""

    indexed = find_index_record(root, user, source, session_id)
    directories = _matching_windows(root, user, source, session_id)
    deleted_windows = delete_session_windows(root, user, source, session_id)
    for directory in directories:
        _remove_window_cache(directory)
    remove_index_session(root, user, source, session_id)
    return max(deleted_windows, 1 if indexed is not None else 0)


def delete_all_sessions(root: Path, user: str, source: str) -> tuple[int, int]:
    """Delete every verified history window for a user/source pair."""

    directories = [directory for directory, _ in _source_windows(root, user, source)]
    deleted_sessions, deleted_windows = delete_source_windows(root, user, source)
    for directory in directories:
        _remove_window_cache(directory)
    removed_index_sessions = remove_all_index_sessions(root, user, source)
    return max(deleted_sessions, removed_index_sessions), deleted_windows


def clear_session(root: Path, user: str, source: str, session_id: str) -> Path:
    existing = find_window(root, user, source, session_id)
    directory = existing or user_dir(user, root) / "history" / _window_name(session_id)
    indexed = find_index_record(root, user, source, session_id)
    window = empty_window(
        user,
        source,
        session_id,
        session_generation=str((indexed or {}).get("session_generation") or ""),
    )
    commit_window(directory, window)
    commit_window(runtime_window_path(directory), copy.deepcopy(window))
    return directory


def session_messages(
    root: Path, user: str, source: str, session_id: str
) -> list[dict[str, Any]]:
    directory = find_window(root, user, source, session_id)
    if directory is None:
        return []
    window = load_window(directory)
    return [
        dict(message)
        for message in window["text"].get("messages", [])
        if isinstance(message, dict)
    ]
