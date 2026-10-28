"""Content-free, per-window calibration from accepted main-model input usage."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def provider_identity(provider: dict[str, Any]) -> str:
    # Never persist routing credentials or an address; only a route/model digest.
    value = [str(provider.get(key) or "") for key in ("type", "base_url", "model")]
    return hashlib.sha256(json.dumps(value).encode("utf-8")).hexdigest()


def usage_checkpoint(
    window: dict[str, Any], provider: dict[str, Any], cache: dict[str, Any] | None,
) -> dict[str, Any] | None:
    for metric in reversed((window.get("data") or {}).get("round_metrics") or []):
        if not isinstance(metric, dict) or "context_usage" not in metric:
            continue
        value = metric["context_usage"]
        if not isinstance(value, dict):
            return None
        if value.get("provider") != provider_identity(provider):
            return None
        if value.get("summary_hash") != (cache or {}).get("source_hash", ""):
            return None
        if all(type(value.get(key)) is int and value[key] > 0
               for key in ("input_tokens", "local_tokens")):
            return dict(value)
        return None
    return None


def make_checkpoint(
    input_tokens: int, local_tokens: int,
    provider: dict[str, Any], cache: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "input_tokens": input_tokens, "local_tokens": local_tokens,
        "provider": provider_identity(provider),
        "summary_hash": (cache or {}).get("source_hash", ""),
    }


def projected_input(local_tokens: int, checkpoint: dict[str, Any] | None) -> int:
    if not checkpoint:
        return local_tokens
    return max(local_tokens, checkpoint["input_tokens"] + local_tokens - checkpoint["local_tokens"])
