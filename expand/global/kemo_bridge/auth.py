"""Local password records retained for existing accounts; never used as peer credentials."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import Any


class UserStore:
    def __init__(self, path: Path, iterations: int = 310_000) -> None:
        self.path = path
        self.iterations = max(100_000, int(iterations))

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def verify(self, username: str, password: str) -> bool:
        record = self._load().get(username)
        if not isinstance(record, dict) or not record.get("enabled", True):
            return False
        try:
            salt = bytes.fromhex(str(record["salt"]))
            expected = bytes.fromhex(str(record["hash"]))
            iterations = int(record.get("iterations", self.iterations))
        except (KeyError, TypeError, ValueError):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)

    def agent_user(self, username: str) -> str:
        record = self._load().get(username)
        if not isinstance(record, dict):
            return username
        configured = str(record.get("agent_user") or "").strip()
        return configured or username

    def set_password(self, username: str, password: str, enabled: bool = True) -> None:
        if not username or len(password) < 10:
            raise ValueError("username 不能为空，password 至少 10 个字符")
        data = self._load()
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.iterations)
        previous = (checked_139 if isinstance(checked_139 := data.get(username), dict) else {})
        data[username] = {
            "salt": salt.hex(),
            "hash": digest.hex(),
            "iterations": self.iterations,
            "enabled": bool(enabled),
        }
        if previous.get("agent_user"):
            data[username]["agent_user"] = str(previous["agent_user"])
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
