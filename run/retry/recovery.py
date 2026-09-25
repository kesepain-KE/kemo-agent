"""Shared bounded recovery rules for attempt-level retries."""

from __future__ import annotations

from typing import Any


MAX_RECOVERY_CALLS = 32
MAX_RECOVERY_CHARS = 120_000
MAX_GUIDANCE_ITEMS = 32
MAX_GUIDANCE_CHARS = 120_000

_EXPAND_LIVE_READ_COMMANDS = frozenset(
    {"configuration_status", "query", "refresh", "status"}
)
_FILE_LIVE_READ_ACTIONS = frozenset(
    {"exists", "hash", "list_dir", "read", "read_range", "search", "stat", "tree_dir"}
)


def reuse_allowed(name: str, arguments: dict[str, Any]) -> bool:
    """Whether a successful tool result stays safe to replay within one Run."""

    if name == "expand_call":
        command = str(arguments.get("command") or "").strip().casefold()
        return command not in _EXPAND_LIVE_READ_COMMANDS
    if name == "file":
        action = str(arguments.get("action") or "").strip().casefold()
        return action not in _FILE_LIVE_READ_ACTIONS
    return True
