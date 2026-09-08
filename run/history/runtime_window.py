"""Runtime-window loading, trimming, and undo operations."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from run.history.commit_ops import commit_window
from run.history.history_models import HistoryError, ITEMS_SCHEMA_VERSION, _lock, synthesize_items
from run.history.store import delete_window as delete_stored_window
from run.history.store import load_window as load_stored_window
from run.history.store import window_exists


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

    from run.history.session_api import find_window

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
    from run.history.session_api import find_window
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
