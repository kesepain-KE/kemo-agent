"""Round append, terminal commit, and archive metadata operations."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from run.history.history_models import (
    HistoryError,
    ITEMS_SCHEMA_VERSION,
    _ARCHIVE_DATA_FIELDS,
    _SUMMARY_UNCHANGED,
    _history_message_item,
    _history_tool_call_item,
    _is_corrupted_provider_item,
    _item_id,
    _lock,
    _now,
    synthesize_items,
)
from run.history.index import (
    build_window_record,
    find_record as find_index_record,
    upsert_window as upsert_index_window,
)
from run.history.store import (
    load_window as load_stored_window,
    patch_window_data,
    save_window,
    save_window_bundle,
)


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
    matched_record_indices: set[int] = set()
    for response_index, response in enumerate(provider_responses, start=1):
        raw_iteration = response.get("_iteration") if isinstance(response, dict) else None
        try:
            iteration = int(raw_iteration) if raw_iteration is not None else response_index
        except (TypeError, ValueError):
            iteration = response_index
        response_id = str(response.get("id") or "")
        iteration_call_ids: set[str] = set()
        for raw in response.get("output", []) if isinstance(response, dict) else []:
            if not isinstance(raw, dict):
                continue
            item = json.loads(json.dumps(raw, ensure_ascii=False, default=str))
            if _is_corrupted_provider_item(item):
                continue
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
            if item.get("type") == "tool_call":
                iteration_call_ids.add(str(item.get("call_id") or ""))
            items.append(item)
        for record_index, record in enumerate(tool_records):
            if (
                not isinstance(record, dict)
                or int(record.get("iteration", 1)) != iteration
            ):
                continue
            matched_record_indices.add(record_index)
            call_id = str(record.get("id") or _item_id("callid"))
            if call_id and call_id not in iteration_call_ids:
                items.append(
                    _history_tool_call_item(
                        {**record, "id": call_id},
                        round_number=round_number,
                        iteration=iteration,
                        response_id=response_id,
                    )
                )
                iteration_call_ids.add(call_id)
            result = record.get("result")
            items.append(
                {
                    "id": _item_id("result"),
                    "type": "tool_result",
                    "status": "completed",
                    "call_id": call_id,
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

    if provider_responses:
        # Custom/legacy Providers may not expose a durable native response for
        # every iteration.  Preserve unmatched execution records instead of
        # silently dropping them just because a recovered response exists.
        for record_index, record in enumerate(tool_records):
            if record_index in matched_record_indices or not isinstance(record, dict):
                continue
            call_id = str(record.get("id") or _item_id("callid"))
            name = str(record.get("name") or "unknown_tool")
            iteration = int(record.get("iteration", 1))
            metadata = {
                "round": round_number,
                "iteration": iteration,
                "history_source": "tool_record_pair_repair",
            }
            items.extend(
                [
                    {
                        "id": _item_id("call"),
                        "type": "tool_call",
                        "status": "completed",
                        "call_id": call_id,
                        "name": name,
                        "arguments": copy.deepcopy(record.get("arguments") or {}),
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
                conditional_active_updates=active_updates,
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
