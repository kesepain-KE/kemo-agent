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
        "session_generation",
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
_DIAGNOSTIC_TRUNCATION_MARKER = "诊断内容已截断"


class HistoryError(RuntimeError):
    """Conversation history cannot be read or committed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _window_name(session_id: str = "") -> str:
    # The physical window key is deliberately independent from the caller's
    # logical session id.  ``history_windows`` and its partition tables are
    # keyed by this name, while source/session ownership is stored separately;
    # reusing a caller-provided ``conv_*`` id could therefore make two sources
    # overwrite one another when they happen to choose the same session id.
    return new_conversation_id()


def _lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def empty_window(
    user: str,
    source: str,
    session_id: str,
    *,
    session_generation: str = "",
) -> dict[str, Any]:
    timestamp = _now()
    # A generation is authoritative only when the session reservation supplied
    # one.  Creating an unrelated in-memory window must not mint a value that
    # looks like an old session generation; otherwise a normal metadata write
    # would be indistinguishable from a stale writer after a session is reused.
    generation = str(session_generation or "").strip()
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
            "session_generation": generation,
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


def _is_corrupted_provider_item(item: dict[str, Any]) -> bool:
    kind = str(item.get("type") or "")
    if kind not in {"message", "reasoning", "tool_call", "tool_result"}:
        return True
    if kind == "tool_call":
        for key in ("call_id", "name"):
            value = str(item.get(key) or "")
            if not value or _DIAGNOSTIC_TRUNCATION_MARKER in value:
                return True
    return False


def _history_tool_call_item(
    record: dict[str, Any],
    *,
    round_number: int,
    iteration: int,
    response_id: str = "",
) -> dict[str, Any]:
    metadata = {
        "round": round_number,
        "iteration": iteration,
        "history_source": "tool_record_pair_repair",
    }
    if response_id:
        metadata["response_id"] = response_id
    return {
        "id": _item_id("call"),
        "type": "tool_call",
        "status": "completed",
        "call_id": str(record.get("id") or _item_id("callid")),
        "name": str(record.get("name") or "unknown_tool"),
        "arguments": copy.deepcopy(record.get("arguments") or {}),
        "metadata": metadata,
        "extensions": {},
    }


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
