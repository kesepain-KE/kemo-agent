"""Compatibility policy for Chat endpoints; never used by the native gateway."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit
import zlib

from provider.protocol.models import normalize_reasoning_effort
from provider.protocol.diagnostics import redact_diagnostic_text
from provider.schema import ProviderError

CHAT_REASONING_PLACEHOLDER = "[Chat compatibility placeholder: upstream reasoning was not provided.]"
CHAT_STATE_KEY = "_chat_reasoning_state"
CHAT_RAW_STATE_KEY = "__chat_reasoning_state"
_REASONING_FIELDS = ("reasoning_content", "reasoning", "reasoning_details")
_STATE_LIMIT = 8_192
_DECODE_LIMIT = 262_144
_RESERVED_FIELDS = {"model", "messages", "tools", "stream"}


def chat_transport_profile(config: dict[str, Any]) -> dict[str, Any]:
    profile = config.get("capabilities")
    if not isinstance(profile, dict):
        return {}
    nested = profile.get("capabilities")
    if isinstance(nested, dict):
        profile = {**nested, **({"transport": profile["transport"]}
                               if isinstance(profile.get("transport"), dict) else {})}
    transport = profile.get("transport")
    return dict(transport) if isinstance(transport, dict) else {}


def profile_field(value: Any, fallback: str) -> str:
    candidate = str(value or "").strip()
    return candidate if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", candidate) else fallback


def chat_url(base_url: str) -> str:
    parts = urlsplit(base_url)
    path = parts.path.rstrip("/")
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def chat_response_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Unwrap one relay envelope, preserving top-level choices and failures."""
    if "choices" in payload or payload.get("error") or payload.get("success") is False:
        return payload
    nested = payload.get("data")
    if (
        isinstance(nested, dict)
        and isinstance(nested.get("choices"), list)
        and nested["choices"]
        and not nested.get("error")
        and nested.get("success") is not False
    ):
        return nested
    return payload


def chat_reasoning_text(message: dict[str, Any]) -> str:
    for field in ("reasoning_content", "reasoning"):
        value = message.get(field)
        if isinstance(value, str) and value.strip():
            return value
    details = message.get("reasoning_details")
    if not isinstance(details, list):
        return ""
    parts: list[str] = []
    for detail in details:
        if isinstance(detail, dict):
            for key in ("text", "summary"):
                value = detail.get(key)
                if isinstance(value, str):
                    parts.append(value)
    return "".join(parts)


def chat_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"] for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
            and isinstance(block.get("text"), str)
        )
    return ""


def chat_usage(raw: dict[str, Any]) -> dict[str, Any]:
    result = dict(raw)
    for target, source in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
        if target not in result and source in raw:
            result[target] = raw[source]
    return result


def chat_state_from_raw(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    state = raw.get(CHAT_RAW_STATE_KEY) if isinstance(raw, dict) else None
    return state if isinstance(state, dict) else None


def chat_public_frame(frame: dict[str, Any]) -> dict[str, Any]:
    """Keep signed continuation blocks out of public streaming metadata."""
    result = copy.deepcopy(frame)
    for choice in result.get("choices", []):
        if not isinstance(choice, dict):
            continue
        for key in ("delta", "message"):
            message = choice.get(key)
            if isinstance(message, dict):
                message.pop("reasoning_details", None)
    return result


class ChatCompatibility:
    def __init__(self, base_url: str, transport: dict[str, Any]) -> None:
        self.base_url = base_url
        self.transport = transport
        candidate = transport.get("reasoning")
        self.reasoning = candidate if isinstance(candidate, dict) else {}
        self.request_format = self.reasoning.get("request_format") or (
            "fields" if any(key in self.reasoning for key in ("enabled_field", "effort_field"))
            else "none"
        )
        if not isinstance(self.request_format, str) or self.request_format not in {"none", "fields", "effort", "openrouter"}:
            raise ProviderError("Chat reasoning.request_format 无效", category="invalid_config")
        history_field = self.reasoning.get("history_field")
        if history_field is not None and history_field not in _REASONING_FIELDS:
            raise ProviderError("Chat reasoning.history_field 无效", category="invalid_config")
        for name in ("enabled_field", "effort_field"):
            if name in self.reasoning:
                self._field(name, "")
        if self.reasoning.get("enabled_field") and self.reasoning.get("enabled_field") == self.reasoning.get("effort_field"):
            raise ProviderError("Chat 思考开关和档位不能映射到同一字段", category="invalid_config")

    def route(self, model: str) -> str:
        return hashlib.sha256(f"{chat_url(self.base_url)}\n{model}".encode()).hexdigest()

    def _field(self, name: str, default: str) -> str:
        value = str(self.reasoning.get(name) or default)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) or value in _RESERVED_FIELDS:
            raise ProviderError(f"Chat reasoning.{name} 无效", category="invalid_config")
        return value

    def payload(
        self, payload: dict[str, Any], *, default_effort: str,
        enabled_field: str, effort_field: str,
    ) -> dict[str, Any]:
        configured_enabled = payload.pop(enabled_field, None)
        configured_effort = payload.pop(effort_field, None)
        enabled = payload.pop("reasoning_enabled", configured_enabled)
        effort = payload.pop("reasoning_effort", configured_effort)
        enabled = enabled is not False and effort != "none"
        effort = normalize_reasoning_effort(effort or default_effort)
        effort_map = self.reasoning.get("effort_map")
        if isinstance(effort_map, dict) and isinstance(effort_map.get(effort), str):
            effort = effort_map[effort]
        if self.request_format == "fields":
            explicit = any(key in self.reasoning for key in ("enabled_field", "effort_field"))
            if not explicit or "enabled_field" in self.reasoning:
                payload[self._field("enabled_field", "reasoning_enabled")] = enabled
            if not explicit or "effort_field" in self.reasoning:
                payload[self._field("effort_field", "reasoning_effort")] = effort if enabled else "none"
        elif self.request_format == "effort":
            payload[self._field("effort_field", "reasoning_effort")] = effort if enabled else "none"
        elif self.request_format == "openrouter":
            existing = payload.get("reasoning")
            controls = dict(existing) if isinstance(existing, dict) else {}
            controls["enabled"] = enabled
            if enabled and "max_tokens" not in controls:
                controls["effort"] = effort
            if not enabled:
                controls.pop("effort", None)
            payload["reasoning"] = controls
        if self.transport.get("metadata_supported") is not True:
            payload.pop("metadata", None)
        model = str(payload.get("model") or "")
        payload["messages"] = [
            self._message(message, model) if isinstance(message, dict) else message
            for message in payload.get("messages", [])
        ]
        return payload

    def _message(self, original: dict[str, Any], model: str) -> dict[str, Any]:
        message = {key: copy.deepcopy(value) for key, value in original.items()
                   if key not in {*_REASONING_FIELDS, CHAT_STATE_KEY}}
        state = original.get(CHAT_STATE_KEY)
        fields = self._decode_state(state, model) if isinstance(state, dict) else {}
        if message.get("role") == "assistant":
            history_field = self.reasoning.get("history_field")
            if "reasoning_details" in fields:
                message["reasoning_details"] = fields["reasoning_details"]
            elif state is None and isinstance(original.get("reasoning_details"), list):
                message["reasoning_details"] = copy.deepcopy(original["reasoning_details"])
            if history_field:
                value = fields.get(history_field)
                if value is None and state is None:
                    value = original.get(history_field)
                if value and value not in (
                    CHAT_REASONING_PLACEHOLDER, "兼容层已启用默认思考模式。",
                ):
                    message[history_field] = copy.deepcopy(value)
        return message

    def state(self, fields: dict[str, Any], model: str) -> dict[str, Any] | None:
        if not fields:
            return None
        encoded = json.dumps({"route": self.route(model), "fields": fields},
                             ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if redact_diagnostic_text(encoded.decode("utf-8")) != encoded.decode("utf-8"):
            raise ProviderError("Chat 思考续接状态包含不能写入历史的凭据信息",
                                category="invalid_response")
        if len(encoded) > _DECODE_LIMIT:
            raise ProviderError("Chat 思考续接状态超出安全容量，不能截断后继续工具调用",
                                category="invalid_response")
        compressed = base64.b64encode(zlib.compress(encoded)).decode("ascii")
        if len(compressed) > _STATE_LIMIT:
            raise ProviderError("Chat 思考续接状态超出历史容量，不能截断签名",
                                category="invalid_response")
        return {"kind": "opaque", "data": compressed, "provider": "chat-adapter",
                "model": model, "version": "1"}

    def _decode_state(self, state: dict[str, Any], model: str) -> dict[str, Any]:
        if (
            state.get("provider") != "chat-adapter" or state.get("model") != model
            or state.get("kind") != "opaque" or state.get("version") != "1"
        ):
            return {}
        data = state.get("data")
        if not isinstance(data, str) or len(data) > _STATE_LIMIT:
            return {}
        try:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(base64.b64decode(data, validate=True), _DECODE_LIMIT + 1)
            if len(decoded) > _DECODE_LIMIT or not decoder.eof or decoder.unused_data:
                return {}
            value = json.loads(decoded)
        except (ValueError, zlib.error, UnicodeError):
            return {}
        if not isinstance(value, dict) or value.get("route") != self.route(model):
            return {}
        fields = value.get("fields")
        return {key: copy.deepcopy(value) for key, value in fields.items()
                if key in _REASONING_FIELDS} if isinstance(fields, dict) else {}

    def response_fields(self, message: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        details = message.get("reasoning_details")
        if isinstance(details, list) and details:
            result["reasoning_details"] = copy.deepcopy(details)
        history_field = self.reasoning.get("history_field")
        if history_field and message.get(history_field):
            result[history_field] = copy.deepcopy(message[history_field])
        return result


class ChatReasoningAccumulator:
    """Reassemble real Chat reasoning fragments, including opaque signatures."""
    def __init__(self, policy: ChatCompatibility) -> None:
        self.policy = policy
        self.fields: dict[str, Any] = {}
        self.details: dict[int, dict[str, Any]] = {}

    def add(self, delta: dict[str, Any]) -> str:
        for key in ("reasoning_content", "reasoning"):
            value = delta.get(key)
            if isinstance(value, str):
                self.fields[key] = str(self.fields.get(key) or "") + value
        fragments = delta.get("reasoning_details")
        if isinstance(fragments, list):
            for position, fragment in enumerate(fragments):
                if not isinstance(fragment, dict):
                    continue
                index = fragment.get("index", position)
                if not isinstance(index, int) or isinstance(index, bool) or index < 0:
                    continue
                target = self.details.setdefault(index, {})
                for key, value in fragment.items():
                    if key in {"text", "summary", "data", "signature"} and isinstance(value, str):
                        target[key] = str(target.get(key) or "") + value
                    elif key not in target:
                        target[key] = copy.deepcopy(value)
                    elif target[key] != value:
                        raise ProviderError("Chat 思考续接片段标识发生冲突", category="invalid_response")
        return chat_reasoning_text(delta)

    def state(self, model: str) -> dict[str, Any] | None:
        message = dict(self.fields)
        if self.details:
            message["reasoning_details"] = [self.details[index] for index in sorted(self.details)]
        return self.policy.state(self.policy.response_fields(message), model)
