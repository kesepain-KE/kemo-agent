"""Provider usage normalization and task-wide aggregation."""

from __future__ import annotations

import copy
from typing import Any

from provider.schema import Usage


def usage_from_dict(value: dict[str, Any] | None) -> Usage:
    raw = value or {}
    prompt_value = raw.get("prompt_tokens")
    if prompt_value is None:
        prompt_value = raw.get("input_tokens")
    completion_value = raw.get("completion_tokens")
    if completion_value is None:
        completion_value = raw.get("output_tokens")
    prompt_tokens = max(0, int(prompt_value or 0))
    completion_tokens = max(0, int(completion_value or 0))
    total_value = raw.get("total_tokens")
    measurement = raw.get("measurement") if isinstance(raw.get("measurement"), dict) else {}
    mode = str(measurement.get("mode") or "")
    estimated = bool(
        raw.get("estimated", False)
        or mode in {"estimated", "mixed", "unknown"}
        or (measurement and not measurement.get("exact", False))
    )
    known = {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated",
        "source",
    }
    return Usage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=max(
            0,
            int(total_value if total_value is not None else prompt_tokens + completion_tokens),
        ),
        estimated=estimated,
        source=str(raw.get("source") or mode or "provider"),
        extra={key: item for key, item in raw.items() if key not in known},
    )


def merge_usage(total: dict[str, Any], usage: Usage) -> None:
    total["prompt_tokens"] = int(total.get("prompt_tokens", 0)) + usage.prompt_tokens
    total["completion_tokens"] = int(total.get("completion_tokens", 0)) + usage.completion_tokens
    total["total_tokens"] = int(total.get("total_tokens", 0)) + usage.total_tokens
    total["estimated"] = bool(total.get("estimated", False) or usage.estimated)
    cached: int | None = None
    for key in (
        "cached_input_tokens",
        "cached_prompt_tokens",
        "cache_hit_tokens",
        "cached_tokens",
    ):
        value = usage.extra.get(key)
        if value is not None:
            cached = max(0, int(value))
            break
    details = usage.extra.get("prompt_tokens_details")
    if cached is None and isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = max(0, int(details["cached_tokens"]))
    missed_raw = usage.extra.get("cache_miss_tokens")
    missed = max(0, int(missed_raw)) if missed_raw is not None else None
    if cached is not None:
        missed = max(0, usage.prompt_tokens - cached) if missed is None else missed
        total["cached_prompt_tokens"] = int(total.get("cached_prompt_tokens", 0)) + cached
        total["cache_miss_tokens"] = int(total.get("cache_miss_tokens", 0)) + missed
        denominator = total["cached_prompt_tokens"] + total["cache_miss_tokens"]
        total["cache_hit_rate"] = (
            round(total["cached_prompt_tokens"] / denominator, 6) if denominator else 0.0
        )
        total["cached_input_tokens"] = total["cached_prompt_tokens"]
    reasoning = usage.extra.get("reasoning_tokens")
    if reasoning is not None:
        total["reasoning_tokens"] = int(total.get("reasoning_tokens", 0)) + max(
            0, int(reasoning)
        )
    visible = usage.extra.get("visible_output_tokens")
    if visible is not None:
        total["visible_output_tokens"] = int(total.get("visible_output_tokens", 0)) + max(
            0, int(visible)
        )
    stages = usage.extra.get("stages")
    if isinstance(stages, list):
        total.setdefault("stages", []).extend(copy.deepcopy(stages))
    media = usage.extra.get("media")
    if isinstance(media, dict):
        aggregate_media = total.setdefault("media", {})
        for key, value in media.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                aggregate_media[key] = aggregate_media.get(key, 0) + value
    measurement = usage.extra.get("measurement")
    if isinstance(measurement, dict):
        previous = total.get("measurement")
        if not isinstance(previous, dict) or previous.get("mode") == "unknown":
            total["measurement"] = copy.deepcopy(measurement)
        elif previous.get("mode") != measurement.get("mode"):
            total["measurement"] = {
                "mode": "mixed",
                "exact": False,
                "exact_fields": [],
                "estimated_fields": sorted(
                    {
                        *previous.get("estimated_fields", []),
                        *measurement.get("estimated_fields", []),
                    }
                ),
            }
    provider_raw = usage.extra.get("provider_raw")
    if isinstance(provider_raw, dict) and provider_raw:
        total.setdefault("provider_raw", []).append(copy.deepcopy(provider_raw))
    provider_request_count = usage.extra.get("provider_request_count")
    if provider_request_count is not None:
        total["provider_request_count"] = int(
            total.get("provider_request_count", 0)
        ) + max(0, int(provider_request_count))
    total["input_tokens"] = total["prompt_tokens"]
    total["output_tokens"] = total["completion_tokens"]


def record_provider_request(total: dict[str, Any], usage: Usage) -> None:
    declared = usage.extra.get("provider_request_count")
    merge_usage(total, usage)
    if declared is None or max(0, int(declared)) == 0:
        total["provider_request_count"] = int(total.get("provider_request_count", 0)) + 1


def new_usage_total() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "provider_request_count": 0,
        "estimated": False,
        "measurement": {"mode": "unknown", "exact": False},
        "stages": [],
        "media": {},
    }
