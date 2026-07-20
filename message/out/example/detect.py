"""Example health detector."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def check(config: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Return the complete refreshed state document."""
    del config
    return {
        **state,
        "schema_version": 1,
        "health": "healthy",
        "last_check": datetime.now().astimezone().isoformat(),
        "error": None,
    }
