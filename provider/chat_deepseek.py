"""DeepSeek-specific wire compatibility for the Chat adapter.

This module is deliberately separate from provider selection and the Kemo
protocol.  It only contains transformations used by the OpenAI-compatible
Chat transport when its local vendor detector identifies DeepSeek.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEEPSEEK_REASONING_PLACEHOLDER = (
    "[DeepSeek compatibility placeholder: upstream reasoning was not provided.]"
)
_DEEPSEEK_REASONING_EFFORTS = {
    "minimal": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "max": "max",
    "xhigh": "high",
    "ultra": "max",
}
_DEEPSEEK_UNSUPPORTED_PAYLOAD_FIELDS = frozenset(
    {
        "allowed_tools",
        "metadata",
        "prompt_cache_key",
        "safety_identifier",
        "store",
    }
)


def is_official_deepseek_url(base_url: str) -> bool:
    return (urlsplit(str(base_url)).hostname or "").casefold() == "api.deepseek.com"


def is_deepseek_chat(config: dict[str, Any]) -> bool:
    """Return whether a Chat adapter config targets DeepSeek."""

    base_url = str(config.get("base_url") or "")
    model = str(config.get("model") or "").strip().casefold()
    return is_official_deepseek_url(base_url) or model.startswith("deepseek-")


def deepseek_reasoning_effort(value: Any) -> str:
    """Map Kemo's logical effort names to DeepSeek's accepted values."""

    candidate = str(value or "").strip().casefold()
    if candidate == "none":
        return "none"
    return _DEEPSEEK_REASONING_EFFORTS.get(candidate, "high")


def deepseek_chat_url(base_url: str) -> str:
    """Normalize official DeepSeek URLs while preserving relay API prefixes."""

    parts = urlsplit(str(base_url))
    path = parts.path.rstrip("/")
    if is_official_deepseek_url(base_url) and path == "/v1":
        path = ""
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def deepseek_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept a relay's data envelope only when it contains a Chat response."""

    if "choices" in payload or payload.get("error") or payload.get("success") is False:
        return payload
    nested = payload.get("data")
    if not isinstance(nested, dict) or nested.get("error"):
        return payload
    choices = nested.get("choices")
    if (
        isinstance(choices, list)
        and choices
        and isinstance(choices[0], dict)
        and isinstance(choices[0].get("message"), dict)
    ):
        return nested
    return payload


def deepseek_payload(
    payload: dict[str, Any],
    *,
    reasoning_enabled_field: str,
    reasoning_effort_field: str,
    default_reasoning_effort: str,
) -> dict[str, Any]:
    """Translate internal Chat fields into a DeepSeek request payload."""

    configured_enabled = payload.pop(reasoning_enabled_field, None)
    if configured_enabled is None:
        configured_enabled = payload.pop("reasoning_enabled", None)
    configured_effort = payload.pop(reasoning_effort_field, None)
    if configured_effort is None:
        configured_effort = payload.pop("reasoning_effort", None)

    enabled = bool(configured_enabled) if configured_enabled is not None else True
    if str(configured_effort or "").strip().casefold() == "none":
        enabled = False
    effort = (
        deepseek_reasoning_effort(
            configured_effort or default_reasoning_effort
        )
        if enabled
        else "none"
    )
    payload["thinking"] = {"type": "enabled" if enabled else "disabled"}
    payload["reasoning_effort"] = effort

    # Legacy histories and relay responses may contain assistant messages
    # without a reasoning field.  The wire field must still be present when
    # tools are enabled. The fixed, explicitly labelled placeholder is adapter
    # compatibility data, never a claim about the model's actual thoughts.
    messages = payload.get("messages")
    if enabled and payload.get("tools") and isinstance(messages, list):
        payload["messages"] = [
            {
                **message,
                "reasoning_content": (
                    message.get("reasoning_content") or DEEPSEEK_REASONING_PLACEHOLDER
                ),
            }
            if isinstance(message, dict) and message.get("role") == "assistant"
            else message
            for message in messages
        ]

    # Keep cleanup scoped to DeepSeek so other Chat providers are unchanged.
    for key in _DEEPSEEK_UNSUPPORTED_PAYLOAD_FIELDS:
        payload.pop(key, None)
    if enabled:
        for key in ("temperature", "presence_penalty", "frequency_penalty"):
            payload.pop(key, None)
    return payload
