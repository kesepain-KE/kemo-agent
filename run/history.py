
"""会话窗口存储合约。

窗口是一个带时间戳的目录，包含text.json、think.json、
工具.json 和数据.json。  data.json 最后以``complete=true`` 提交；
读者永远不会将部分编写的四文件集视为完整的。"""

from __future__ import annotations

import json
import os
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.users import user_dir


SCHEMA_VERSION = 1
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class HistoryError(RuntimeError):
    """Conversation history cannot be read or committed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_name() -> str:
    local = datetime.now().astimezone().strftime("%Y-%m-%d-%H-%M-%S-%f")
    return f"{local}-{uuid.uuid4().hex[:6]}"


def _lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoryError(f"历史文件不可读：{path}（{exc}）") from exc


def empty_window(user: str, source: str, session_id: str) -> dict[str, Any]:
    timestamp = _now()
    return {
        "text": {"schema_version": SCHEMA_VERSION, "messages": []},
        "think": {"schema_version": SCHEMA_VERSION, "rounds": []},
        "tool": {"schema_version": SCHEMA_VERSION, "rounds": []},
        "data": {
            "schema_version": SCHEMA_VERSION,
            "user": user,
            "source": source,
            "session_id": session_id,
            "title": "",
            "created_at": timestamp,
            "updated_at": timestamp,
            "rounds": 0,
            "round_metrics": [],
            "token_usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated": False,
            },
            "complete": True,
        },
    }


def commit_window(directory: Path, window: dict[str, Any]) -> None:
    """Atomically replace each file and mark the four-file set complete last."""

    with _lock(directory):
        data = dict(window["data"])
        # 会话标题由独立的重命名 API 管理。运行中的请求可能持有一份较早
        # 加载的 window，因此提交对话内容时应保留磁盘上的最新标题。
        existing_data_path = directory / "data.json"
        if existing_data_path.is_file():
            try:
                existing_data = _read_json(existing_data_path)
            except HistoryError:
                existing_data = None
            if isinstance(existing_data, dict) and isinstance(existing_data.get("title"), str):
                data["title"] = existing_data["title"]
        data["updated_at"] = _now()
        data["complete"] = False
        _atomic_write_json(directory / "data.json", data)
        _atomic_write_json(directory / "text.json", window["text"])
        _atomic_write_json(directory / "think.json", window["think"])
        _atomic_write_json(directory / "tool.json", window["tool"])
        data["complete"] = True
        _atomic_write_json(directory / "data.json", data)
        window["data"] = data


def load_window(directory: Path) -> dict[str, Any]:
    with _lock(directory):
        paths = {name: directory / f"{name}.json" for name in ("text", "think", "tool", "data")}
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise HistoryError(f"历史窗口不完整：{directory}，缺少 {', '.join(missing)}")
        data = _read_json(paths["data"])
        if not isinstance(data, dict) or not data.get("complete"):
            raise HistoryError(f"历史窗口尚未完成提交：{directory}")
        window = {name: _read_json(path) for name, path in paths.items() if name != "data"}
        window["data"] = data
        if not isinstance(window["text"], dict) or not isinstance(window["text"].get("messages"), list):
            raise HistoryError(f"text.json schema 无效：{directory}")
        return window


def find_window(root: Path, user: str, source: str, session_id: str) -> Path | None:
    history_dir = user_dir(user, root) / "history"
    if not history_dir.is_dir():
        return None
    candidates: list[tuple[str, Path]] = []
    for directory in history_dir.iterdir():
        if not directory.is_dir():
            continue
        try:
            data = _read_json(directory / "data.json")
        except HistoryError:
            continue
        if (
            isinstance(data, dict)
            and data.get("complete") is True
            and data.get("source") == source
            and data.get("session_id") == session_id
        ):
            candidates.append((str(data.get("updated_at") or ""), directory))
    return max(candidates, default=("", None), key=lambda item: item[0])[1]


def prepare_window(root: Path, user: str, source: str, session_id: str) -> tuple[Path, dict[str, Any], bool]:
    """Load a committed window or prepare a new in-memory window.

    A new directory is not written until ``commit_window`` succeeds after the
    provider response.  This prevents failed requests from creating empty
    conversation windows.
    """

    existing = find_window(root, user, source, session_id)
    if existing is not None:
        return existing, load_window(existing), False
    history_dir = user_dir(user, root) / "history"
    return history_dir / _window_name(), empty_window(user, source, session_id), True


def get_or_create_window(root: Path, user: str, source: str, session_id: str) -> tuple[Path, dict[str, Any]]:
    directory, window, is_new = prepare_window(root, user, source, session_id)
    if is_new:
        commit_window(directory, window)
    return directory, window


def list_sessions(root: Path, user: str, source: str) -> list[dict[str, Any]]:
    history_dir = user_dir(user, root) / "history"
    sessions: dict[str, dict[str, Any]] = {}
    if not history_dir.is_dir():
        return []
    for directory in history_dir.iterdir():
        if not directory.is_dir():
            continue
        try:
            data = _read_json(directory / "data.json")
        except HistoryError:
            continue
        if not isinstance(data, dict) or data.get("complete") is not True or data.get("source") != source:
            continue
        session_id = str(data.get("session_id") or "")
        item = {
            "session_id": session_id,
            "window": directory.name,
            "title": str(data.get("title") or ""),
            "rounds": int(data.get("rounds", 0)),
            "updated_at": str(data.get("updated_at") or ""),
        }
        previous = sessions.get(session_id)
        if previous is None or item["updated_at"] > previous["updated_at"]:
            sessions[session_id] = item
    return sorted(sessions.values(), key=lambda item: item["updated_at"], reverse=True)


def _source_windows(root: Path, user: str, source: str) -> list[tuple[Path, str]]:
    """Return verified committed windows for one user/source pair."""

    history_dir = user_dir(user, root) / "history"
    if not history_dir.is_dir():
        return []
    matches: list[tuple[Path, str]] = []
    for directory in history_dir.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            data = _read_json(directory / "data.json")
        except HistoryError:
            continue
        if (
            isinstance(data, dict)
            and data.get("complete") is True
            and data.get("user") == user
            and data.get("source") == source
        ):
            session_id = str(data.get("session_id") or "")
            if session_id:
                matches.append((directory, session_id))
    return matches


def _matching_windows(root: Path, user: str, source: str, session_id: str) -> list[Path]:
    """Return only committed windows whose stored identity matches exactly."""

    return [
        directory
        for directory, stored_session_id in _source_windows(root, user, source)
        if stored_session_id == session_id
    ]


def rename_session(root: Path, user: str, source: str, session_id: str, title: str) -> int:
    """Persist a display title across every window belonging to a session."""

    directories = _matching_windows(root, user, source, session_id)
    for directory in directories:
        with _lock(directory):
            data_path = directory / "data.json"
            data = _read_json(data_path)
            if not isinstance(data, dict) or data.get("complete") is not True:
                continue
            data["title"] = title
            # A cosmetic rename must not affect chronological session ordering.
            _atomic_write_json(data_path, data)
    return len(directories)


def delete_session(root: Path, user: str, source: str, session_id: str) -> int:
    """Delete every verified history window belonging to a session."""

    directories = _matching_windows(root, user, source, session_id)
    for directory in directories:
        with _lock(directory):
            shutil.rmtree(directory)
    return len(directories)


def delete_all_sessions(root: Path, user: str, source: str) -> tuple[int, int]:
    """Delete every verified history window for a user/source pair."""

    entries = _source_windows(root, user, source)
    session_ids = {session_id for _, session_id in entries}
    for directory, _ in entries:
        with _lock(directory):
            shutil.rmtree(directory)
    return len(session_ids), len(entries)


def clear_session(root: Path, user: str, source: str, session_id: str) -> Path:
    existing = find_window(root, user, source, session_id)
    directory = existing or user_dir(user, root) / "history" / _window_name()
    commit_window(directory, empty_window(user, source, session_id))
    return directory


def session_messages(root: Path, user: str, source: str, session_id: str) -> list[dict[str, Any]]:
    directory = find_window(root, user, source, session_id)
    if directory is None:
        return []
    window = load_window(directory)
    return [dict(message) for message in window["text"].get("messages", []) if isinstance(message, dict)]
