"""Cron-owned scan for expired temporary memories."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agent_runner import AgentRunner
from run.memory import (
    TEMPORARY_TIERS,
    MemoryStore,
    normalize_memory_filename,
    parse_time,
    utc_now,
)


class MemoryPromotionError(RuntimeError):
    pass


def _promotion_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        key: value.get(key)
        for key in (
            "from_tier",
            "to_tier",
            "filename",
            "merged_with",
            "skill_created",
        )
        if key in value
    }


def scan_and_promote(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    cancel_event: threading.Event | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Delete expired low-weight memories and dispatch eligible promotions."""
    store = MemoryStore(root, user, config)
    current = now or utc_now()
    due_promotions: list[dict[str, Any]] = []
    deleted: list[str] = []

    with store._lock:
        for tier in TEMPORARY_TIERS:
            rule = store.rules[tier]
            for item in store.load_tier(tier):
                filename = str(item["filename"])
                meta = item
                expires_at = parse_time(meta.get("expires_at"))
                if expires_at is None or expires_at > current:
                    continue
                weight = int(meta.get("weight", 0))
                threshold = int(rule.upgrade_threshold or 0)
                if weight < threshold:
                    store.delete_fragment(tier, filename)
                    deleted.append(filename)
                    continue
                if rule.next is None:
                    raise MemoryPromotionError(f"临时记忆层缺少晋升目标：{tier}")
                due_promotions.append(
                    {
                        "from_tier": tier,
                        "to_tier": rule.next,
                        "filename": filename,
                        "content": str(item.get("content") or "").strip(),
                        "weight": weight,
                        "expires_at": meta.get("expires_at"),
                    }
                )

    if cancel_event is not None and cancel_event.is_set():
        return {
            "status": "cancelled",
            "requested": 0,
            "deleted": deleted,
            "promotions": [],
        }
    if not due_promotions:
        return {
            "status": "completed",
            "requested": 0,
            "deleted": deleted,
            "promotions": [],
        }

    result = AgentRunner(
        root,
        user,
        config=config,
        provider_factory=provider_factory,
    ).run(
        "self_improve",
        {
            "trigger": "memory_promotion",
            "promotions": due_promotions,
        },
        cancel_event=cancel_event,
    )
    promotions = result.data.get("promotions")
    if not isinstance(promotions, list):
        raise MemoryPromotionError("self_improve 输出缺少 promotions 数组")
    decisions = {
        (
            str(item.get("from_tier") or ""),
            normalize_memory_filename(item.get("filename")),
        ): item
        for item in promotions
        if isinstance(item, dict) and item.get("filename")
    }
    applied: list[str] = []
    with store._lock:
        for requested in due_promotions:
            key = (requested["from_tier"], requested["filename"])
            decision = decisions.get(key)
            if not isinstance(decision, dict):
                continue
            if decision.get("to_tier") != requested["to_tier"]:
                continue
            location = store.locate_in_tier(
                requested["from_tier"], requested["filename"]
            )
            if location is None:
                applied.append(requested["filename"])
                continue
            merged_with = decision.get("merged_with")
            if merged_with:
                merged_content = decision.get("content")
                if not isinstance(merged_content, str) or not merged_content.strip():
                    continue
                store._promote_location(
                    location,
                    requested["to_tier"],
                    current,
                    merged_content=merged_content,
                    target_filename=str(merged_with),
                )
            else:
                store._promote_location(
                    location,
                    requested["to_tier"],
                    current,
                )
            applied.append(requested["filename"])
    summaries = [
        summary
        for item in promotions
        if (summary := _promotion_summary(item)) is not None
    ]
    return {
        "status": "completed",
        "requested": len(due_promotions),
        "deleted": deleted,
        "promotions": summaries,
        "applied": applied,
        "model": result.model,
        "usage": result.usage,
    }
