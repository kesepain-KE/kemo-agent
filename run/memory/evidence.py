"""Host-owned evidence dates and references; never trust model-supplied dates."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any


def text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(str(b.get("text") or "") for b in value
                         if isinstance(b, dict) and b.get("type") == "text")
    return ""


def normalize(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def trusted_dates(rounds: list[dict[str, Any]]) -> dict[str, str]:
    from run.memory import local_day, utc_now

    result = {}
    for item in rounds:
        try:
            # Missing/naive legacy times cannot establish a trustworthy day.
            stamp = datetime.fromisoformat(str(item.get("committed_at") or "").replace("Z", "+00:00"))
            number = item.get("round")
            if type(number) is int and number > 0 and stamp.tzinfo and stamp <= utc_now():
                result[str(number)] = local_day(stamp)
        except (ValueError, TypeError):
            continue
    return result


def quoted_rounds(candidate: dict[str, Any], rounds: list[dict[str, Any]]) -> list[int]:
    """A quote must occur inside a single user message, not across messages."""
    quote = normalize(candidate.get("evidence"))
    if not quote:
        return []
    matches = sorted({r["round"] for r in rounds
                      if type(r.get("round")) is int and r["round"] > 0
                      and any(isinstance(m, dict) and m.get("role") == "user"
                              and quote in normalize(text(m.get("content")))
                              for m in r.get("messages", []))})
    selected = candidate.get("evidence_rounds")
    if selected is None:  # Compatibility: infer only exact matching rounds.
        return matches
    if not isinstance(selected, list) or not selected or any(
        type(n) is not int or n not in matches for n in selected
    ):
        return []
    return sorted(set(selected))


def search_receipts(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Only successful tool results with full content authorize an old reference."""
    found = {}
    for call in metadata.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("name") != "memory_manage":
            continue
        payload = call.get("result") or {}
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            continue
        data = payload.get("result")
        if not isinstance(data, dict) or data.get("action") != "search_many":
            continue
        for group in data.get("results") or []:
            if not isinstance(group, dict):
                continue
            for hit in group.get("matches") or []:
                if (isinstance(hit, dict) and isinstance(hit.get("content"), str)
                        and isinstance(hit.get("memory_ref"), str)
                        and isinstance(hit.get("filename"), str)):
                    found[hit["memory_ref"]] = hit
    return found


def bind_reference(candidate: dict[str, Any], receipts: dict[str, dict[str, Any]], store: Any) -> dict[str, Any]:
    """Bind a model decision to the exact searched row and content snapshot."""
    from run.memory import normalize_memory_filename

    result = dict(candidate)
    result.pop("expected_content_hash", None)
    action = str(result.get("action") or "upsert").strip().casefold()
    result["action"] = action
    ref = result.get("memory_ref")
    filename = result.get("filename")
    if ref:
        if not isinstance(ref, str) or ref not in receipts:
            raise ValueError("unverified_memory_ref")
        hit = receipts[ref]
        if filename and normalize_memory_filename(filename) != hit["filename"]:
            raise ValueError("reference_filename_mismatch")
        filename = hit["filename"]
    else:
        filename = normalize_memory_filename(filename or result.get("content"))
        location = store.locate(filename)
        if location is not None:
            ref = f"{location.tier}:{location.filename}"
            if ref not in receipts:
                raise ValueError("existing_memory_requires_search")
        elif action in {"reinforce", "revise", "forget"}:
            raise ValueError("missing_memory_ref")
    result["filename"] = filename
    if ref:
        hit = receipts[ref]
        if action == "create":
            raise ValueError("create_conflicts_with_existing")
        result["memory_ref"] = ref
        result["expected_content_hash"] = sha256(hit["content"].encode("utf-8")).hexdigest()
        if action == "upsert":
            content = result.get("content")
            result["action"] = "reinforce" if isinstance(content, str) and content.strip() == hit["content"] else "revise"
        if result.get("action") == "reinforce":
            # A reinforcement must never rewrite the stored body.
            result["content"] = hit["content"]
    elif action == "upsert":
        result["action"] = "create"
    return result
