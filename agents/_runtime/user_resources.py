"""Trusted convention-based resolution of one user's prompt resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def attach_user_prompt_sources(
    registry: Any,
    root: Path,
    user: str,
) -> None:
    """Attach data-only user resources without importing user Python files."""

    user_base = root.resolve() / "users" / user
    registry.add_skills("user", user_base / "user_skills")
    registry.add_user_expands(user_base / "expand")
