"""用于删除历史回合的派生原子上下文摘要缓存。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from events import RunEvent
from run.agent_runner import AgentRunner
from run.context import RoundGroup, estimate_text_tokens


SUMMARY_SCHEMA_VERSION = 2
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
        "tools": (group.tool or {}).get("calls", []),
    }


def summary_source(groups: list[RoundGroup]) -> list[dict[str, Any]]:
    return [_source_round(group) for group in groups]


def source_hash(groups: list[RoundGroup]) -> str:
    payload = _stable_json(summary_source(groups)).encode("utf-8")
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
    return result


def _read_cache(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("schema_version") != SUMMARY_SCHEMA_VERSION:
        return None
    try:
        value["summary"] = _normalise_summary(value.get("summary"))
    except SummaryError:
        return None
    return value


def _atomic_write(path: Path, value: dict[str, Any]) -> None:
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
    cache_path: Path,
    groups: list[RoundGroup],
    agent_runner: AgentRunner,
    agent_name: str,
    trigger: str,
    cancel_event: threading.Event | None = None,
    chunk_token_budget: int = 24000,
    max_tokens: int = 2048,
    response_hook: Callable[[dict[str, Any]], None] | None = None,
    event_callback: Callable[[RunEvent], None] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return an exact cache hit or atomically generate a replacement.

    Any generation/parse/write failure returns ``None`` with diagnostics and
    leaves an existing cache untouched.  This is deliberate: the caller then
    proceeds with pure whole-round trimming.
    """
    diagnostics: dict[str, Any] = {
        "cache_hit": False,
        "generated": False,
        "failed": False,
        "covered_rounds": [group.number for group in groups],
    }
    if not groups:
        return None, diagnostics
    digest = source_hash(groups)
    existing = _read_cache(cache_path)
    if existing and existing.get("source_hash") == digest:
        diagnostics["cache_hit"] = True
        return existing, diagnostics
    if cancel_event is not None and cancel_event.is_set():
        diagnostics.update({"failed": True, "error": "cancelled"})
        return None, diagnostics

    try:
        rolling: dict[str, Any] | None = None
        memory_extractions: list[dict[str, Any]] = []
        chunks = _chunks(groups, max(256, chunk_token_budget))
        diagnostics["chunks"] = len(chunks)
        for chunk in chunks:
            if cancel_event is not None and cancel_event.is_set():
                raise SummaryError("cancelled")
            result = agent_runner.run(
                agent_name,
                {
                    "previous_summary": rolling,
                    "rounds": summary_source(chunk),
                    "trigger": trigger,
                },
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
        value = {
            "schema_version": SUMMARY_SCHEMA_VERSION,
            "source_hash": digest,
            "covered_rounds": [group.number for group in groups],
            "created_at": _now(),
            "summary": rolling,
            "memory_extractions": memory_extractions,
        }
        _atomic_write(cache_path, value)
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
