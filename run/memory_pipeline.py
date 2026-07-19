"""Memory extraction performed synchronously during context compression."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from run.agent_runner import AgentRunResult
from run.memory import MemoryStore


class MemoryExtractionError(RuntimeError):
    pass


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
