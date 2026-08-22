"""会话窗口双层 SQLite 存储合约。

archive 窗口保存用户可见的完整对话，不受轮次上限影响，也不允许上下文整理
裁剪。runtime 窗口是上游 Provider 使用的可变上下文窗口，受
agents.max_rounds 限制，允许保存压缩统计和局部轮号偏移。二者都位于 SQLite
表中；Path 只作为稳定的逻辑窗口标识。

两层都保留 text/think/tool/items/data 五个逻辑分区，但由 SQLite 事务原子提交。
归档表是用户可见历史的权威来源，运行时表只保存可裁剪的 Provider 工作区。
"""

from __future__ import annotations

import copy
import json
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from run.history.index import (
    build_window_record,
    find_record as find_index_record,
    list_records as list_index_records,
    list_records_page as list_index_records_page,
    new_conversation_id,
    remove_all_sessions as remove_all_index_sessions,
    remove_session as remove_index_session,
    update_title as update_index_title,
    upsert_window as upsert_index_window,
)
from run.history.store import (
    delete_session_windows,
    delete_source_windows,
    delete_window as delete_stored_window,
    find_window_name,
    list_windows as list_stored_windows,
    load_window as load_stored_window,
    patch_window_data,
    rename_windows,
    save_window,
    save_window_bundle,
    window_exists,
)
from run.config import user_dir


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
        "memory_processed_round",
        "memory_status",
        "memory_error",
        "memory_last_error",
        "memory_queue_reason",
        "memory_target_round",
        "memory_queued_at",
        "complete",
    }
)
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SUMMARY_UNCHANGED = object()


class HistoryError(RuntimeError):
    """Conversation history cannot be read or committed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_name(session_id: str = "") -> str:
    # Window identifiers remain opaque even when callers provide a custom
    # session identifier.
    value = str(session_id or "")
    if value.startswith("conv_") and value.replace("_", "").isalnum():
        return value
    return new_conversation_id()


def _lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


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
                "provider_request_count": 0,
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
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blocks = content if isinstance(content, list) else _text_content(content)
    item: dict[str, Any] = {
        "id": _item_id("msg"),
        "type": "message",
        "status": "completed",
        "role": role,
        "content": blocks,
        "metadata": {
            "round": round_number,
            "history_source": "partition_fallback",
            **copy.deepcopy(metadata or {}),
        },
        "extensions": {},
    }
    if role == "assistant":
        item["phase"] = "final_answer"
    return item


def synthesize_items(window: dict[str, Any]) -> dict[str, Any]:
    """Build Item v2 when a current window has no canonical items partition."""

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
        assistant_messages = [
            value for value in group if value.get("role") == "assistant"
        ]
        other_messages = [
            value for value in group if value.get("role") not in {"user", "assistant"}
        ]
        for message in user_messages:
            input_attachments = message.get("attachments")
            items.append(
                _history_message_item(
                    "user",
                    message.get("content"),
                    round_number=round_number,
                    metadata={"input_attachments": input_attachments}
                    if isinstance(input_attachments, list) and input_attachments
                    else None,
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
                    "metadata": {
                        "round": round_number,
                        "history_source": "partition_fallback",
                    },
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
                "history_source": "partition_fallback",
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
    user_metadata: dict[str, Any] | None = None,
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
            "metadata": {"round": round_number, **copy.deepcopy(user_metadata or {})},
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
            if (
                not isinstance(record, dict)
                or int(record.get("iteration", 1)) != iteration
            ):
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


def commit_window(
    directory: Path,
    window: dict[str, Any],
    *,
    summary_cache: dict[str, Any] | None | object = _SUMMARY_UNCHANGED,
) -> None:
    """Atomically commit an archive or mutable temp workspace.

    Archive metadata is restricted to durable conversation metadata. Temp
    commits may additionally persist context-management diagnostics.
    """

    with _lock(directory):
        data = dict(window["data"])
        is_runtime_workspace = directory.parent.name == "temp"
        if not is_runtime_workspace:
            data = {
                key: value for key, value in data.items() if key in _ARCHIVE_DATA_FIELDS
            }
        data["updated_at"] = _now()
        items = window.get("items")
        if not isinstance(items, dict) or not isinstance(items.get("items"), list):
            items = synthesize_items(window)
            window["items"] = items
        data["complete"] = True
        stored_window = {
                "text": window["text"],
                "think": window["think"],
                "tool": window["tool"],
                "items": items,
                "data": data,
            }
        stored_data = (
            save_window(directory, stored_window)
            if summary_cache is _SUMMARY_UNCHANGED
            else save_window(
                directory,
                stored_window,
                summary_cache=(
                    summary_cache if isinstance(summary_cache, dict) else None
                ),
            )
        )
        current_data = window.get("data")
        if isinstance(current_data, dict):
            current_data.clear()
            current_data.update(stored_data)
        else:
            window["data"] = stored_data
        if (
            directory.parent.name == "history"
            and directory.parent.parent.parent.name == "users"
        ):
            try:
                root = directory.parents[3]
                upsert_index_window(
                    root,
                    str(stored_data.get("user") or directory.parent.parent.name),
                    str(stored_data.get("source") or ""),
                    str(stored_data.get("session_id") or ""),
                    directory,
                    stored_data,
                )
            except Exception:
                # The committed window is authoritative. A missing registry
                # can be rebuilt from the SQLite window table on the next read.
                pass


def commit_terminal_windows(
    archive_directory: Path,
    archive_window: dict[str, Any],
    runtime_directory: Path,
    runtime_window: dict[str, Any],
    *,
    summary_cache: dict[str, Any] | None = None,
    run_state: str = "idle",
    active_key: str | None = None,
) -> None:
    """Commit both terminal windows and the session row in one transaction."""

    directories = sorted(
        (archive_directory, runtime_directory), key=lambda value: str(value.resolve())
    )
    first_lock = _lock(directories[0])
    second_lock = _lock(directories[1])
    with first_lock:
        with second_lock:
            timestamp = _now()

            archive_data = {
                key: value
                for key, value in dict(archive_window["data"]).items()
                if key in _ARCHIVE_DATA_FIELDS
            }
            archive_data["updated_at"] = timestamp
            archive_data["complete"] = True
            archive_items = archive_window.get("items")
            if not isinstance(archive_items, dict) or not isinstance(
                archive_items.get("items"), list
            ):
                archive_items = synthesize_items(archive_window)
                archive_window["items"] = archive_items
            stored_archive = {
                "text": archive_window["text"],
                "think": archive_window["think"],
                "tool": archive_window["tool"],
                "items": archive_items,
                "data": archive_data,
            }

            runtime_data = dict(runtime_window["data"])
            runtime_data["updated_at"] = timestamp
            runtime_data["complete"] = True
            runtime_items = runtime_window.get("items")
            if not isinstance(runtime_items, dict) or not isinstance(
                runtime_items.get("items"), list
            ):
                runtime_items = synthesize_items(runtime_window)
                runtime_window["items"] = runtime_items
            stored_runtime = {
                "text": runtime_window["text"],
                "think": runtime_window["think"],
                "tool": runtime_window["tool"],
                "items": runtime_items,
                "data": runtime_data,
            }

            source = str(archive_data.get("source") or "")
            session_id = str(archive_data.get("session_id") or "")
            user = str(archive_data.get("user") or archive_directory.parent.parent.name)
            root = archive_directory.parents[3]
            previous = find_index_record(root, user, source, session_id)
            record = build_window_record(
                source=source,
                session_id=session_id,
                directory=archive_directory,
                data=archive_data,
                previous=previous,
                run_state=run_state,
            )
            active_updates = (
                {active_key: {"source": source, "session_id": session_id}}
                if isinstance(active_key, str) and active_key.strip()
                else None
            )
            stored_data = save_window_bundle(
                [
                    (archive_directory, stored_archive, _SUMMARY_UNCHANGED),
                    (runtime_directory, stored_runtime, summary_cache),
                ],
                session_record=record,
                active_updates=active_updates,
                updated_at=timestamp,
            )
            for target, data in (
                (archive_window, stored_data[0]),
                (runtime_window, stored_data[1]),
            ):
                current = target.get("data")
                if isinstance(current, dict):
                    current.clear()
                    current.update(data)
                else:
                    target["data"] = data


def patch_archive_metadata(
    directory: Path,
    window: dict[str, Any],
    *,
    updates: dict[str, Any],
    removals: tuple[str, ...] = (),
    run_state: str | None = None,
) -> dict[str, Any]:
    """Persist a small archive metadata transition without rewriting messages."""

    with _lock(directory):
        current = window.setdefault("data", {})
        if not isinstance(current, dict):
            raise HistoryError("历史窗口 data 分区无效")
        data = {
            key: copy.deepcopy(value)
            for key, value in current.items()
            if key in _ARCHIVE_DATA_FIELDS
        }
        for key, value in updates.items():
            if key in _ARCHIVE_DATA_FIELDS:
                data[key] = copy.deepcopy(value)
        for key in removals:
            data.pop(key, None)
        updated_at = _now()
        metadata_updates = {
            key: copy.deepcopy(value)
            for key, value in updates.items()
            if key in _ARCHIVE_DATA_FIELDS
        }
        metadata_updates.update({"updated_at": updated_at, "complete": True})
        metadata_removals = tuple(
            key for key in removals if key in _ARCHIVE_DATA_FIELDS
        )

        def build_record(
            merged: dict[str, Any], previous: dict[str, Any] | None
        ) -> dict[str, Any]:
            source = str(merged.get("source") or "")
            session_id = str(merged.get("session_id") or "")
            return build_window_record(
                source=source,
                session_id=session_id,
                directory=directory,
                data=merged,
                previous=previous,
                run_state=run_state,
            )

        stored = patch_window_data(
            directory,
            data,
            merge_updates=metadata_updates,
            merge_removals=metadata_removals,
            session_record_factory=build_record,
            updated_at=updated_at,
        )

        # The metadata transaction intentionally does not rebuild transcript
        # partitions.  Reload the current data so callers do not replace a
        # newer in-memory round with the stale snapshot they passed in.
        fresh = load_stored_window(directory)
        if isinstance(fresh, dict) and isinstance(fresh.get("data"), dict):
            stored = fresh["data"]
        current.clear()
        current.update(stored)
        return stored


def load_window(directory: Path) -> dict[str, Any]:
    with _lock(directory):
        window = load_stored_window(directory)
        if window is None:
            raise HistoryError(f"历史窗口不存在：{directory}")
        data = window.get("data")
        if not isinstance(data, dict) or not data.get("complete"):
            raise HistoryError(f"历史窗口尚未完成提交：{directory}")
        if not isinstance(window["text"], dict) or not isinstance(
            window["text"].get("messages"), list
        ):
            raise HistoryError(f"历史 text 分区 schema 无效：{directory}")
        items = window.get("items")
        messages = (window.get("text") or {}).get("messages")
        if (
            not isinstance(items, dict)
            or not isinstance(items.get("items"), list)
            or (not items.get("items") and isinstance(messages, list) and messages)
        ):
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


def _usage_from_round_metrics(metrics: Any) -> dict[str, Any]:
    """Rebuild durable session usage after a committed round is removed."""

    result: dict[str, Any] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "estimated": False,
    }
    additive = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "provider_request_count",
        "cached_prompt_tokens",
        "cached_input_tokens",
        "cache_miss_tokens",
        "reasoning_tokens",
        "visible_output_tokens",
    )
    for metric in metrics if isinstance(metrics, list) else []:
        usage = metric.get("usage") if isinstance(metric, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in additive:
            try:
                result[key] = int(result.get(key, 0)) + max(0, int(usage.get(key, 0)))
            except (TypeError, ValueError):
                continue
        result["estimated"] = bool(result["estimated"] or usage.get("estimated"))
        stages = usage.get("stages")
        if isinstance(stages, list):
            result.setdefault("stages", []).extend(copy.deepcopy(stages))
        provider_raw = usage.get("provider_raw")
        if isinstance(provider_raw, list):
            result.setdefault("provider_raw", []).extend(copy.deepcopy(provider_raw))
        elif isinstance(provider_raw, dict) and provider_raw:
            result.setdefault("provider_raw", []).append(copy.deepcopy(provider_raw))
        media = usage.get("media")
        if isinstance(media, dict):
            target_media = result.setdefault("media", {})
            for key, value in media.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    target_media[key] = target_media.get(key, 0) + value
        measurement = usage.get("measurement")
        if isinstance(measurement, dict):
            result["measurement"] = copy.deepcopy(measurement)
    result["input_tokens"] = result["prompt_tokens"]
    result["output_tokens"] = result["completion_tokens"]
    cached = int(result.get("cached_prompt_tokens", 0))
    missed = int(result.get("cache_miss_tokens", 0))
    if cached or missed:
        result["cache_hit_rate"] = round(cached / (cached + missed), 6)
    return result


def _remove_last_round(window: dict[str, Any], round_number: int) -> dict[str, Any]:
    result = copy.deepcopy(window)
    message_groups = _message_rounds((result.get("text") or {}).get("messages"))
    if not message_groups or not any(
        item.get("role") == "user" for item in message_groups[-1]
    ):
        raise HistoryError("最后一轮历史缺少用户消息，无法撤销")
    result.setdefault("text", {})["messages"] = [
        message for group in message_groups[:-1] for message in group
    ]
    for section in ("think", "tool"):
        rounds = (result.get(section) or {}).get("rounds", [])
        result.setdefault(section, {})["rounds"] = [
            item
            for item in rounds
            if isinstance(rounds, list)
            and isinstance(item, dict)
            and item.get("round") != round_number
        ]
    raw_items = (result.get("items") or {}).get("items", [])
    result.setdefault("items", {"schema_version": ITEMS_SCHEMA_VERSION})["items"] = [
        item
        for item in raw_items
        if isinstance(raw_items, list)
        and isinstance(item, dict)
        and not (
            isinstance(item.get("metadata"), dict)
            and item["metadata"].get("round") == round_number
        )
    ]
    data = result.setdefault("data", {})
    metrics = data.get("round_metrics", [])
    kept_metrics = [
        item
        for item in metrics
        if isinstance(metrics, list)
        and isinstance(item, dict)
        and item.get("round") != round_number
    ]
    data["round_metrics"] = kept_metrics
    data["rounds"] = max(0, round_number - 1)
    data["token_usage"] = _usage_from_round_metrics(kept_metrics)
    if data.get("memory_processed_round") is not None:
        data["memory_processed_round"] = min(
            max(0, int(data.get("memory_processed_round") or 0)),
            data["rounds"],
        )
        data["memory_status"] = (
            "completed"
            if data["memory_processed_round"] >= data["rounds"]
            else "pending"
        )
        data.pop("memory_error", None)
    data.pop("context", None)
    return result


def undo_last_round(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    expected_round: int,
    expected_prompt: str,
) -> dict[str, Any]:
    """Undo one exact committed round in archive and runtime history.

    ``expected_round == current + 1`` represents a frontend-only interrupted
    round and is intentionally a no-op. Other mismatches are rejected so a
    stale browser cannot remove an unrelated successful round.
    """

    directory = find_window(root, user, source, session_id)
    if directory is None:
        if expected_round == 1:
            return {
                "found": False,
                "rolled_back": False,
                "round": expected_round,
                "remaining_rounds": 0,
                "prompt": expected_prompt,
                "content": [],
            }
        raise HistoryError("会话轮次已发生变化，请刷新后重试")
    runtime_directory = runtime_window_path(directory)
    with _lock(directory), _lock(runtime_directory):
        archive_original = load_window(directory)
        current_round = int((archive_original.get("data") or {}).get("rounds", 0))
        if expected_round == current_round + 1:
            return {
                "found": True,
                "rolled_back": False,
                "round": expected_round,
                "remaining_rounds": current_round,
                "prompt": expected_prompt,
                "content": [],
            }
        if expected_round != current_round or current_round < 1:
            raise HistoryError("会话轮次已发生变化，请刷新后重试")

        groups = _message_rounds((archive_original.get("text") or {}).get("messages"))
        last_group = groups[-1] if groups else []
        user_message = next(
            (item for item in last_group if item.get("role") == "user"), None
        )
        prompt = str((user_message or {}).get("content") or "")
        if prompt.strip() != expected_prompt.strip():
            raise HistoryError("最后一轮消息与重发目标不一致，请刷新后重试")

        content: list[dict[str, Any]] = []
        for item in reversed((archive_original.get("items") or {}).get("items", [])):
            metadata = item.get("metadata") if isinstance(item, dict) else None
            if (
                isinstance(metadata, dict)
                and metadata.get("round") == current_round
                and item.get("type") == "message"
                and item.get("role") == "user"
                and isinstance(item.get("content"), list)
            ):
                content = copy.deepcopy(item["content"])
                break

        archive_next = _remove_last_round(archive_original, current_round)
        runtime_original: dict[str, Any] | None = None
        if window_exists(runtime_directory):
            try:
                runtime_original = load_window(runtime_directory)
            except HistoryError:
                runtime_original = None
        if runtime_original is not None:
            local_round = int((runtime_original.get("data") or {}).get("rounds", 0))
            context = (runtime_original.get("data") or {}).get("context") or {}
            try:
                offset = max(0, int(context.get("round_offset", 0)))
            except (TypeError, ValueError):
                offset = 0
            runtime_next = (
                _remove_last_round(runtime_original, local_round)
                if local_round > 0 and offset + local_round == current_round
                else copy.deepcopy(archive_next)
            )
        else:
            runtime_next = copy.deepcopy(archive_next)
        runtime_next.setdefault("data", {}).pop("context", None)

        try:
            commit_window(directory, archive_next)
            commit_window(runtime_directory, runtime_next)
        except BaseException:
            try:
                commit_window(directory, archive_original)
                if runtime_original is not None:
                    commit_window(runtime_directory, runtime_original)
                else:
                    delete_stored_window(runtime_directory)
            except BaseException:
                pass
            raise
        return {
            "found": True,
            "rolled_back": True,
            "round": current_round,
            "remaining_rounds": current_round - 1,
            "prompt": prompt,
            "content": content,
        }


def _local_round(value: Any, removed: int) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number - removed if number > removed else None


def _trim_to_max_rounds(window: dict[str, Any], max_rounds: int) -> dict[str, Any]:
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
    result.setdefault("items", {"schema_version": ITEMS_SCHEMA_VERSION})["items"] = (
        kept_items
    )

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
    if window_exists(runtime_directory):
        try:
            return runtime_directory, _trim_to_max_rounds(
                load_window(runtime_directory), max_rounds
            )
        except HistoryError:
            # A damaged temp workspace must never make the archive unusable.
            pass
    source = (
        archive_window if archive_window is not None else load_window(archive_directory)
    )
    restored = copy.deepcopy(source)
    restored.setdefault("data", {}).pop("context", None)
    return runtime_directory, _trim_to_max_rounds(restored, max_rounds)


def find_window(root: Path, user: str, source: str, session_id: str) -> Path | None:
    history_dir = user_dir(user, root) / "history"
    # The registry normally knows the exact logical window identifier.
    indexed = find_index_record(root, user, source, session_id)
    archive_window = str((indexed or {}).get("archive_window") or "")
    if archive_window and Path(archive_window).name == archive_window:
        indexed_directory = history_dir / archive_window
        if window_exists(indexed_directory):
            return indexed_directory
    stored_name = find_window_name(root, user, source, session_id)
    return history_dir / stored_name if stored_name else None


def queue_memory_extraction(
    root: Path,
    user: str,
    source: str,
    session_id: str,
    *,
    target_round: int | None = None,
    reason: str = "session_closed",
) -> dict[str, Any]:
    """Durably queue a bounded set of committed rounds for memory extraction."""

    directory = find_window(root, user, source, session_id)
    if directory is None:
        return {
            "status": "skipped",
            "reason": "no_archive",
            "rounds": 0,
            "processed_round": 0,
        }
    window = load_window(directory)
    data = window.setdefault("data", {})
    rounds = max(0, int(data.get("rounds") or 0))
    processed_round = max(0, int(data.get("memory_processed_round") or 0))
    requested_target = rounds if target_round is None else max(0, int(target_round))
    requested_target = min(rounds, requested_target)
    existing_target = max(0, int(data.get("memory_target_round") or 0))
    bounded_target = max(requested_target, existing_target)
    if rounds < 1 or processed_round >= bounded_target:
        return {
            "status": "skipped",
            "reason": (
                "already_processed"
                if reason == "manual_compression"
                else "no_pending_rounds"
            ),
            "rounds": rounds,
            "processed_round": processed_round,
            "target_round": bounded_target,
            "pending_rounds": 0,
        }
    from run.config import load_config
    from run.memory import memory_extraction_mode

    if memory_extraction_mode(load_config(user, root)) == "disabled":
        return {
            "status": "skipped",
            "reason": "memory_extraction_disabled",
            "rounds": rounds,
            "processed_round": processed_round,
            "target_round": bounded_target,
            "pending_rounds": 0,
        }
    data["memory_status"] = "queued"
    data["memory_queue_reason"] = str(reason or "session_closed")
    data["memory_target_round"] = bounded_target
    data["memory_queued_at"] = _now()
    data.pop("memory_error", None)
    patch_archive_metadata(
        directory,
        window,
        updates={
            "memory_status": "queued",
            "memory_queue_reason": data["memory_queue_reason"],
            "memory_target_round": bounded_target,
            "memory_queued_at": data["memory_queued_at"],
        },
        removals=("memory_error",),
    )
    indexed = find_index_record(root, user, source, session_id)
    if (
        not isinstance(indexed, dict)
        or str(indexed.get("memory_status") or "") not in {"queued", "processing"}
        or int(indexed.get("memory_target_round") or 0) != bounded_target
        or int(indexed.get("memory_processed_round") or 0) != processed_round
    ):
        raise HistoryError("记忆后台队列未能同步写入历史索引")
    return {
        "status": "queued",
        "reason": data["memory_queue_reason"],
        "rounds": rounds,
        "processed_round": processed_round,
        "target_round": bounded_target,
        "pending_rounds": bounded_target - processed_round,
    }


def prepare_window(
    root: Path, user: str, source: str, session_id: str
) -> tuple[Path, dict[str, Any], bool]:
    """Load a committed window or prepare a new in-memory window.

    A new directory is not written until ``commit_window`` succeeds after the
    provider response.  This prevents failed requests from creating empty
    conversation windows.
    """

    existing = find_window(root, user, source, session_id)
    if existing is not None:
        return existing, load_window(existing), False
    history_dir = user_dir(user, root) / "history"
    return (
        history_dir / _window_name(session_id),
        empty_window(user, source, session_id),
        True,
    )


def get_or_create_window(
    root: Path, user: str, source: str, session_id: str
) -> tuple[Path, dict[str, Any]]:
    directory, window, is_new = prepare_window(root, user, source, session_id)
    if is_new:
        commit_window(directory, window)
        commit_window(runtime_window_path(directory), copy.deepcopy(window))
    return directory, window


def _session_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": str(record.get("source") or ""),
        "bound_platform": str(record.get("bound_platform") or ""),
        "session_id": str(record.get("session_id") or ""),
        "conversation_id": str(record.get("conversation_id") or ""),
        "window": str(record.get("archive_window") or ""),
        "title": str(record.get("title") or ""),
        "summary": str(record.get("summary") or ""),
        "summary_status": str(record.get("summary_status") or "none"),
        "summary_target_round": int(record.get("summary_target_round") or 0),
        "summary_completed_round": int(record.get("summary_completed_round") or 0),
        "summary_retry_at": str(record.get("summary_retry_at") or ""),
        "summary_retry_count": max(0, int(record.get("summary_retry_count") or 0)),
        "summary_attempt_count": max(0, int(record.get("summary_attempt_count") or 0)),
        "summary_consecutive_failures": max(
            0, int(record.get("summary_consecutive_failures") or 0)
        ),
        "summary_max_attempts": max(1, int(record.get("summary_max_attempts") or 5)),
        "summary_last_attempt_at": str(record.get("summary_last_attempt_at") or ""),
        "summary_recovered_at": str(record.get("summary_recovered_at") or ""),
        "summary_last_error": copy.deepcopy(
            record.get("summary_last_error")
            if isinstance(record.get("summary_last_error"), dict)
            else None
        ),
        "summary_checkpoint_next_chunk": max(
            0, int(record.get("summary_checkpoint_next_chunk") or 0)
        ),
        "summary_checkpoint_total_chunks": max(
            0, int(record.get("summary_checkpoint_total_chunks") or 0)
        ),
        "state": str(record.get("lifecycle") or "open"),
        "run_state": str(record.get("run_state") or "idle"),
        "chain": str(record.get("chain") or ""),
        "memory_status": str(record.get("memory_status") or "unknown"),
        "memory_processed_round": max(
            0, int(record.get("memory_processed_round") or 0)
        ),
        "memory_target_round": max(0, int(record.get("memory_target_round") or 0)),
        "memory_queue_reason": str(record.get("memory_queue_reason") or ""),
        "memory_queued_at": str(record.get("memory_queued_at") or ""),
        "memory_last_error": copy.deepcopy(
            record.get("memory_last_error")
            if isinstance(record.get("memory_last_error"), dict)
            else None
        ),
        "rounds": int(record.get("rounds") or 0),
        "updated_at": str(record.get("updated_at") or ""),
    }


def list_sessions(
    root: Path,
    user: str,
    source: str | None,
    *,
    query: str = "",
) -> list[dict[str, Any]]:
    return [
        _session_payload(record)
        for record in list_index_records(root, user, source=source, query=query)
    ]


def list_sessions_page(
    root: Path,
    user: str,
    source: str | None,
    *,
    query: str = "",
    limit: int = 50,
    before_updated_at: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    records, has_more = list_index_records_page(
        root,
        user,
        source=source,
        query=query,
        limit=limit,
        before_updated_at=before_updated_at,
    )
    return [_session_payload(record) for record in records], has_more


def _source_windows(root: Path, user: str, source: str) -> list[tuple[Path, str]]:
    """Return committed logical windows for one user/source pair."""

    history_dir = user_dir(user, root) / "history"
    return [
        (history_dir / str(item["window_name"]), str(item["session_id"]))
        for item in list_stored_windows(root, user, source=source)
        if str(item.get("session_id") or "")
    ]


def _matching_windows(
    root: Path, user: str, source: str, session_id: str
) -> list[Path]:
    """Return only committed windows whose stored identity matches exactly."""

    return [
        directory
        for directory, stored_session_id in _source_windows(root, user, source)
        if stored_session_id == session_id
    ]


def rename_session(
    root: Path, user: str, source: str, session_id: str, title: str
) -> int:
    """Persist a display title without changing chronological ordering."""

    changed_windows = rename_windows(root, user, source, session_id, title)
    indexed = update_index_title(root, user, source, session_id, title)
    return max(changed_windows, 1 if indexed is not None else 0)


def _remove_window_cache(directory: Path) -> None:
    for candidate in (runtime_window_path(directory), directory):
        if candidate.is_dir():
            with _lock(candidate):
                shutil.rmtree(candidate)


def delete_session(root: Path, user: str, source: str, session_id: str) -> int:
    """Delete every verified history window belonging to a session."""

    indexed = find_index_record(root, user, source, session_id)
    directories = _matching_windows(root, user, source, session_id)
    deleted_windows = delete_session_windows(root, user, source, session_id)
    for directory in directories:
        _remove_window_cache(directory)
    remove_index_session(root, user, source, session_id)
    return max(deleted_windows, 1 if indexed is not None else 0)


def delete_all_sessions(root: Path, user: str, source: str) -> tuple[int, int]:
    """Delete every verified history window for a user/source pair."""

    directories = [directory for directory, _ in _source_windows(root, user, source)]
    deleted_sessions, deleted_windows = delete_source_windows(root, user, source)
    for directory in directories:
        _remove_window_cache(directory)
    removed_index_sessions = remove_all_index_sessions(root, user, source)
    return max(deleted_sessions, removed_index_sessions), deleted_windows


def clear_session(root: Path, user: str, source: str, session_id: str) -> Path:
    existing = find_window(root, user, source, session_id)
    directory = existing or user_dir(user, root) / "history" / _window_name(session_id)
    window = empty_window(user, source, session_id)
    commit_window(directory, window)
    commit_window(runtime_window_path(directory), copy.deepcopy(window))
    return directory


def session_messages(
    root: Path, user: str, source: str, session_id: str
) -> list[dict[str, Any]]:
    directory = find_window(root, user, source, session_id)
    if directory is None:
        return []
    window = load_window(directory)
    return [
        dict(message)
        for message in window["text"].get("messages", [])
        if isinstance(message, dict)
    ]


_DOMAIN_MODULES = ("index", "store", "summary_scheduler")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.history.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
