"""UI-only read action; this never becomes an agent Prompt control tool."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from data_update import collect, load_timezone_name, resolve_timezone


def execute(command: str, params: dict[str, Any]) -> dict[str, Any]:
    if command != "clock_data":
        raise ValueError("未知的时间 UI 动作")
    tz, name, note = resolve_timezone(load_timezone_name())
    path = Path(__file__).parent / "module" / "status.json"
    status = json.loads(path.read_text("utf-8")) if path.exists() else {}
    return {
        "timezone": name, "current": collect(tz), "note": note,
        "last_collection": {key: status.get(key) for key in ("health", "timezone", "local", "offset", "last", "summary")},
    }
