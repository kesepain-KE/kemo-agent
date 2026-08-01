"""Cross-platform helpers for durable atomic file replacement."""

from __future__ import annotations

import errno
import os
from pathlib import Path
import time
from typing import Iterable


ATOMIC_REPLACE_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2)
_TRANSIENT_REPLACE_ERRNOS = frozenset({errno.EACCES, errno.EPERM, errno.EBUSY})
_TRANSIENT_WINDOWS_REPLACE_ERRORS = frozenset({5, 32, 33})


def replace_with_retry(
    source: Path,
    target: Path,
    *,
    retry_delays: Iterable[float] = ATOMIC_REPLACE_RETRY_DELAYS,
) -> None:
    """Atomically replace ``target`` while tolerating brief sharing violations."""

    delays = tuple(retry_delays)
    for attempt in range(len(delays) + 1):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            transient = (
                exc.errno in _TRANSIENT_REPLACE_ERRNOS
                or getattr(exc, "winerror", None)
                in _TRANSIENT_WINDOWS_REPLACE_ERRORS
            )
            if not transient or attempt >= len(delays):
                raise
            time.sleep(delays[attempt])

