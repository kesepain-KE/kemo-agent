"""Example input module implementing the blocking lifecycle contract."""

from __future__ import annotations

import threading
from typing import Any


_STOP_EVENT = threading.Event()


def start(
    config: dict[str, Any],
    message_buffer: str,
    files_dir: str,
    state_path: str,
) -> None:
    """Wait until stop; real adapters receive and append platform messages here."""
    del config, message_buffer, files_dir, state_path
    _STOP_EVENT.clear()
    while not _STOP_EVENT.wait(0.5):
        pass


def stop() -> None:
    """Stop the example receiver."""
    _STOP_EVENT.set()
