"""后台内存提取仅在提交一轮后提交。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunResult
from run.agent_service import get_agent_scheduler
from run.memory import MemoryStore


class MemoryExtractionError(RuntimeError):
    pass


EXISTING_CANDIDATE_LIMIT = 12


def _existing_candidates(store: MemoryStore, text: str, limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in store.search(text, limit=limit):
        selected.append({"filename": item["filename"], "tier": item["tier"]})
        seen.add(item["filename"].casefold())
    for item in store.list_file_references():
        if len(selected) >= max(0, limit):
            break
        key = item["filename"].casefold()
        if key in seen:
            continue
        selected.append({"filename": item["filename"], "tier": item["tier"]})
        seen.add(key)
    return selected


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
    existing = _existing_candidates(
        store,
        f"{user_text}\n{assistant_text}",
        EXISTING_CANDIDATE_LIMIT,
    )

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
