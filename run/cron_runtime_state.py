"""Process-local schedule state for high-frequency system cron tasks.

Task JSON files describe durable scheduling intent.  Updating their
``latest_run_at``/``next_run_at`` fields every few seconds creates needless
filesystem churn, so the live scheduler keeps those volatile fields here and
checkpoints them only at bounded intervals or shutdown.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
import threading
import time
from typing import Any


_LOCK = threading.RLock()
_STATES: dict[tuple[str, str, bool, str], dict[str, Any]] = {}
_LEASE_GUARD = threading.Lock()
_LEASED_ROOTS: set[str] = set()


class SystemCronLease:
    """Hold one cross-process system-scheduler leadership lease per root.

    Volatile ``next_run_at`` values are safe only while a single process owns
    system-task execution.  The operating-system file lock is released even
    after an unclean process exit, while the process-local set also prevents
    two scheduler objects in the same interpreter from becoming leaders.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.path = self.root / "runtime" / ".system-cron.lock"
        self._root_key = str(self.root).casefold()
        self._handle: Any | None = None

    @property
    def owned(self) -> bool:
        return self._handle is not None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            return True
        with _LEASE_GUARD:
            if self._root_key in _LEASED_ROOTS:
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
            except (BlockingIOError, OSError):
                handle.close()
                return False
            _LEASED_ROOTS.add(self._root_key)
            self._handle = handle
            return True

    def release(self) -> None:
        with _LEASE_GUARD:
            handle = self._handle
            if handle is None:
                return
            self._handle = None
            _LEASED_ROOTS.discard(self._root_key)
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _key(root: Path, user: str, system: bool, task_id: str) -> tuple[str, str, bool, str]:
    return (str(root.resolve()).casefold(), str(user), bool(system), str(task_id))


def overlay_cron_runtime(
    root: Path,
    user: str,
    system: bool,
    task: dict[str, Any],
) -> dict[str, Any]:
    rendered = copy.deepcopy(task)
    task_id = str(rendered.get("task_id") or "")
    if not task_id:
        return rendered
    with _LOCK:
        state = _STATES.get(_key(root, user, system, task_id))
        if isinstance(state, dict):
            for field in ("latest_run_at", "next_run_at", "status"):
                if field in state:
                    rendered[field] = copy.deepcopy(state[field])
    return rendered


def update_cron_runtime(
    root: Path,
    user: str,
    system: bool,
    task_id: str,
    *,
    latest_run_at: str,
    next_run_at: str,
    status: str = "enabled",
) -> dict[str, Any]:
    key = _key(root, user, system, task_id)
    now = time.monotonic()
    with _LOCK:
        previous = _STATES.get(key)
        state = {
            "root": str(root.resolve()),
            "user": str(user),
            "system": bool(system),
            "task_id": str(task_id),
            "latest_run_at": str(latest_run_at),
            "next_run_at": str(next_run_at),
            "status": str(status),
            "updated_monotonic": now,
            "checkpointed_monotonic": (
                float(previous.get("checkpointed_monotonic") or now)
                if isinstance(previous, dict)
                else now
            ),
            "dirty": True,
        }
        _STATES[key] = state
        return copy.deepcopy(state)


def runtime_checkpoint_due(state: dict[str, Any], interval_seconds: float) -> bool:
    if not state.get("dirty"):
        return False
    last = float(state.get("checkpointed_monotonic") or 0.0)
    return time.monotonic() - last >= max(1.0, float(interval_seconds))


def pending_cron_runtime(root: Path) -> list[dict[str, Any]]:
    normalized_root = str(root.resolve()).casefold()
    with _LOCK:
        return [
            copy.deepcopy(state)
            for key, state in _STATES.items()
            if key[0] == normalized_root and bool(state.get("dirty"))
        ]


def mark_cron_runtime_checkpoint(
    root: Path,
    user: str,
    system: bool,
    task_id: str,
    *,
    expected_updated_monotonic: float,
    expected_latest_run_at: str,
    expected_next_run_at: str,
    expected_status: str,
) -> bool:
    """Mark one successfully persisted snapshot as clean.

    The expected values make the acknowledgement conditional.  If another
    scan published a newer snapshot while the filesystem write was in flight,
    that newer state remains dirty and will be checkpointed later instead of
    being cleared by the older write.
    """

    key = _key(root, user, system, task_id)
    with _LOCK:
        state = _STATES.get(key)
        if not isinstance(state, dict) or not state.get("dirty"):
            return False
        try:
            same_updated = float(state.get("updated_monotonic")) == float(
                expected_updated_monotonic
            )
        except (TypeError, ValueError):
            return False
        if not same_updated:
            return False
        if any(
            str(state.get(field) or "") != expected
            for field, expected in (
                ("latest_run_at", str(expected_latest_run_at)),
                ("next_run_at", str(expected_next_run_at)),
                ("status", str(expected_status)),
            )
        ):
            return False
        state["checkpointed_monotonic"] = time.monotonic()
        state["dirty"] = False
        return True


def clear_cron_runtime(
    root: Path,
    user: str,
    system: bool,
    task_id: str,
) -> None:
    with _LOCK:
        _STATES.pop(_key(root, user, system, task_id), None)


__all__ = [
    "SystemCronLease",
    "clear_cron_runtime",
    "mark_cron_runtime_checkpoint",
    "overlay_cron_runtime",
    "pending_cron_runtime",
    "runtime_checkpoint_due",
    "update_cron_runtime",
]
