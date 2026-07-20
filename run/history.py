
"""会话窗口双层存储合约。

归档路径（history/<timestamp>/）保存用户可见的完整对话，不受轮次上限
影响，也不允许上下文整理裁剪。临时工作区
（history/temp/<timestamp>/）是上游 Provider 使用的可变上下文窗口，受
agents.max_rounds 限制，允许保存压缩统计和局部轮号偏移。

两层都包含 text.json、think.json、tool.json、items.json 和 data.json。
data.json 最后以 complete=true 提交；读者不会把部分写入视为完整窗口。
"""

from __future__ import annotations

import copy
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
ITEMS_SCHEMA_VERSION = 2
_ARCHIVE_DATA_FIELDS = frozenset(
    {
        "schema_version",
        "user",
        "source",
        "session_id",
        "title",
        "created_at",
        "updated_at",
        "rounds",
        "round_metrics",
        "token_usage",
        "complete",
    }
)
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
        "items": {"schema_version": ITEMS_SCHEMA_VERSION, "items": []},
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


def _item_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _text_content(value: Any) -> list[dict[str, Any]]:
    return [{"type": "text", "text": str(value or "")}]


def _history_message_item(
    role: str,
    content: Any,
    *,
    round_number: int,
) -> dict[str, Any]:
    blocks = content if isinstance(content, list) else _text_content(content)
    item: dict[str, Any] = {
        "id": _item_id("msg"),
        "type": "message",
        "status": "completed",
        "role": role,
        "content": blocks,
        "metadata": {"round": round_number, "history_source": "legacy"},
        "extensions": {},
    }
    if role == "assistant":
        item["phase"] = "final_answer"
    return item


def synthesize_items(window: dict[str, Any]) -> dict[str, Any]:
    """Build Item v2 in memory from a legacy text/think/tool window."""

    messages = (window.get("text") or {}).get("messages", [])
    think_rounds = {
        int(value.get("round")): value
        for value in (window.get("think") or {}).get("rounds", [])
        if isinstance(value, dict) and str(value.get("round", "")).isdigit()
    }
    tool_rounds = {
        int(value.get("round")): value
        for value in (window.get("tool") or {}).get("rounds", [])
        if isinstance(value, dict) and str(value.get("round", "")).isdigit()
    }
    grouped: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw in messages if isinstance(messages, list) else []:
        if not isinstance(raw, dict):
            continue
        if raw.get("role") == "user" and current:
            grouped.append(current)
            current = []
        current.append(raw)
    if current:
        grouped.append(current)

    items: list[dict[str, Any]] = []
    for round_number, group in enumerate(grouped, start=1):
        user_messages = [value for value in group if value.get("role") == "user"]
        assistant_messages = [value for value in group if value.get("role") == "assistant"]
        other_messages = [
            value for value in group if value.get("role") not in {"user", "assistant"}
        ]
        for message in user_messages:
            items.append(
                _history_message_item(
                    "user", message.get("content"), round_number=round_number
                )
            )
        think = think_rounds.get(round_number) or {}
        reasoning = str(think.get("content") or "")
        if reasoning:
            items.append(
                {
                    "id": _item_id("rs"),
                    "type": "reasoning",
                    "status": "completed",
                    "content": reasoning,
                    "metadata": {"round": round_number, "history_source": "legacy"},
                    "extensions": {},
                }
            )
        records = (tool_rounds.get(round_number) or {}).get("calls", [])
        for position, record in enumerate(records if isinstance(records, list) else []):
            if not isinstance(record, dict):
                continue
            call_id = str(record.get("id") or f"history-{round_number}-{position}")
            name = str(record.get("name") or "unknown_tool")
            metadata = {
                "round": round_number,
                "iteration": int(record.get("iteration", 1)),
                "history_source": "legacy",
            }
            items.append(
                {
                    "id": _item_id("call"),
                    "type": "tool_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": name,
                    "arguments": record.get("arguments") or {},
                    "metadata": metadata,
                    "extensions": {},
                }
            )
            result = record.get("result")
            items.append(
                {
                    "id": _item_id("result"),
                    "type": "tool_result",
                    "status": "completed",
                    "call_id": call_id,
                    "name": name,
                    "is_error": bool(
                        isinstance(result, dict) and result.get("ok") is False
                    ),
                    "content": [{"type": "json", "data": result}],
                    "metadata": metadata,
                    "extensions": {},
                }
            )
        for message in [*assistant_messages, *other_messages]:
            role = str(message.get("role") or "assistant")
            if role not in {"user", "assistant"}:
                role = "assistant"
            items.append(
                _history_message_item(
                    role, message.get("content"), round_number=round_number
                )
            )
    return {"schema_version": ITEMS_SCHEMA_VERSION, "items": items}


def append_round_items(
    window: dict[str, Any],
    *,
    round_number: int,
    user_content: list[dict[str, Any]],
    reasoning: str,
    text: str,
    tool_records: list[dict[str, Any]],
    provider_responses: list[dict[str, Any]],
) -> None:
    """Append one committed round while preserving native Provider output Items."""

    container = window.setdefault(
        "items", {"schema_version": ITEMS_SCHEMA_VERSION, "items": []}
    )
    items = container.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        container["items"] = items
    items.append(
        {
            "id": _item_id("msg"),
            "type": "message",
            "status": "completed",
            "role": "user",
            "content": user_content,
            "metadata": {"round": round_number},
            "extensions": {},
        }
    )

    has_reasoning = False
    has_assistant = False
    for iteration, response in enumerate(provider_responses, start=1):
        response_id = str(response.get("id") or "")
        for raw in response.get("output", []) if isinstance(response, dict) else []:
            if not isinstance(raw, dict):
                continue
            item = json.loads(json.dumps(raw, ensure_ascii=False, default=str))
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            item["metadata"] = {
                **metadata,
                "round": round_number,
                "iteration": iteration,
                "response_id": response_id,
            }
            has_reasoning = has_reasoning or item.get("type") == "reasoning"
            has_assistant = has_assistant or (
                item.get("type") == "message" and item.get("role") == "assistant"
            )
            items.append(item)
        for record in tool_records:
            if not isinstance(record, dict) or int(record.get("iteration", 1)) != iteration:
                continue
            result = record.get("result")
            items.append(
                {
                    "id": _item_id("result"),
                    "type": "tool_result",
                    "status": "completed",
                    "call_id": str(record.get("id") or ""),
                    "name": str(record.get("name") or "unknown_tool"),
                    "is_error": bool(
                        isinstance(result, dict) and result.get("ok") is False
                    ),
                    "content": [{"type": "json", "data": result}],
                    "metadata": {
                        "round": round_number,
                        "iteration": iteration,
                        "tool_status": record.get("status"),
                    },
                    "extensions": {},
                }
            )

    if not provider_responses:
        if reasoning:
            items.append(
                {
                    "id": _item_id("rs"),
                    "type": "reasoning",
                    "status": "completed",
                    "content": reasoning,
                    "metadata": {"round": round_number},
                    "extensions": {},
                }
            )
        for record in tool_records:
            call_id = str(record.get("id") or _item_id("callid"))
            name = str(record.get("name") or "unknown_tool")
            metadata = {
                "round": round_number,
                "iteration": int(record.get("iteration", 1)),
            }
            items.extend(
                [
                    {
                        "id": _item_id("call"),
                        "type": "tool_call",
                        "status": "completed",
                        "call_id": call_id,
                        "name": name,
                        "arguments": record.get("arguments") or {},
                        "metadata": metadata,
                        "extensions": {},
                    },
                    {
                        "id": _item_id("result"),
                        "type": "tool_result",
                        "status": "completed",
                        "call_id": call_id,
                        "name": name,
                        "is_error": bool(
                            isinstance(record.get("result"), dict)
                            and record["result"].get("ok") is False
                        ),
                        "content": [{"type": "json", "data": record.get("result")}],
                        "metadata": metadata,
                        "extensions": {},
                    },
                ]
            )
    elif reasoning and not has_reasoning:
        items.append(
            {
                "id": _item_id("rs"),
                "type": "reasoning",
                "status": "completed",
                "content": reasoning,
                "metadata": {"round": round_number},
                "extensions": {},
            }
        )
    if text and not has_assistant:
        item = _history_message_item("assistant", text, round_number=round_number)
        item["metadata"]["history_source"] = "run_fallback"
        items.append(item)


def commit_window(directory: Path, window: dict[str, Any]) -> None:
    """Atomically commit an archive or mutable temp workspace.

    Archive data.json is restricted to durable conversation metadata. Temp
    commits may additionally persist context-management diagnostics.
    """

    with _lock(directory):
        data = dict(window["data"])
        is_runtime_workspace = directory.parent.name == "temp"
        if not is_runtime_workspace:
            data = {
                key: value for key, value in data.items() if key in _ARCHIVE_DATA_FIELDS
            }
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
        items = window.get("items")
        if not isinstance(items, dict) or not isinstance(items.get("items"), list):
            items = synthesize_items(window)
            window["items"] = items
        _atomic_write_json(directory / "items.json", items)
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
        items_path = directory / "items.json"
        if items_path.is_file():
            items = _read_json(items_path)
            if not isinstance(items, dict) or not isinstance(items.get("items"), list):
                raise HistoryError(f"items.json schema 无效：{directory}")
            window["items"] = items
        else:
            # v1 四文件历史在内存中无损升级；下一次成功提交时再双写 items.json。
            window["items"] = synthesize_items(window)
        return window


def runtime_window_path(archive_directory: Path) -> Path:
    """Return the temp workspace path used to build upstream API context."""

    return archive_directory.parent / "temp" / archive_directory.name


def _message_rounds(messages: Any) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw in messages if isinstance(messages, list) else []:
        if not isinstance(raw, dict):
            continue
        item = copy.deepcopy(raw)
        if item.get("role") == "user" and current:
            groups.append(current)
            current = []
        current.append(item)
    if current:
        groups.append(current)
    return groups


def _local_round(value: Any, removed: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number - removed if number > removed else None


def _trim_to_max_rounds(
    window: dict[str, Any], max_rounds: int
) -> dict[str, Any]:
    """Return a deep-copied temp workspace containing only the latest rounds.

    Temp round numbers are local and remain contiguous. ``context.round_offset``
    records how many archive rounds precede local round 1, allowing Engine to
    append the next local round without losing the archive's absolute numbering.
    """

    result = copy.deepcopy(window)
    if max_rounds <= 0:
        return result
    text = result.setdefault("text", {}).setdefault("messages", [])
    groups = _message_rounds(text)
    removed = max(0, len(groups) - max_rounds)
    if removed <= 0:
        return result

    result["text"]["messages"] = [
        message for group in groups[removed:] for message in group
    ]
    for section in ("think", "tool"):
        kept: list[dict[str, Any]] = []
        for raw in (result.get(section) or {}).get("rounds", []):
            if not isinstance(raw, dict):
                continue
            number = _local_round(raw.get("round"), removed)
            if number is None:
                continue
            item = copy.deepcopy(raw)
            item["round"] = number
            kept.append(item)
        result.setdefault(section, {})["rounds"] = kept

    kept_items: list[dict[str, Any]] = []
    for raw in (result.get("items") or {}).get("items", []):
        if not isinstance(raw, dict):
            continue
        metadata = raw.get("metadata")
        if not isinstance(metadata, dict):
            continue
        number = _local_round(metadata.get("round"), removed)
        if number is None:
            continue
        item = copy.deepcopy(raw)
        item["metadata"] = {**metadata, "round": number}
        kept_items.append(item)
    result.setdefault("items", {"schema_version": ITEMS_SCHEMA_VERSION})[
        "items"
    ] = kept_items

    data = result.setdefault("data", {})
    kept_metrics: list[dict[str, Any]] = []
    for raw in data.get("round_metrics", []):
        if not isinstance(raw, dict):
            continue
        number = _local_round(raw.get("round"), removed)
        if number is None:
            continue
        metric = copy.deepcopy(raw)
        metric["round"] = number
        kept_metrics.append(metric)
    data["round_metrics"] = kept_metrics
    data["rounds"] = len(groups) - removed
    context = data.get("context")
    if not isinstance(context, dict):
        context = {}
    try:
        previous_offset = int(context.get("round_offset", 0))
    except (TypeError, ValueError):
        previous_offset = 0
    data["context"] = {
        **context,
        "round_offset": max(0, previous_offset) + removed,
        "workspace_rounds": data["rounds"],
    }
    return result


def load_runtime_window(
    archive_directory: Path,
    archive_window: dict[str, Any] | None = None,
    *,
    max_rounds: int = 80,
) -> tuple[Path, dict[str, Any]]:
    """Load temp workspace; restore only recent rounds when it is unavailable."""

    runtime_directory = runtime_window_path(archive_directory)
    if (runtime_directory / "data.json").is_file():
        try:
            return runtime_directory, _trim_to_max_rounds(
                load_window(runtime_directory), max_rounds
            )
        except HistoryError:
            # A damaged temp workspace must never make the archive unusable.
            pass
    source = archive_window if archive_window is not None else load_window(archive_directory)
    restored = copy.deepcopy(source)
    restored.setdefault("data", {}).pop("context", None)
    return runtime_directory, _trim_to_max_rounds(restored, max_rounds)


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
        commit_window(runtime_window_path(directory), copy.deepcopy(window))
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
        runtime_directory = runtime_window_path(directory)
        runtime_data_path = runtime_directory / "data.json"
        if runtime_data_path.is_file():
            with _lock(runtime_directory):
                runtime_data = _read_json(runtime_data_path)
                if isinstance(runtime_data, dict) and runtime_data.get("complete") is True:
                    runtime_data["title"] = title
                    _atomic_write_json(runtime_data_path, runtime_data)
    return len(directories)


def delete_session(root: Path, user: str, source: str, session_id: str) -> int:
    """Delete every verified history window belonging to a session."""

    directories = _matching_windows(root, user, source, session_id)
    for directory in directories:
        with _lock(directory):
            shutil.rmtree(directory)
        runtime_directory = runtime_window_path(directory)
        if runtime_directory.is_dir():
            with _lock(runtime_directory):
                shutil.rmtree(runtime_directory)
    return len(directories)


def delete_all_sessions(root: Path, user: str, source: str) -> tuple[int, int]:
    """Delete every verified history window for a user/source pair."""

    entries = _source_windows(root, user, source)
    session_ids = {session_id for _, session_id in entries}
    for directory, _ in entries:
        with _lock(directory):
            shutil.rmtree(directory)
        runtime_directory = runtime_window_path(directory)
        if runtime_directory.is_dir():
            with _lock(runtime_directory):
                shutil.rmtree(runtime_directory)
    return len(session_ids), len(entries)


def clear_session(root: Path, user: str, source: str, session_id: str) -> Path:
    existing = find_window(root, user, source, session_id)
    directory = existing or user_dir(user, root) / "history" / _window_name()
    window = empty_window(user, source, session_id)
    commit_window(directory, window)
    commit_window(runtime_window_path(directory), copy.deepcopy(window))
    return directory


def session_messages(root: Path, user: str, source: str, session_id: str) -> list[dict[str, Any]]:
    directory = find_window(root, user, source, session_id)
    if directory is None:
        return []
    window = load_window(directory)
    return [dict(message) for message in window["text"].get("messages", []) if isinstance(message, dict)]
