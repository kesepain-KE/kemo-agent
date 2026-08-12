"""Persistent, device-targeted command queue for the Android App bridge."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any


SUPPORTED_ACTIONS = frozenset({
    "alarm.create",
    "timer.start",
    "calendar.event.create",
    "todo.create",
})
FINAL_STATUSES = frozenset({"presented", "completed", "cancelled", "failed", "expired", "unsupported"})
ACK_STATUSES = frozenset({
    "received", "waiting_user", "presented", "completed", "cancelled",
    "failed", "expired", "unsupported", "duplicate_ignored",
})
STATUS_TRANSITIONS = {
    "queued": frozenset({
        "received", "waiting_user", "presented", "completed", "cancelled",
        "failed", "expired", "unsupported",
    }),
    "received": frozenset({
        "waiting_user", "presented", "completed", "cancelled", "failed",
        "expired", "unsupported",
    }),
    "waiting_user": frozenset({
        "presented", "completed", "cancelled", "failed", "expired",
        "unsupported",
    }),
}
LOCK_ACQUIRE_TIMEOUT_SECONDS = 5.0


@contextmanager
def _exclusive_file_lock(path: Path):
    """Serialize read-modify-write cycles across bridge/control processes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.monotonic() + LOCK_ACQUIRE_TIMEOUT_SECONDS
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"设备命令存储锁等待超时：{path}")
                    time.sleep(0.01)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"设备命令存储锁等待超时：{path}")
                    time.sleep(0.01)
        yield
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _non_empty(value: Any, name: str, maximum: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} 不能为空")
    if len(text) > maximum:
        raise ValueError(f"{name} 超过 {maximum} 字符")
    return text


def _integer(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是整数")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if number < minimum or number > maximum:
        raise ValueError(f"{name} 必须在 {minimum}..{maximum} 之间")
    return number


def _timestamp(value: Any, name: str) -> str:
    text = _non_empty(value, name, 80)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} 必须是 ISO-8601 时间并包含时区") from exc
    return text


def validate_arguments(action: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("arguments 必须是 JSON 对象")
    arguments = dict(value)
    if action == "alarm.create":
        result: dict[str, Any] = {
            "hour": _integer(arguments.get("hour"), "hour", 0, 23),
            "minute": _integer(arguments.get("minute"), "minute", 0, 59),
            "label": str(arguments.get("label") or "")[:200],
            "vibrate": bool(arguments.get("vibrate", True)),
        }
        days = arguments.get("repeat_days", [])
        if not isinstance(days, list):
            raise ValueError("repeat_days 必须是数组")
        result["repeat_days"] = sorted({_integer(day, "repeat_days", 1, 7) for day in days})
        return result
    if action == "timer.start":
        return {
            "duration_seconds": _integer(arguments.get("duration_seconds"), "duration_seconds", 1, 86_400),
            "label": str(arguments.get("label") or "")[:200],
        }
    if action == "calendar.event.create":
        start_at = _timestamp(arguments.get("start_at"), "start_at")
        end_at = _timestamp(arguments.get("end_at"), "end_at")
        if datetime.fromisoformat(end_at.replace("Z", "+00:00")) <= datetime.fromisoformat(start_at.replace("Z", "+00:00")):
            raise ValueError("end_at 必须晚于 start_at")
        return {
            "title": _non_empty(arguments.get("title"), "title", 200),
            "description": str(arguments.get("description") or "")[:4000],
            "location": str(arguments.get("location") or "")[:500],
            "start_at": start_at,
            "end_at": end_at,
            "all_day": bool(arguments.get("all_day", False)),
        }
    if action == "todo.create":
        result = {
            "title": _non_empty(arguments.get("title"), "title", 200),
            "notes": str(arguments.get("notes") or "")[:4000],
        }
        if arguments.get("due_at"):
            result["due_at"] = _timestamp(arguments.get("due_at"), "due_at")
        if arguments.get("reminder_at"):
            result["reminder_at"] = _timestamp(arguments.get("reminder_at"), "reminder_at")
        return result
    raise ValueError(f"不支持的设备动作: {action}")


class DeviceCommandStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock_path = path.with_suffix(path.suffix + ".lock")
        self._lock = threading.RLock()

    @contextmanager
    def _transaction(self):
        with self._lock:
            with _exclusive_file_lock(self.lock_path):
                yield

    def enqueue(
        self,
        *,
        username: str,
        device_id: str,
        action: str,
        arguments: Any,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        user = _non_empty(username, "user", 128)
        target = _non_empty(device_id, "device_id", 128)
        normalized_action = _non_empty(action, "action", 128).casefold()
        if normalized_action not in SUPPORTED_ACTIONS:
            raise ValueError(f"不支持的设备动作: {normalized_action}")
        ttl = _integer(ttl_seconds, "ttl_seconds", 30, 86_400)
        now = int(time.time())
        command = {
            "protocol_version": 1,
            "command_id": f"cmd_{uuid.uuid4().hex}",
            "user": user,
            "device_id": target,
            "action": normalized_action,
            "arguments": validate_arguments(normalized_action, arguments),
            "confirmation": "system_ui",
            "status": "queued",
            "created_at": now,
            "expires_at": now + ttl,
            "updated_at": now,
            "detail": {},
        }
        with self._transaction():
            state = self._read()
            state["commands"].append(command)
            self._prune(state, now)
            self._write(state)
        return deepcopy(command)

    def pending_for(self, username: str, device_id: str) -> list[dict[str, Any]]:
        now = int(time.time())
        with self._transaction():
            state = self._read()
            changed = self._expire(state, now)
            pending = [
                deepcopy(item) for item in state["commands"]
                if item.get("user") == username
                and item.get("device_id") == device_id
                and item.get("status") not in FINAL_STATUSES
                and int(item.get("expires_at") or 0) > now
            ]
            if changed:
                self._write(state)
        return pending

    def update(self, command_id: str, *, username: str, device_id: str, status: str, detail: Any = None) -> dict[str, Any] | None:
        normalized = str(status or "").strip().casefold()
        if normalized not in ACK_STATUSES:
            raise ValueError(f"不支持的命令状态: {status}")
        now = int(time.time())
        with self._transaction():
            state = self._read()
            self._expire(state, now)
            found = None
            for item in state["commands"]:
                if item.get("command_id") != command_id:
                    continue
                if item.get("user") != username or item.get("device_id") != device_id:
                    return None
                current_status = str(item.get("status") or "queued")
                if normalized == "duplicate_ignored":
                    found = deepcopy(item)
                    break
                if current_status in FINAL_STATUSES:
                    if normalized == current_status:
                        found = deepcopy(item)
                        break
                    raise ValueError(
                        f"命令 {command_id} 已处于终态 {current_status}，不能改为 {normalized}"
                    )
                allowed = STATUS_TRANSITIONS.get(current_status, frozenset())
                if normalized not in allowed:
                    raise ValueError(
                        f"命令 {command_id} 状态不能从 {current_status} 改为 {normalized}"
                    )
                item["status"] = normalized
                item["updated_at"] = now
                item["detail"] = detail if isinstance(detail, dict) else {}
                found = deepcopy(item)
                break
            self._prune(state, now)
            self._write(state)
        return found

    def get(self, command_id: str, *, username: str | None = None) -> dict[str, Any] | None:
        with self._transaction():
            state = self._read()
            changed = self._expire(state, int(time.time()))
            for item in state["commands"]:
                if item.get("command_id") == command_id:
                    if username is not None and item.get("user") != username:
                        if changed:
                            self._write(state)
                        return None
                    if changed:
                        self._write(state)
                    return deepcopy(item)
            if changed:
                self._write(state)
        return None

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            commands = value.get("commands", []) if isinstance(value, dict) else []
            return {"schema_version": 1, "commands": commands if isinstance(commands, list) else []}
        except FileNotFoundError:
            return {"schema_version": 1, "commands": []}
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"设备命令存储损坏或不可读：{self.path}") from exc

    def _write(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.replace(temporary, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _expire(state: dict[str, Any], now: int) -> bool:
        changed = False
        for item in state["commands"]:
            if item.get("status") not in FINAL_STATUSES and int(item.get("expires_at") or 0) <= now:
                item["status"] = "expired"
                item["updated_at"] = now
                changed = True
        return changed

    @classmethod
    def _prune(cls, state: dict[str, Any], now: int) -> None:
        cls._expire(state, now)
        cutoff = now - 7 * 24 * 60 * 60
        state["commands"] = [
            item for item in state["commands"]
            if item.get("status") not in FINAL_STATUSES or int(item.get("updated_at") or now) >= cutoff
        ][-500:]
