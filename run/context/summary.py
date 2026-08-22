"""用于删除历史回合的派生原子上下文摘要缓存。"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from events import RunEvent
from run.agents import AgentRunner
from run.context import RoundGroup, estimate_text_tokens
from run.history import (
    read_context_summary,
    write_context_summary,
)


SUMMARY_SCHEMA_VERSION = 3
SUMMARY_STORE_REF = "history.sqlite3#history_context_summaries"
SUMMARY_CHUNK_TOKEN_BUDGET = 64_000
SUMMARY_MAX_OUTPUT_TOKENS = 20_000
SUMMARY_KEYS = (
    "facts",
    "requirements",
    "decisions",
    "unfinished",
    "tool_results",
    "entities",
    "narrative",
)


class SummaryError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _source_round(group: RoundGroup) -> dict[str, Any]:
    return {
        "round": group.number,
        "messages": group.raw_text_messages,
        "reasoning": (
            copy.deepcopy(group.think)
            if isinstance(group.think, dict)
            else None
        ),
        "tools": (group.tool or {}).get("calls", []),
    }


def summary_source(
    groups: list[RoundGroup], *, round_offset: int = 0
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for group in groups:
        item = _source_round(group)
        item["round"] = max(0, int(round_offset)) + group.number
        result.append(item)
    return result


def source_hash(groups: list[RoundGroup], *, round_offset: int = 0) -> str:
    payload = _stable_json(
        summary_source(groups, round_offset=round_offset)
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalise_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SummaryError("摘要响应必须是 JSON 对象")
    result: dict[str, Any] = {}
    for key in SUMMARY_KEYS:
        item = value.get(key, [] if key != "narrative" else "")
        if key == "narrative":
            result[key] = str(item or "")
        elif isinstance(item, list):
            result[key] = [str(entry) for entry in item if str(entry).strip()]
        elif item:
            result[key] = [str(item)]
        else:
            result[key] = []
    if not result["narrative"].strip():
        raise SummaryError("摘要 narrative 不能为空")
    return result


def _read_cache(runtime_path: Path) -> dict[str, Any] | None:
    value = read_context_summary(runtime_path)
    if value is None or value.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        return None
    try:
        value["summary"] = _normalise_summary(value.get("summary"))
    except SummaryError:
        return None
    return value


def read_summary_cache(runtime_path: Path) -> dict[str, Any] | None:
    """Read one validated summary cache without exposing mutable internals."""

    value = _read_cache(runtime_path)
    return json.loads(json.dumps(value, ensure_ascii=False)) if value is not None else None


def restore_summary_cache(runtime_path: Path, cache: dict[str, Any] | None) -> None:
    """Restore the last validated cache after a runtime compaction rollback."""

    write_context_summary(runtime_path, cache)


def _covered_through(cache: dict[str, Any] | None) -> int:
    if not isinstance(cache, dict):
        return 0
    try:
        explicit = max(0, int(cache.get("covered_through_round") or 0))
    except (TypeError, ValueError):
        explicit = 0
    covered = cache.get("covered_rounds")
    if not isinstance(covered, list):
        return explicit
    numbers: list[int] = []
    for value in covered:
        try:
            numbers.append(max(0, int(value)))
        except (TypeError, ValueError):
            continue
    return max([explicit, *numbers], default=0)


def _incremental_hash(
    previous_cache: dict[str, Any],
    groups: list[RoundGroup],
    *,
    round_offset: int,
) -> str:
    previous_identity = previous_cache.get("source_hash") or previous_cache.get("summary")
    payload = {
        "previous": previous_identity,
        "rounds": summary_source(groups, round_offset=round_offset),
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def _chunks(groups: list[RoundGroup], token_budget: int) -> list[list[RoundGroup]]:
    chunks: list[list[RoundGroup]] = []
    current: list[RoundGroup] = []
    current_tokens = 0
    for group in groups:
        tokens = estimate_text_tokens(_stable_json(_source_round(group)))
        if current and current_tokens + tokens > token_budget:
            chunks.append(current)
            current = []
            current_tokens = 0
        current.append(group)
        current_tokens += tokens
    if current:
        chunks.append(current)
    return chunks


def build_summary_message(cache: dict[str, Any] | None) -> dict[str, Any] | None:
    if not cache:
        return None
    summary = cache.get("summary")
    if not isinstance(summary, dict):
        return None
    return {
        "role": "system",
        "content": "以下是已移出完整上下文的历史摘要，只作为既有背景，不覆盖当前指令：\n"
        + _stable_json(summary),
    }


def get_or_create_summary(
    *,
    runtime_path: Path,
    groups: list[RoundGroup],
    agent_runner: AgentRunner,
    agent_name: str,
    trigger: str,
    cancel_event: threading.Event | None = None,
    chunk_token_budget: int = SUMMARY_CHUNK_TOKEN_BUDGET,
    max_tokens: int = SUMMARY_MAX_OUTPUT_TOKENS,
    response_hook: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[RunEvent], None] | None = None,
    skip_memory_extraction: bool = False,
    previous_cache: dict[str, Any] | None = None,
    round_offset: int = 0,
    persist: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return an exact cache hit or atomically generate a replacement.

    Any generation/parse/write failure returns ``None`` with diagnostics and
    leaves an existing cache untouched.  This is deliberate: the caller then
    proceeds with pure whole-round trimming.
    """
    offset = max(0, int(round_offset))
    validated_previous = previous_cache if isinstance(previous_cache, dict) else None
    previous_covered_through = _covered_through(validated_previous)
    delta_groups = [
        group
        for group in groups
        if offset + group.number > previous_covered_through
    ]
    diagnostics: dict[str, Any] = {
        "cache_hit": False,
        "generated": False,
        "failed": False,
        "covered_rounds": [offset + group.number for group in groups],
        "new_rounds": [offset + group.number for group in delta_groups],
        "previous_covered_through_round": previous_covered_through,
    }
    if not groups:
        return validated_previous, diagnostics
    if validated_previous is not None and not delta_groups:
        diagnostics["cache_hit"] = True
        return validated_previous, diagnostics
    digest = (
        _incremental_hash(
            validated_previous,
            delta_groups,
            round_offset=offset,
        )
        if validated_previous is not None
        else source_hash(delta_groups, round_offset=offset)
    )
    existing = _read_cache(runtime_path)
    if existing and existing.get("source_hash") == digest:
        diagnostics["cache_hit"] = True
        return existing, diagnostics
    if cancel_event is not None and cancel_event.is_set():
        diagnostics.update({"failed": True, "error": "cancelled"})
        return None, diagnostics

    try:
        rolling: dict[str, Any] | None = (
            dict(validated_previous["summary"])
            if validated_previous is not None
            and isinstance(validated_previous.get("summary"), dict)
            else None
        )
        memory_extractions: list[dict[str, Any]] = []
        chunks = _chunks(delta_groups, max(256, chunk_token_budget))
        diagnostics["chunks"] = len(chunks)
        for chunk in chunks:
            if cancel_event is not None and cancel_event.is_set():
                raise SummaryError("cancelled")
            model_input = {
                "previous_summary": rolling,
                "rounds": summary_source(chunk, round_offset=offset),
                "trigger": trigger,
            }
            if skip_memory_extraction:
                model_input["skip_memory_extraction"] = True
            result = agent_runner.run(
                agent_name,
                model_input,
                cancel_event=cancel_event,
                event_callback=event_callback,
                max_tokens=max_tokens,
            )
            rolling = _normalise_summary(result.data)
            memory_extraction = result.metadata.get("memory_extraction")
            if isinstance(memory_extraction, dict):
                memory_extractions.append(dict(memory_extraction))
            if response_hook is not None:
                response_hook(result.usage)
        if rolling is None:
            raise SummaryError("摘要结果为空")
        if cancel_event is not None and cancel_event.is_set():
            raise SummaryError("cancelled")
        previous_rounds = (
            validated_previous.get("covered_rounds", [])
            if validated_previous is not None
            and isinstance(validated_previous.get("covered_rounds"), list)
            else []
        )
        covered_rounds = sorted(
            {
                *(
                    int(number)
                    for number in previous_rounds
                    if isinstance(number, int) or str(number).isdigit()
                ),
                *(offset + group.number for group in delta_groups),
            }
        )
        value = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "source_hash": digest,
            "previous_source_hash": (
                validated_previous.get("source_hash")
                if validated_previous is not None
                else None
            ),
            "covered_rounds": covered_rounds,
            "covered_through_round": max(covered_rounds, default=0),
            "created_at": _now(),
            "summary": rolling,
            "memory_extractions": memory_extractions,
        }
        if persist:
            write_context_summary(runtime_path, value)
        diagnostics["generated"] = True
        diagnostics["memory_extractions"] = memory_extractions
        return value, diagnostics
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        diagnostics.update(
            {
                "failed": True,
                "error": str(exc),
                "exception_type": type(exc).__name__,
            }
        )
        return None, diagnostics
