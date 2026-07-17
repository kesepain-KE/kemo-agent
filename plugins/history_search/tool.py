from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def run(query: str, limit: int = 10, *, context: dict[str, Any]) -> dict[str, Any]:
    needle = query.casefold().strip()
    if not needle:
        return {"query": query, "matches": []}
    history_dir = Path(context["root"]) / "users" / context["user"] / "history"
    matches: list[dict[str, Any]] = []
    if not history_dir.is_dir():
        return {"query": query, "matches": matches}
    for directory in sorted(history_dir.iterdir(), reverse=True):
        if not directory.is_dir():
            continue
        try:
            data = json.loads((directory / "data.json").read_text("utf-8"))
            text = json.loads((directory / "text.json").read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or data.get("complete") is not True:
            continue
        for index, message in enumerate(text.get("messages") or []):
            if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
                continue
            content = message.get("content")
            if not isinstance(content, str) or needle not in content.casefold():
                continue
            matches.append(
                {
                    "window": directory.name,
                    "source": data.get("source"),
                    "session_id": data.get("session_id"),
                    "message_index": index,
                    "role": message.get("role"),
                    "content": content,
                }
            )
            if len(matches) >= limit:
                return {"query": query, "matches": matches}
    return {"query": query, "matches": matches}
