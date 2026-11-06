"""Session-scoped writer serialization and live-execution fencing."""

from __future__ import annotations

import copy
import logging
import os
import threading
from pathlib import Path
from typing import Any


_SESSION_LOCKS: dict[tuple[str, str, str, str], Any] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


class _SessionLock:
    """Reentrant writer lock with a durable fence for response-closed children."""
    def __init__(self, root, user, source, session_id):
        self.identity = root, user, source, session_id
        self.lock = threading.RLock()
        self.depth = threading.local()

    def acquire(self, blocking=True, timeout=-1):
        if not self.lock.acquire(blocking, timeout):
            return False
        try:
            if not getattr(self.depth, "value", 0):
                from run.agents import ensure_session_execution_available
                ensure_session_execution_available(*self.identity)
        except BaseException as exc:
            self.lock.release()
            if not blocking and isinstance(exc, Exception):
                return False
            raise
        self.depth.value = getattr(self.depth, "value", 0) + 1
        return True

    def release(self):
        self.lock.release()
        self.depth.value -= 1

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def session_lock(
    root: Path,
    user: str,
    source: str,
    session_id: str,
) -> _SessionLock:
    # Windows paths are case-insensitive.  Normalize only the path component
    # so aliases such as ``E:\\code\\kemo-agent`` and ``e:\\code\\kemo-agent``
    # cannot bypass the same in-process session lock; user/source/session
    # values remain case-sensitive protocol identities.
    key = (os.path.normcase(str(root.resolve())), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        if key not in _SESSION_LOCKS:
            _SESSION_LOCKS[key] = _SessionLock(root, user, source, session_id)
        return _SESSION_LOCKS[key]
