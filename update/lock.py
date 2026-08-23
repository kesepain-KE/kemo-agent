"""Cross-process updater lock backed by the operating system."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid
from pathlib import Path

from ._utils import UpdateError
from .constants import ROOT


def _try_os_lock(fd: int) -> None:
    """Acquire a non-blocking OS lock and normalize platform errors."""

    if os.name == "nt":
        import msvcrt

        # ``msvcrt.locking`` locks from the current file position and needs a
        # byte to exist in the file.
        os.lseek(fd, 0, os.SEEK_SET)
        if os.fstat(fd).st_size < 1:
            os.write(fd, b"\0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise BlockingIOError(str(exc)) from exc
        return

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise BlockingIOError(str(exc)) from exc


def _release_os_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return

    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


class UpdateLock:
    """A small single-instance lock that works without extra dependencies.

    The lock file remains as a harmless marker, while the open file descriptor
    carries the OS lock.  The operating system releases it automatically if an
    updater crashes, so there is no unsafe PID-based stale-lock deletion race.
    """

    def __init__(self, *, root: Path = ROOT) -> None:
        self.path = root / ".update.lock"
        self._token = uuid.uuid4().hex
        self._fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "created_at": time.time(),
            "token": self._token,
        }
        encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                _try_os_lock(descriptor)
            except BlockingIOError as exc:
                raise UpdateError("已有更新正在运行，本次更新已停止。") from exc
            # Keep at least one byte in the file on Windows.  ``msvcrt.locking``
            # locks a byte range relative to the current file position; shrinking
            # the file to zero after acquiring that range can invalidate the
            # region on some Windows filesystems.  The payload is diagnostic
            # only, so sizing it to the new JSON is sufficient and still
            # replaces stale metadata atomically under the held lock.
            os.ftruncate(descriptor, max(1, len(encoded)))
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, encoded)
            os.fsync(descriptor)
            self._fd = descriptor
        except Exception:
            _release_os_lock(descriptor)
            os.close(descriptor)
            raise

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            _release_os_lock(self._fd)
            os.close(self._fd)
        finally:
            self._fd = None

    def __enter__(self) -> "UpdateLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        del exc_type, exc, tb
        self.release()
