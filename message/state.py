"""入站幂等性的持久每用户处理消息状态。"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MessageStateError(RuntimeError):
    pass


_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root: Path, user: str) -> threading.RLock:
    key = (str(root.resolve()).casefold(), user)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), "utf-8")
        os.replace(temporary, path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


class ProcessedMessageStore:
    def __init__(self, root: Path, user: str, *, max_entries: int = 2000) -> None:
        self.root = root.resolve()
        self.user = user
        self.max_entries = max(1, int(max_entries))
        self.path = self.root / "users" / user / "message_state" / "processed.json"
        self._lock = _lock_for(self.root, user)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text("utf-8"))
        except FileNotFoundError:
            return {"schema_version": 1, "messages": {}}
        except (OSError, json.JSONDecodeError) as exc:
            raise MessageStateError(f"消息状态文件不可读：{self.path}（{exc}）") from exc
        if not isinstance(value, dict) or not isinstance(value.get("messages"), dict):
            raise MessageStateError(f"消息状态文件结构无效：{self.path}")
        return value

    def claim(self, key: str) -> bool:
        return self.claim_many((key,))

    def claim_many(self, keys: tuple[str, ...]) -> bool:
        normalized = tuple(dict.fromkeys(str(key).strip() for key in keys if str(key).strip()))
        if not normalized:
            raise MessageStateError("消息幂等键不能为空")
        with self._lock:
            data = self._read()
            messages = data["messages"]
            if any(key in messages for key in normalized):
                return False
            now = _now()
            for key in normalized:
                messages[key] = {
                    "status": "processing",
                    "claimed_at": now,
                    "updated_at": now,
                    "error": None,
                }
            self._trim(messages, protected=set(normalized))
            _atomic_write(self.path, data)
            return True

    def complete(self, key: str, *, status: str, error: dict[str, Any] | None = None) -> None:
        self.complete_many((key,), status=status, error=error)

    def complete_many(
        self,
        keys: tuple[str, ...],
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise MessageStateError(f"终态无效：{status!r}")
        normalized = tuple(dict.fromkeys(str(key).strip() for key in keys if str(key).strip()))
        if not normalized:
            raise MessageStateError("消息幂等键不能为空")
        with self._lock:
            data = self._read()
            missing = [key for key in normalized if key not in data["messages"]]
            if missing:
                raise MessageStateError(f"消息尚未领取：{', '.join(missing)}")
            now = _now()
            for key in normalized:
                record = data["messages"][key]
                record["status"] = status
                record["updated_at"] = now
                record["error"] = error
            self._trim(data["messages"])
            _atomic_write(self.path, data)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._read()["messages"].get(key)
            return dict(record) if isinstance(record, dict) else None

    def recover_interrupted(self) -> list[str]:
        """Mark in-progress records failed; never replay possible side effects."""
        recovered: list[str] = []
        with self._lock:
            data = self._read()
            for key, record in data["messages"].items():
                if isinstance(record, dict) and record.get("status") == "processing":
                    record["status"] = "failed"
                    record["updated_at"] = _now()
                    record["error"] = {
                        "message": "宿主重启时消息仍在处理中；为避免重复副作用，不自动重放",
                        "phase": "recovery",
                    }
                    recovered.append(key)
            if recovered:
                _atomic_write(self.path, data)
        return recovered

    def _trim(
        self,
        messages: dict[str, Any],
        *,
        protected: set[str] | None = None,
    ) -> None:
        overflow = len(messages) - self.max_entries
        if overflow <= 0:
            return
        protected_keys = protected or set()
        ordered = sorted(
            (key for key in messages if key not in protected_keys),
            key=lambda key: str((messages.get(key) or {}).get("updated_at") or ""),
        )
        for key in ordered[:overflow]:
            messages.pop(key, None)
