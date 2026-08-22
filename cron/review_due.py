"""Cron-owned scan for expired temporary memories."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from provider.factory import create_provider
from run.agents import AgentRunner
from run.memory import (
    TEMPORARY_TIERS,
    MemoryStore,
    normalize_memory_filename,
    parse_time,
    utc_now,
)
from run.conversation import new_usage_total, record_provider_request, usage_from_dict


class MemoryPromotionError(RuntimeError):
    pass


PROMOTION_BATCH_SIZE = 20
PERMANENT_PROMOTION_BATCH_SIZE = 8
MAX_CONSECUTIVE_BATCH_FAILURES = 2


def _promotion_batches(
    promotions: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in promotions:
        grouped.setdefault(str(item.get("to_tier") or ""), []).append(item)
    batches: list[list[dict[str, Any]]] = []
    for target in ("one_month", "half_year", "permanent"):
        items = grouped.pop(target, [])
        size = (
            PERMANENT_PROMOTION_BATCH_SIZE
            if target == "permanent"
            else PROMOTION_BATCH_SIZE
        )
        batches.extend(
            items[index : index + size] for index in range(0, len(items), size)
        )
    for items in grouped.values():
        batches.extend(
            items[index : index + PROMOTION_BATCH_SIZE]
            for index in range(0, len(items), PROMOTION_BATCH_SIZE)
        )
    return batches


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

    runner = AgentRunner(
        root,
        user,
        config=config,
        provider_factory=provider_factory,
    )
    usage = new_usage_total()
    applied: list[str] = []
    summaries: list[dict[str, Any]] = []
    batch_results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    models: list[str] = []
    batches = _promotion_batches(due_promotions)
    consecutive_failures = 0
    processed_batches = 0

    for batch_index, batch in enumerate(batches):
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            result = runner.run(
                "self_improve",
                {
                    "trigger": "memory_promotion",
                    "promotions": batch,
                },
                cancel_event=cancel_event,
            )
            promotions = result.data.get("promotions")
            if not isinstance(promotions, list):
                raise MemoryPromotionError("self_improve 输出缺少 promotions 数组")
            record_provider_request(usage, usage_from_dict(result.usage))
            if result.model and result.model not in models:
                models.append(result.model)
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            errors.append(
                {
                    "batch": batch_index + 1,
                    "requested": len(batch),
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
            )
            batch_results.append(
                {
                    "batch": batch_index + 1,
                    "requested": len(batch),
                    "applied": 0,
                    "status": "failed",
                }
            )
            processed_batches += 1
            if consecutive_failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                break
            continue

        decisions = {
            (
                str(item.get("from_tier") or ""),
                normalize_memory_filename(item.get("filename")),
            ): item
            for item in promotions
            if isinstance(item, dict) and item.get("filename")
        }
        batch_applied: list[str] = []
        with store._lock:
            for requested in batch:
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
                    batch_applied.append(requested["filename"])
                    continue
                merged_with = decision.get("merged_with")
                if merged_with:
                    merged_content = decision.get("content")
                    if (
                        not isinstance(merged_content, str)
                        or not merged_content.strip()
                    ):
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
                batch_applied.append(requested["filename"])
        applied.extend(batch_applied)
        summaries.extend(
            summary
            for item in promotions
            if (summary := _promotion_summary(item)) is not None
        )
        batch_results.append(
            {
                "batch": batch_index + 1,
                "requested": len(batch),
                "applied": len(batch_applied),
                "status": (
                    "completed" if len(batch_applied) == len(batch) else "partial"
                ),
            }
        )
        processed_batches += 1

    applied_set = set(applied)
    pending = [
        item["filename"]
        for item in due_promotions
        if item["filename"] not in applied_set
    ]
    return {
        "status": "completed" if not pending and not errors else "partial",
        "requested": len(due_promotions),
        "deleted": deleted,
        "promotions": summaries,
        "applied": applied,
        "pending": pending,
        "batches": batch_results,
        "processed_batches": processed_batches,
        "total_batches": len(batches),
        "errors": errors,
        "model": models[-1] if models else None,
        "models": models,
        "usage": usage,
    }
