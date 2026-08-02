"""SQLite fixture helpers for memory tests."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from run.memory_store import connection


_ALLOWED_METADATA = frozenset(
    {
        "weight",
        "created_at",
        "content_updated_at",
        "last_used_at",
        "last_weight_date",
        "tier_entered_at",
        "expires_at",
    }
)


def update_fragment_metadata(
    store: Any,
    tier: str,
    filename: str,
    **changes: Any,
) -> None:
    """Update one existing SQLite row to construct lifecycle test fixtures."""

    unknown = set(changes) - _ALLOWED_METADATA
    if unknown:
        raise ValueError("unsupported memory metadata: " + ", ".join(sorted(unknown)))
    if not changes:
        return
    values = {
        name: value.isoformat() if isinstance(value, datetime) else value
        for name, value in changes.items()
    }
    assignments = ", ".join(f"{name}=?" for name in values)
    with connection(store.root, store.user, write=True) as database:
        changed = database.execute(
            f"""
            UPDATE memory_fragments
            SET {assignments}, revision=revision+1
            WHERE tier=? AND filename=?
            """,
            (*values.values(), tier, filename),
        ).rowcount
        if changed != 1:
            raise AssertionError(f"memory fragment not found: {tier}/{filename}")
