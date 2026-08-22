"""Memory extraction performed synchronously during context compression."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from run.agent_runner import AgentRunResult
from run.memory import MemoryStore
from run.long_task_runtime import semantic_user_text


class MemoryExtractionError(RuntimeError):
    pass


def _text_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return str(value or "")
    parts: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
        else:
            parts.append(
                f"[{block.get('type') or 'content'}:{block.get('asset_id') or 'inline'}]"
            )
    return "\n".join(part for part in parts if part)


def memory_round_payload(
    window: dict[str, Any], round_number: int
) -> dict[str, Any]:
    """Rebuild the extraction payload for one durable archived round."""

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for raw in (window.get("text") or {}).get("messages", []):
        if not isinstance(raw, dict):
            continue
        message = copy.deepcopy(raw)
        if message.get("role") == "user" and current:
            groups.append(current)
            current = []
        current.append(message)
    if current:
        groups.append(current)
    if round_number < 1 or round_number > len(groups):
        raise MemoryExtractionError(
            f"归档缺少第 {round_number} 轮正文（可用轮数：{len(groups)}）"
        )
    group = groups[round_number - 1]
    prompt = "\n".join(
        semantic_user_text(item, _text_value(item.get("content")))
        for item in group
        if item.get("role") == "user"
    ).strip()
    text = "\n".join(
        _text_value(item.get("content"))
        for item in group
        if item.get("role") == "assistant"
    ).strip()
    think = next(
        (
            item
            for item in (window.get("think") or {}).get("rounds", [])
            if isinstance(item, dict) and item.get("round") == round_number
        ),
        {},
    )
    tool = next(
        (
            item
            for item in (window.get("tool") or {}).get("rounds", [])
            if isinstance(item, dict) and item.get("round") == round_number
        ),
        {},
    )
    records = tool.get("calls", []) if isinstance(tool, dict) else []
    return {
        "prompt": prompt,
        "text": text,
        "reasoning": str(think.get("content") or "") if isinstance(think, dict) else "",
        "tool_records": copy.deepcopy(records) if isinstance(records, list) else [],
    }


def extract_compressed_round_memory(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    rounds: list[dict[str, Any]],
    trigger: str,
    agent_runner: Any,
    cancel_event: Any = None,
) -> AgentRunResult:
    """Extract and persist memory from complete rounds before compression."""
    complete_rounds = [item for item in rounds if isinstance(item, dict)]
    round_numbers: list[int] = []
    for raw_round in complete_rounds:
        try:
            round_numbers.append(int(raw_round.get("round")))
        except (TypeError, ValueError):
            pass

    source = {
        "source": "context_compression",
        "trigger": trigger,
        "rounds": round_numbers,
    }
    result = agent_runner.run(
        "self_improve",
        {
            "trigger": "context_compression",
            "rounds": complete_rounds,
            "source": source,
        },
        cancel_event=cancel_event,
    )
    candidates = result.data.get("candidates")
    if not isinstance(candidates, list):
        raise MemoryExtractionError("self_improve 输出缺少 candidates 数组")
    MemoryStore(root, user, config).upsert_candidates(candidates, source=source)
    return result
