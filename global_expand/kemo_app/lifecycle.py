"""Secret-safe initialization and activation state for the App bridge."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


BASE_DIR = Path(__file__).resolve().parent
TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def inspect_configuration(base_dir: Path | None = None) -> dict[str, Any]:
    """Return public initialization state without exposing any credential value."""

    root = (base_dir or BASE_DIR).resolve()
    config_path = root / "config.json"
    state: dict[str, Any] = {
        "initialized": config_path.is_file(),
        "configured": False,
        "missing": [],
        "host": "127.0.0.1",
        "port": 8742,
        "upstream_configured": False,
        "enabled_users": 0,
    }
    if not config_path.is_file():
        state["missing"] = ["config.json", "device_token", "session_secret", "user_account"]
        return state

    try:
        config = _load_json(config_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        state["error"] = "config_invalid"
        state["missing"] = ["valid_config"]
        return state
    if not isinstance(config, dict):
        state["error"] = "config_invalid"
        state["missing"] = ["valid_config"]
        return state

    missing: list[str] = []
    token_hash = str(config.get("token_sha256") or "").strip().lower()
    if not TOKEN_HASH.fullmatch(token_hash):
        missing.append("device_token")
    if len(str(config.get("session_secret") or "")) < 32:
        missing.append("session_secret")

    upstream = str(config.get("upstream") or "").strip()
    parsed = urlsplit(upstream)
    upstream_configured = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    if not upstream_configured:
        missing.append("upstream")

    users_path = root / str(config.get("users_path") or "users.json")
    enabled_users = 0
    try:
        users = _load_json(users_path)
    except (OSError, UnicodeError, json.JSONDecodeError):
        users = {}
    if isinstance(users, dict):
        enabled_users = sum(
            1
            for record in users.values()
            if isinstance(record, dict)
            and bool(record.get("enabled", True))
            and bool(str(record.get("salt") or ""))
            and bool(str(record.get("hash") or ""))
        )
    if enabled_users == 0:
        missing.append("user_account")

    try:
        port = int(config.get("port", 8742))
    except (TypeError, ValueError):
        port = 0
    if not 1 <= port <= 65535:
        missing.append("valid_port")
        port = 8742

    state.update(
        {
            "configured": not missing,
            "missing": missing,
            "host": str(config.get("host") or "127.0.0.1"),
            "port": port,
            "upstream_configured": upstream_configured,
            "enabled_users": enabled_users,
        }
    )
    return state


def load_ready_config(base_dir: Path | None = None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    root = (base_dir or BASE_DIR).resolve()
    state = inspect_configuration(root)
    if not state["configured"]:
        return None, state
    payload = _load_json(root / "config.json")
    if not isinstance(payload, dict):
        return None, {**state, "configured": False, "error": "config_invalid"}
    return payload, state
