"""Context inspection and mutable runtime-window compaction services."""

from __future__ import annotations

import copy
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from run.agents import AgentRunner
from run.config import load_config, project_root
from run.context import ContextPolicy, select_context
from run.context.summary import (
    SUMMARY_MAX_OUTPUT_TOKENS,
    build_summary_message,
    read_summary_cache,
)
from run.history.store import context_summary_exists
from run.history import load_runtime_window, prepare_window, runtime_window_path
from run.memory import MemoryStore
from run.config.prompt import build_prompt_bundle
from run.conversation import required_text
from run.tools import (
    ToolRegistry,
    apply_runtime_tool_policy,
    discover_tools,
)


def round_item_data(window: dict[str, Any], round_number: int) -> list[dict[str, Any]]:
    return [
        item
        for item in (window.get("items") or {}).get("items", [])
        if isinstance(item, dict)
        and isinstance(item.get("metadata"), dict)
        and item["metadata"].get("round") == round_number
    ]


def _tool_think_summary(data: dict[str, Any]) -> str:
    """Keep the compact narrative plus any structured conclusions it omitted."""

    narrative = str(data.get("narrative") or "").strip()
    sections: list[str] = []
    for key, label in (
        ("facts", "事实"),
        ("decisions", "决策"),
        ("tool_results", "工具结论"),
        ("unfinished", "未完成"),
    ):
        values = data.get(key)
        if not isinstance(values, list):
            continue
        missing = [
            str(value).strip()
            for value in values
            if str(value).strip() and str(value).strip() not in narrative
        ]
        if missing:
            sections.append(f"{label}：" + "；".join(missing))
    return "\n".join(part for part in (narrative, *sections) if part).strip()


def compress_per_round_tool_think(
    *,
    window: dict[str, Any],
    conserved_rounds: int,
    agent_runner: AgentRunner,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    """Compress at most one newly unprotected round in the mutable temp mirror."""

    think_rounds = (window.get("think") or {}).get("rounds", [])
    tool_rounds = (window.get("tool") or {}).get("rounds", [])
    think_by_number = {
        int(item["round"]): item
        for item in think_rounds
        if isinstance(item, dict) and str(item.get("round", "")).isdigit()
    }
    tool_by_number = {
        int(item["round"]): item
        for item in tool_rounds
        if isinstance(item, dict) and str(item.get("round", "")).isdigit()
    }
    latest_round = int((window.get("data") or {}).get("rounds", 0))
    candidates = [
        number
        for number in sorted(set(think_by_number) | set(tool_by_number))
        if latest_round - number > max(0, conserved_rounds)
        and not bool((think_by_number.get(number) or {}).get("compressed"))
        and not bool((tool_by_number.get(number) or {}).get("compressed"))
    ]
    if not candidates:
        return {"compressed": False, "round": None}
    round_number = candidates[0]
    think_data = think_by_number.get(round_number)
    tool_data = tool_by_number.get(round_number)
    item_data = round_item_data(window, round_number)
    has_payload = bool(str((think_data or {}).get("content") or "").strip()) or bool(
        (tool_data or {}).get("calls")
    ) or any(item.get("type") in {"reasoning", "tool_call", "tool_result"} for item in item_data)
    summary = ""
    usage: dict[str, Any] = {}
    if has_payload:
        result = agent_runner.run(
            "context_manage",
            {
                "previous_summary": None,
                "rounds": [
                    {
                        "round": round_number,
                        "think": copy.deepcopy(think_data),
                        "tool": copy.deepcopy(tool_data),
                        "items": copy.deepcopy(item_data),
                    }
                ],
                "trigger": "tool_think_compress",
            },
            cancel_event=cancel_event,
            max_tokens=SUMMARY_MAX_OUTPUT_TOKENS,
        )
        summary = _tool_think_summary(result.data)
        if not summary:
            raise RuntimeError("context_manage 工具/思考摘要为空，已保留原始数据")
        usage = dict(result.usage)
    if think_data is not None:
        think_data["content"] = summary
        think_data["summary"] = summary
        think_data["compressed"] = True
    if tool_data is not None:
        tool_data["calls"] = []
        tool_data["compressed"] = True

    items = (window.get("items") or {}).get("items", [])
    rewritten: list[dict[str, Any]] = []
    summary_written = False
    for item in items if isinstance(items, list) else []:
        metadata = item.get("metadata") if isinstance(item, dict) else None
        same_round = isinstance(metadata, dict) and metadata.get("round") == round_number
        if not same_round:
            rewritten.append(item)
            continue
        kind = item.get("type")
        if kind in {"tool_call", "tool_result"}:
            continue
        if kind == "reasoning":
            if summary_written:
                continue
            if not summary:
                rewritten.append(copy.deepcopy(item))
                summary_written = True
                continue
            replacement = copy.deepcopy(item)
            replacement["content"] = summary
            replacement["extensions"] = {
                **(replacement.get("extensions") or {}),
                "compressed": True,
            }
            rewritten.append(replacement)
            summary_written = True
            continue
        if (
            not summary_written
            and summary
            and kind == "message"
            and item.get("role") == "assistant"
        ):
            rewritten.append(
                {
                    "id": f"rs_{uuid.uuid4().hex}",
                    "type": "reasoning",
                    "status": "completed",
                    "content": summary,
                    "metadata": {"round": round_number, "history_source": "runtime_compression"},
                    "extensions": {"compressed": True},
                }
            )
            summary_written = True
        rewritten.append(item)
    if isinstance((window.get("items") or {}).get("items"), list):
        window["items"]["items"] = rewritten
    return {
        "compressed": True,
        "round": round_number,
        "generated": has_payload,
        "usage": usage,
    }


def context_status(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
) -> dict[str, Any]:
    user = required_text(request, "user")
    source = required_text(request, "source")
    session_id = required_text(request, "session_id")
    base = (root or project_root()).resolve()
    config = load_config(user, base)
    policy = ContextPolicy.from_config(config)
    window_path, archive_window, is_new = prepare_window(base, user, source, session_id)
    runtime_path, window = (
        (runtime_window_path(window_path), archive_window)
        if is_new
        else load_runtime_window(
            window_path,
            archive_window,
            max_rounds=policy.max_rounds,
        )
    )
    tool_config = config.get("tools") or {}
    registry = (
        apply_runtime_tool_policy(tool_registry_factory(base, user), config)
        if bool(tool_config.get("enabled", True))
        else ToolRegistry({})
    )
    memory_store = MemoryStore(base, user, config)
    prompt_bundle = build_prompt_bundle(
        base,
        user,
        config,
        plugin_manifests=registry.plugin_manifests,
        memory_store=memory_store,
        source=source,
        session_id=session_id,
    )
    summary_cache = read_summary_cache(runtime_path)
    selection = select_context(
        window=window,
        policy=policy,
        system_message=(
            {"role": "system", "content": prompt_bundle.text}
            if prompt_bundle.text
            else None
        ),
        summary_message=build_summary_message(summary_cache),
        current_user_message=None,
        tools=registry.schemas() or None,
    )
    persisted = window.get("data", {}).get("context")
    return {
        "user": user,
        "source": source,
        "session_id": session_id,
        "window": None if is_new else window_path.name,
        "rounds": int(window.get("data", {}).get("rounds", 0)),
        "context": selection.stats(),
        "prompt": prompt_bundle.diagnostics,
        "last_committed_context": persisted if isinstance(persisted, dict) else None,
        "summary_cache_exists": context_summary_exists(runtime_path),
        "policy": {
            "recent_tool_rounds": policy.recent_tool_rounds,
            "recent_full_rounds": policy.recent_full_rounds,
            "max_rounds": policy.max_rounds,
            "rounds_after_compression": policy.rounds_after_compression,
            "token_limit": policy.token_limit,
            "compression_ratio": policy.compression_ratio,
            "input_budget": policy.input_budget,
            "output_reserve": policy.output_reserve,
        },
    }
