"""Web appearance transport; locale belongs exclusively to user_config.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from run.config import read_json_object, web_background, web_language
from web.errors import InvalidRequestError
from web.services._io import atomic_write


def read_preferences(root: Path, user: str) -> dict[str, Any]:
    base = root / "users" / user
    value = read_json_object(base / "web_preferences.json", allow_empty=True)
    appearance = value.get("appearance")
    appearance = appearance if isinstance(appearance, dict) else {}
    config = read_json_object(base / "user_config.json", allow_empty=True)
    return {
        "user": user,
        "appearance": {
            "theme": appearance.get("theme", "light"),
            "font_size": appearance.get("font_size", "medium"),
            "language": web_language(config),
            "background": web_background(config),
        },
    }


def write_preferences(root: Path, user: str, changes: Any) -> dict[str, Any]:
    if not isinstance(changes, dict):
        raise InvalidRequestError("appearance 必须是对象")
    if {"language", "background"}.intersection(changes):
        raise InvalidRequestError("语言与背景请通过用户配置 web_ui 保存")
    current = read_preferences(root, user)["appearance"]
    theme = changes.get("theme", current["theme"])
    font_size = changes.get("font_size", current["font_size"])
    if theme not in {"light", "dark"}:
        raise InvalidRequestError("theme 只允许 light 或 dark")
    if font_size not in {"small", "medium", "large"}:
        raise InvalidRequestError("font_size 只允许 small、medium 或 large")
    value = {"schema_version": 1, "appearance": {"theme": theme, "font_size": font_size}}
    path = root / "users" / user / "web_preferences.json"
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
    return {**read_preferences(root, user), "updated": True}
