"""Host-owned evidence dates and references; never trust model-supplied dates."""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
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


def contains_sensitive_payload(value: Any) -> bool:
    """Inspect original values, not JSON with escaped credential-key quotes."""
    from run.memory import contains_sensitive_credential
    if isinstance(value, str):
        return contains_sensitive_credential(value)
    if isinstance(value, dict):
        return any(
            contains_sensitive_payload(item) or (
                item is not None and not isinstance(item, (dict, list))
                and contains_sensitive_credential(f"{key}: {item}"))
            for key, item in value.items())
    if isinstance(value, list):
        return any(contains_sensitive_payload(item) for item in value)
    return False


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


def safe_body(value: Any, *, role: str) -> str:
    """Only visible narrative, never fenced/quoted payloads or credentials."""
    from run.memory import contains_sensitive_credential
    body = text(value)
    if contains_sensitive_credential(body):
        return ""
    visible, fence = [], None
    for line in body.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            continue
        if fence or line.startswith(("    ", "\t")) or line.lstrip().startswith(
                (">", "工具结果", "Tool result")):
            continue
        visible.append(line)
    body = "\n".join(visible)
    if role == "assistant":
        body = "\n\n".join(p for p in body.split("\n\n") if not re.search(
            r"可能|猜测|待验证|尚未确认|据推测|\b(?:maybe|possibly|guess|unverified)\b", p, re.I))
    return body.strip()


def narrative_record(card) -> dict | None:
    """Only actual user/guidance and delivered assistant prose is evidence.

    VIEW activity is irrelevant: absorbed RAW remains evidence. Withdrawn
    integrations are excluded by the RAW query, not by token_active.
    """
    payload = card.raw_payload
    if card.raw_kind == "user_text":
        role = "user"
    elif card.raw_kind == "agent_text" and card.raw_level == "final":
        role = "assistant"
    else:
        return None
    if any(payload.get(key) for key in ("incomplete", "retry_snapshot", "withdrawn")):
        return None
    body = safe_body(payload.get("text") or payload.get("content"), role=role)
    if not body:
        return None
    return {"round": card.seq, "seq": card.seq, "card_id": card.card_id,
            "integration_id": card.integration_id, "level": card.raw_level,
            "committed_at": card.created_at, "messages": [{"role": role, "content": body}]}


def narrative_rows(db, key, start, end):
    """Stream narrative only; never count tool-card traffic as memory input."""
    return db.execute(
        "SELECT c.* FROM context_cards c LEFT JOIN card_integrations i "
        "ON i.integration_id=c.integration_id AND i.session_key=c.session_key "
        "WHERE c.session_key=? AND c.seq>=? AND c.seq<=? "
        "AND c.raw_kind IN ('user_text','agent_text') "
        "AND COALESCE(json_extract(i.record_json,'$.withdrawn'),0)!=1 ORDER BY c.seq",
        (key, start, end))


def extraction_rounds(rounds: list[Any]) -> list[dict[str, Any]]:
    result = []
    for item in rounds:
        if not isinstance(item, dict):
            continue
        messages = []
        for message in item.get("messages") or []:
            if isinstance(message, dict) and message.get("role") in {"user", "assistant"}:
                role = message["role"]
                body = safe_body(message.get("content"), role=role)
                if body:
                    messages.append({"role": role, "content": body})
        record = {key: item[key] for key in (
            "round", "seq", "card_id", "integration_id", "level", "committed_at", "context_only")
            if key in item}
        record["messages"] = messages
        result.append(record)
    return result


def quoted_rounds(candidate: dict[str, Any], rounds: list[dict[str, Any]],
                  *, role: str = "user") -> list[int]:
    """A quote must occur inside a single safe message of the permitted role."""
    quote = normalize(candidate.get("evidence"))
    if not quote:
        return []
    matches = sorted({r["round"] for r in rounds
                       if type(r.get("round")) is int and r["round"] > 0 and not r.get("context_only")
                      and any(isinstance(m, dict) and m.get("role") == role
                              and quote in normalize(safe_body(m.get("content"), role=role))
                              for m in r.get("messages", []))})
    selected = candidate.get("evidence_rounds")
    if selected is None:  # Compatibility: infer only exact matching rounds.
        return matches
    if not isinstance(selected, list) or not selected or any(
        type(n) is not int or n not in matches for n in selected
    ):
        return []
    return sorted(set(selected))


def assistant_backing(candidate: dict, rounds: list[dict]) -> list[int]:
    """Assistant-only recitation cannot manufacture an independent observation.

    RAW records require host-owned integration linkage; combined evidence
    records use their own record ID. Generic assent and questions do not count.
    """
    from run.memory import _tokens, contains_sensitive_credential
    backing = candidate.get("user_evidence")
    if not isinstance(backing, str) or contains_sensitive_credential(backing):
        return []
    if len(normalize(backing)) < 8 or any(c in backing for c in ("?", "？")):
        return []
    if not re.search(r"确认|仍然|依然|确实|一直|没变|正确|\b(?:confirm|still|correct|unchanged)\b", backing, re.I):
        return []
    overlap = _tokens(backing) & _tokens(candidate.get("content", ""))
    if len(overlap) < 3:
        return []
    user_candidate = {k: v for k, v in candidate.items() if k != "evidence_rounds"}
    confirmed = set(quoted_rounds({**user_candidate, "evidence": backing}, rounds))
    assistant_records = quoted_rounds(candidate, rounds, role="assistant")
    integrations = {
        item["integration_id"] for item in rounds
        if item.get("round") in confirmed and item.get("integration_id")
    }
    return sorted({
        item["round"] for item in rounds if item.get("round") in assistant_records
        and (item["integration_id"] in integrations if item.get("integration_id")
             else item["round"] in confirmed)
    })


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
