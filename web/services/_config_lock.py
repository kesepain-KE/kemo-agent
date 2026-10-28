"""Bounded in-process locks shared by config and background mutations."""

from pathlib import Path
from threading import RLock

_LOCKS = tuple(RLock() for _ in range(64))


def config_lock(path: Path):
    return _LOCKS[hash(str(path.absolute()).casefold()) % len(_LOCKS)]
