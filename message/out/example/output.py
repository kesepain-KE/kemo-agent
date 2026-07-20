"""Example output module implementing the platform send contract."""

from __future__ import annotations

from typing import Any


def send(message: dict[str, Any]) -> bool:
    """Validate the payload and report success without contacting a platform."""
    if message.get("chat_type") not in {"private", "group"}:
        return False
    if not str(message.get("external_chat_id") or "").strip():
        return False
    return bool(
        str(message.get("text") or "").strip()
        or str(message.get("file_path") or "").strip()
    )
