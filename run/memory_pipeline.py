"""Background memory extraction submitted only after a committed round."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunResult
from run.agent_service import get_agent_scheduler
from run.memory import MemoryStore


class MemoryExtractionError(RuntimeError):
    pass


def _existing_candidates(store: MemoryStore, text: str, limit: int) -> list[dict[str, Any]]:
    return [
        {
            "id": item["id"],
            "content": item["content"],
            "tier": item["tier"],
            "keywords": item.get("keywords", []),
            "entities": item.get("entities", []),
        }
        for item in store.search(text, limit=limit)
    ]


def submit_memory_extraction(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    user_text: str,
    assistant_text: str,
    tool_results: list[dict[str, Any]],
    source: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
) -> str:
    """Queue one committed round; the worker validates then atomically writes."""

    store = MemoryStore(root, user, config)
    limit = int((config.get("memory") or {}).get("existing_candidates_for_extraction", 12))
    existing = _existing_candidates(store, f"{user_text}\n{assistant_text}", limit)

    def persist(result: AgentRunResult) -> None:
        candidates = result.data.get("candidates")
        if not isinstance(candidates, list):
            raise MemoryExtractionError("self_improve 输出缺少 candidates 数组")
        store.upsert_candidates(candidates, source=source)
        store.review_due()

    scheduler = get_agent_scheduler(
        root,
        user,
        config=config,
        provider_factory=provider_factory,
    )
    return scheduler.submit(
        "self_improve",
        {
            "user_text": user_text,
            "assistant_text": assistant_text,
            "tool_results": tool_results,
            "source": source,
            "existing_candidates": existing,
        },
        result_handler=persist,
    )
