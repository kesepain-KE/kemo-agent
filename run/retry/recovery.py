"""Shared bounded recovery rules for attempt-level retries."""

from __future__ import annotations

MAX_RECOVERY_CALLS = 32
MAX_RECOVERY_CHARS = 120_000
MAX_GUIDANCE_ITEMS = 32
MAX_GUIDANCE_CHARS = 120_000

_FILE_LIVE_READ_ACTIONS = frozenset(
    {"exists", "hash", "list_dir", "read", "read_range", "search", "stat", "tree_dir"}
)
_LIVE_READ_ACTIONS = {
    "subagent_dispatch": frozenset({"list", "status"}),
    "shell": frozenset({"status"}),
    "network": frozenset({"get", "read"}),
}
