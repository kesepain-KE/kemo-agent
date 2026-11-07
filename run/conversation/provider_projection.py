"""Bounded Provider event projections; CardStream stores the full typed response."""
from __future__ import annotations

import json
from typing import Any

from provider.protocol.diagnostics import redact_diagnostic_text, sanitize_provider_diagnostic
from provider.protocol.models import (
    KemoResponse, MessageItem, ProviderState, RefusalContent, TextContent, ToolCallItem,
)


_MAX_DURABLE_RESPONSE_CHARS = 256_000
_MAX_DURABLE_ITEM_CHARS = 32_000
_MAX_DURABLE_PROVIDER_STATE_CHARS = 8_192


def final_answer_messages(response: KemoResponse) -> list[MessageItem]:
    """A request terminal alone is not a final assistant deliverable."""
    if response.status != "completed" or any(isinstance(item, ToolCallItem) for item in response.output):
        return []
    messages = [
        item for item in response.output
        if isinstance(item, MessageItem) and item.role == "assistant"
        and item.status == "completed" and item.phase == "final_answer"
        and (str(item.refusal or "").strip() or any(
            not isinstance(block, (TextContent, RefusalContent)) or block.text.strip()
            for block in item.content
        ))
    ]
    return messages


def _bounded_durable_text(value: Any) -> str:
    text = redact_diagnostic_text(str(value or ""))
    if len(text) <= _MAX_DURABLE_ITEM_CHARS:
        return text
    return text[:_MAX_DURABLE_ITEM_CHARS] + "…(历史内容已截断)"


def _bounded_durable_json(value: Any) -> Any:
    bounded = sanitize_provider_diagnostic(value)
    try:
        encoded = json.dumps(
            bounded,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError, RecursionError):
        return {"content_omitted": True, "reason": "durable_diagnostic_error"}
    if len(encoded) <= _MAX_DURABLE_ITEM_CHARS:
        return bounded
    return {
        "content_omitted": True,
        "reason": "durable_item_limit",
        "payload_length": len(encoded),
    }


def _durable_provider_state(value: Any) -> dict[str, Any] | None:
    """Keep the minimum bounded state needed for native reasoning replay.

    Provider state is executable continuation data, not a general diagnostic
    tree.  Only the protocol fields are retained; the opaque payload is
    redacted and bounded so arbitrary metadata or credential-shaped text cannot
    expand durable history without a limit.
    """

    if not isinstance(value, dict):
        return None
    kind = str(value.get("kind") or "").strip()
    provider = str(value.get("provider") or "").strip()
    data = value.get("data")
    if kind not in {"encrypted", "opaque"} or not provider or not isinstance(data, str):
        return None
    safe_data = redact_diagnostic_text(data)
    # Provider continuation state is opaque and cannot be repaired after a
    # partial redaction.  Omit a credential-shaped payload rather than writing
    # a corrupted state that would fail on the next request.
    if safe_data != data:
        return None
    if not safe_data or len(safe_data) > _MAX_DURABLE_PROVIDER_STATE_CHARS:
        return None
    state: dict[str, Any] = {
        "kind": kind,
        "data": safe_data,
        "provider": provider[:160],
    }
    for key in ("model", "version", "expires_at"):
        if value.get(key) is not None:
            state[key] = redact_diagnostic_text(str(value[key]))[:160]
    try:
        ProviderState.model_validate(state)
    except Exception:
        return None
    return state


def _durable_content_blocks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    blocks: list[dict[str, Any]] = []
    for raw in value[:256]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "")
        if kind == "text":
            block = {"type": "text", "text": _bounded_durable_text(raw.get("text"))}
            if raw.get("language") is not None:
                block["language"] = _bounded_durable_text(raw.get("language"))[:128]
            blocks.append(block)
            continue
        if kind == "json":
            blocks.append(
                {
                    "type": "json",
                    "data": _bounded_durable_json(raw.get("data")),
                }
            )
            continue
        if kind in {"image", "audio", "video", "file"}:
            block: dict[str, Any] = {"type": kind}
            for key in (
                "asset_id",
                "mime_type",
                "checksum_sha256",
                "detail",
                "width",
                "height",
                "duration_ms",
                "filename",
            ):
                if raw.get(key) is not None:
                    block[key] = raw[key]
            # Do not persist source.uri/data or any other transient media body.
            blocks.append(block)
            continue
        if kind == "reference":
            block = {"type": "reference", "target_id": str(raw.get("target_id") or "")}
            if raw.get("label") is not None:
                block["label"] = _bounded_durable_text(raw.get("label"))[:256]
            blocks.append(block)
    return blocks


def _durable_item_payload(item: Any) -> dict[str, Any] | None:
    raw = item.model_dump(mode="json", by_alias=True, exclude_none=True)
    kind = str(raw.get("type") or "")
    item_id = str(raw.get("id") or "")
    status = str(raw.get("status") or "completed")
    if not item_id or kind not in {"message", "reasoning", "tool_call", "tool_result"}:
        return None
    base: dict[str, Any] = {"id": item_id, "type": kind, "status": status}
    if kind == "tool_call":
        call_id = str(raw.get("call_id") or "")
        name = str(raw.get("name") or "")
        if not call_id or not name:
            return None
        arguments = _bounded_durable_json(raw.get("arguments") or {})
        base.update(
            {
                "call_id": call_id,
                "name": name,
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
        return base
    if kind == "reasoning":
        for key in ("summary", "content"):
            if key in raw:
                base[key] = _bounded_durable_text(raw[key])
        provider_state = _durable_provider_state(raw.get("provider_state"))
        if provider_state is not None:
            base["provider_state"] = provider_state
        if "token_count" in raw:
            base["token_count"] = raw["token_count"]
        # Only the validated, bounded provider_state projection above is kept;
        # arbitrary reasoning metadata and opaque response extensions are not.
        return base
    if kind == "message":
        role = str(raw.get("role") or "assistant")
        content = _durable_content_blocks(raw.get("content") or [])
        base.update(
            {
                "role": role,
                "content": content,
            }
        )
        if raw.get("phase") in {"commentary", "final_answer"}:
            base["phase"] = raw["phase"]
        return base
    call_id = str(raw.get("call_id") or "")
    name = str(raw.get("name") or "")
    if not call_id or not name:
        return None
    content = _durable_content_blocks(raw.get("content") or [])
    base.update(
        {
            "call_id": call_id,
            "name": name,
            "is_error": bool(raw.get("is_error")),
            "content": content,
        }
    )
    return base


def durable_provider_response_payload(response: KemoResponse) -> dict[str, Any]:
    """Return a bounded event diagnostic, separate from authoritative RAW.

    Public runtime events keep using the bounded diagnostic representation.  The
    internal event projection preserves structural fields because the global budget
    can truncate structural Item fields such as ``type``, ``call_id`` and
    ``name``.  Keeping only the response identity and canonical output Items
    also prevents unrelated Provider metadata from entering conversation
    history through this private channel.
    """

    output: list[dict[str, Any]] = []
    encoded_size = 0
    for item in response.output:
        safe_item = _durable_item_payload(item)
        if safe_item is None:
            continue
        encoded_item = json.dumps(
            safe_item,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        if encoded_size + len(encoded_item) > _MAX_DURABLE_RESPONSE_CHARS:
            if safe_item.get("type") == "tool_call":
                safe_item = {
                    key: value
                    for key, value in safe_item.items()
                    if key != "arguments"
                }
                safe_item["arguments"] = {}
                encoded_item = json.dumps(
                    safe_item,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            else:
                continue
        if encoded_size + len(encoded_item) > _MAX_DURABLE_RESPONSE_CHARS:
            continue
        output.append(safe_item)
        encoded_size += len(encoded_item)
    return {
        "id": redact_diagnostic_text(str(response.id or ""))[:160],
        "request_id": redact_diagnostic_text(str(response.request_id or ""))[:160],
        "status": str(response.status),
        "model": redact_diagnostic_text(str(response.model or ""))[:160],
        "output": output,
    }
