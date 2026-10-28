from __future__ import annotations

import copy
import io
import json
import urllib.error
from typing import Any
from unittest.mock import patch

import pytest

from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import chat_request_to_kemo, kemo_request_to_chat
from provider.chat_compat import (
    CHAT_REASONING_PLACEHOLDER, CHAT_RAW_STATE_KEY, CHAT_STATE_KEY,
    ChatCompatibility, chat_url,
)
from provider.openai_chat import OpenAIChatTransport
from provider.protocol.models import KemoResponse, ReasoningItem, TextContent, ToolCallItem, ToolResultItem
from provider.schema import ChatRequest, ProviderError
from run.conversation import events_for_protocol_response
from tests.provider.test_provider_protocol import FakeHTTPResponse
from tests.type_assertions import http_headers


def config(transport: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "base_url": "https://relay.test/proxy/v1", "api_key": "offline",
        "model": "vendor/model",
    }
    if transport is not None:
        result["capabilities"] = {"transport": transport}
    return result


def request(**extra: Any) -> ChatRequest:
    return ChatRequest(
        model="vendor/model", messages=[{"role": "user", "content": "hello"}],
        extra={"reasoning_enabled": True, "reasoning_effort": "medium", **extra},
    )


def reply(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {"id": "response-test", "choices": [{"message": message, "finish_reason": finish}]}


def response(value: dict[str, Any]) -> FakeHTTPResponse:
    return FakeHTTPResponse(json.dumps(value).encode(), content_type="application/json")


def sse(*deltas: dict[str, Any]) -> FakeHTTPResponse:
    frames = [json.dumps({"choices": [{"delta": delta}]}) for delta in deltas]
    frames.append(json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}))
    return FakeHTTPResponse(
        "".join(f"data: {frame}\n\n" for frame in frames).encode() + b"data: [DONE]\n\n",
        content_type="text/event-stream",
    )


CALL = {"id": "echo-call", "type": "function",
        "function": {"name": "echo", "arguments": '{"value":"ok"}'}}
DETAILS = [
    {"type": "reasoning.text", "text": "Observed reasoning.", "signature": "signed-original", "index": 0},
    {"type": "reasoning.encrypted", "data": "opaque-original", "format": "vendor-v1", "index": 1},
]


def test_strict_endpoint_accepts_clean_payload_and_missing_reasoning() -> None:
    captured: list[dict[str, Any]] = []
    def strict_endpoint(req, **kwargs):
        payload = json.loads(req.data)
        captured.append(payload)
        for field in ("reasoning_enabled", "reasoning_effort", "metadata"):
            if field in payload:
                raise urllib.error.HTTPError(req.full_url, 400, "unknown parameter", http_headers({}), io.BytesIO())
        return response(reply({"content": "OK"}))
    wire = request(metadata={"session_id": "local-only"})
    original = copy.deepcopy(wire)
    with patch("urllib.request.urlopen", side_effect=strict_endpoint):
        result = OpenAIChatTransport(config()).chat(wire)
    assert len(captured) == 1
    assert captured[0]["model"] == wire.model
    assert result.text == "OK" and result.reasoning_synthetic
    assert result.reasoning == CHAT_REASONING_PLACEHOLDER
    assert wire == original


@pytest.mark.parametrize(("policy", "expected"), [
    ({}, {}),
    ({"request_format": "none"}, {}),
    ({"enabled_field": "enable_thinking"}, {"enable_thinking": True}),
    ({"effort_field": "reasoning_effort"}, {"reasoning_effort": "medium"}),
    ({"request_format": "effort"}, {"reasoning_effort": "medium"}),
    ({"request_format": "fields"}, {"reasoning_enabled": True, "reasoning_effort": "medium"}),
    ({"request_format": "openrouter"}, {"reasoning": {"enabled": True, "effort": "medium"}}),
    ({"effort_field": "effort", "effort_map": {"medium": "balanced"}}, {"effort": "balanced"}),
])
def test_only_declared_reasoning_controls_are_sent(policy, expected) -> None:
    payload = OpenAIChatTransport(config({"reasoning": policy}))._payload(request(), stream=False)
    assert {key: value for key, value in payload.items() if key not in {"model", "messages", "stream"}} == expected


@pytest.mark.parametrize("wrapped", [False, True])
def test_explicit_disabled_thinking_and_wrapped_profile_are_honored(wrapped) -> None:
    cfg = config({"reasoning": {"enabled_field": "enable_thinking"}, "metadata_supported": True})
    if wrapped:
        cfg["capabilities"] = {**cfg["capabilities"], "capabilities": {}}
    payload = OpenAIChatTransport(cfg)._payload(
        request(reasoning_enabled=False, metadata={"tag": "keep"}), stream=False,
    )
    assert payload["enable_thinking"] is False
    assert payload["metadata"] == {"tag": "keep"}
    assert "reasoning_effort" not in payload


@pytest.mark.parametrize("field", ["reasoning_content", "reasoning", "reasoning_details"])
@pytest.mark.parametrize("stream", [False, True])
def test_reasoning_aliases_preserve_real_text(field, stream) -> None:
    value = [{"type": "reasoning.text", "text": "real-source", "index": 0}] if field == "reasoning_details" else "real-source"
    message = {"content": "OK", field: value}
    transport = OpenAIChatTransport(config())
    with patch("urllib.request.urlopen", return_value=sse(message) if stream else response(reply(message))):
        if stream:
            events = list(transport.chat_stream(request()))
            assert "".join(e.content for e in events if e.type == "reasoning_delta") == "real-source"
            assert not events[-1].metadata["reasoning_synthetic"]
        else:
            parsed = transport.chat(request())
            assert parsed.reasoning == "real-source"
            assert not parsed.reasoning_synthetic


@pytest.mark.parametrize("stream", [False, True])
def test_generic_relay_envelope_and_content_blocks(stream) -> None:
    message = {"content": [{"type": "text", "text": "first"}, {"type": "output_text", "text": "second"}]}
    wire = {"data": {**reply(message), "usage": {"input_tokens": 4, "output_tokens": 2}}}
    transport = OpenAIChatTransport(config())
    with patch("urllib.request.urlopen", return_value=response(wire)) as opened:
        if stream:
            events = list(transport.chat_stream(request()))
            assert "".join(e.content for e in events if e.type == "text_delta") == "firstsecond"
            assert events[-1].usage is not None and events[-1].usage["total_tokens"] == 6
        else:
            result = transport.chat(request())
            assert result.text == "firstsecond" and result.usage.total_tokens == 6
        opened.assert_called_once()


@pytest.mark.parametrize("wire", [
    {"error": {"message": "failed"}, "data": reply({"content": "must not accept"})},
    {"success": False, "data": reply({"content": "must not accept"})},
    {"data": {"data": reply({"content": "must not accept"})}},
    {"data": {"choices": []}}, {"choices": [None]},
    {"choices": [{"message": None}]}, {"choices": [{"message": {}}]},
    {**reply({"content": "must not accept"}), "success": False},
])
def test_envelope_errors_and_empty_outputs_are_not_fabricated_as_success(wire) -> None:
    with pytest.raises(ProviderError):
        OpenAIChatTransport(config())._response(wire, request())


@pytest.mark.parametrize(("base", "expected"), [
    ("https://relay.test/v1", "https://relay.test/v1/chat/completions"),
    ("https://relay.test/proxy/v1/", "https://relay.test/proxy/v1/chat/completions"),
    ("https://relay.test/v1/chat/completions", "https://relay.test/v1/chat/completions"),
    ("https://relay.test/v1/?region=test", "https://relay.test/v1/chat/completions?region=test"),
])
def test_endpoint_path_is_preserved_without_duplicate_completion_suffix(base, expected) -> None:
    assert chat_url(base) == expected


def native_request():
    wire = request()
    wire.tools = [{"type": "function", "function": {
        "name": "echo", "parameters": {"type": "object", "properties": {"value": {"type": "string"}}},
    }}]
    return chat_request_to_kemo(wire)


@pytest.mark.parametrize("stream", [False, True])
def test_signed_state_survives_tools_durable_history_and_api_continuation(stream) -> None:
    cfg = config()
    cfg["capabilities"] = {
        "provider_id": "chat-adapter", "streaming": True,
        "tools": {"function_calling": True, "parallel_calls": True},
        "reasoning": {"supported": True, "efforts": ["medium"],
                      "returns": ["none", "auto", "content"], "persisted_state": True},
        "transport": {"reasoning": {"request_format": "effort"}},
    }
    bridge = ChatBridgeProvider(cfg)
    native = native_request()
    captured: list[dict[str, Any]] = []
    first = sse(
        {"reasoning_details": [{"type": "reasoning.text", "text": "Observed ", "signature": "signed-", "index": 0}]},
        {"reasoning_details": [{"type": "reasoning.text", "text": "reasoning.", "signature": "original", "index": 0}]},
        {"reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque-", "format": "vendor-v1", "index": 1}]},
        {"reasoning_details": [{"type": "reasoning.encrypted", "data": "original", "index": 1}]},
        {"content": "Checking.", "tool_calls": [CALL]},
    ) if stream else response(reply({"content": "Checking.", "reasoning_details": DETAILS, "tool_calls": [CALL]}, "tool_calls"))
    with patch("urllib.request.urlopen", return_value=first):
        if stream:
            terminal = list(bridge.stream(native))[-1]
            assert terminal.response is not None
            result = terminal.response
            assert isinstance(result, KemoResponse)
        else:
            result = bridge.create(native)
    durable = list(events_for_protocol_response(result))[-1]
    assert "opaque-original" not in json.dumps(durable.to_dict())
    saved = KemoResponse.model_validate(durable.internal["provider_response"])
    thinking = next(item for item in saved.output if isinstance(item, ReasoningItem))
    assert thinking.provider_state is not None
    call = next(item for item in saved.output if isinstance(item, ToolCallItem))
    followup = native.model_copy(update={"input": [
        *native.input, *saved.output,
        ToolResultItem(id="result_test", call_id=call.call_id, name=call.name, content=[TextContent(text="ok")]),
    ]})
    def continuation(req, **kwargs):
        payload = json.loads(req.data)
        captured.append(payload)
        assistants = [item for item in payload["messages"] if item["role"] == "assistant"]
        assert len(assistants) == 1
        assert assistants[0]["reasoning_details"] == DETAILS
        assert assistants[0]["content"] == "Checking."
        assert assistants[0]["tool_calls"][0]["id"] == payload["messages"][-1]["tool_call_id"]
        assert CHAT_STATE_KEY not in assistants[0]
        return response(reply({"content": "OK"}))
    with patch("urllib.request.urlopen", side_effect=continuation):
        completed = bridge.create(followup)
    assert completed.status == "completed" and len(captured) == 1


def test_opaque_reasoning_is_not_replaced_with_a_fake_signature() -> None:
    details = [{"type": "reasoning.encrypted", "data": "original-ciphertext", "index": 0}]
    transport = OpenAIChatTransport(config())
    with patch("urllib.request.urlopen", return_value=response(reply({"tool_calls": [CALL], "reasoning_details": details}, "tool_calls"))):
        parsed = transport.chat(request())
    assert parsed.reasoning_synthetic
    assert parsed.raw is not None and parsed.raw[CHAT_RAW_STATE_KEY] is not None


@pytest.mark.parametrize("change", ["model", "endpoint", "corrupt"])
def test_continuation_state_cannot_leak_to_another_route(change) -> None:
    policy = ChatCompatibility(config()["base_url"], {})
    state = policy.state({"reasoning_details": DETAILS}, "vendor/model")
    assert state is not None
    original = {"role": "assistant", "content": "Checking.", CHAT_STATE_KEY: state}
    if change == "model":
        model = "vendor/other"
    else:
        model = "vendor/model"
    if change == "endpoint":
        policy = ChatCompatibility("https://other.test/v1", {})
    if change == "corrupt":
        state["data"] = "not-valid-state"
    payload = policy.payload({"model": model, "messages": [original]},
                             default_effort="medium", enabled_field="reasoning_enabled", effort_field="reasoning_effort")
    assert "reasoning_details" not in payload["messages"][0]
    assert CHAT_STATE_KEY not in payload["messages"][0]
    assert CHAT_STATE_KEY in original


def test_generic_local_placeholders_are_not_sent_in_history() -> None:
    policy = ChatCompatibility(config()["base_url"], {"reasoning": {"history_field": "reasoning_content"}})
    wire = {"messages": [{"role": "assistant", "content": "OK", "reasoning_content": CHAT_REASONING_PLACEHOLDER}]}
    payload = policy.payload(wire, default_effort="medium", enabled_field="reasoning_enabled", effort_field="reasoning_effort")
    assert "reasoning_content" not in payload["messages"][0]


@pytest.mark.parametrize("request_format", ["none", "fields", "effort", "openrouter"])
@pytest.mark.parametrize("field", ["enabled_field", "effort_field"])
def test_reasoning_controls_cannot_overwrite_the_model_field(request_format, field) -> None:
    with pytest.raises(ProviderError, match="无效"):
        OpenAIChatTransport(config({"reasoning": {"request_format": request_format, field: "model"}}))


def test_credential_shaped_state_is_not_hidden_inside_compressed_history() -> None:
    policy = ChatCompatibility(config()["base_url"], {})
    with pytest.raises(ProviderError, match="凭据"):
        policy.state({"reasoning_details": [{"type": "reasoning.text", "text": "api_key=secret-value"}]}, "vendor/model")


def test_plain_reasoning_replay_requires_an_explicit_profile() -> None:
    transport = {"reasoning": {"history_field": "reasoning_content"}}
    cfg = config(transport)
    cfg["capabilities"] = {"transport": transport}
    bridge = ChatBridgeProvider(cfg)
    # The transport-only profile need not invent wire capability declarations.
    native = native_request()
    parsed = bridge._transport._response(reply({"content": "OK", "reasoning_content": "source"}), request())
    from provider.adapters.compat import chat_response_to_kemo
    converted = chat_response_to_kemo(parsed, native)
    mapped = kemo_request_to_chat(native.model_copy(update={"input": converted.output}))
    payload = bridge._transport._payload(mapped, stream=False)
    assert payload["messages"][0]["reasoning_content"] == "source"


def test_signed_stream_blocks_are_private_even_when_text_shares_the_frame() -> None:
    transport = OpenAIChatTransport(config())
    with patch("urllib.request.urlopen", return_value=sse({"content": "OK", "reasoning_details": DETAILS})):
        events = list(transport.chat_stream(request()))
    public = json.dumps([event.to_dict() for event in events])
    assert "opaque-original" not in public
    assert "signed-original" not in public
    assert events[-1].internal[CHAT_RAW_STATE_KEY] is not None


def test_signed_state_over_capacity_fails_before_publishing_executable_tools() -> None:
    import random
    oversized = random.Random(20261018).randbytes(12000).hex()
    details = [{"type": "reasoning.encrypted", "data": oversized, "index": 0}]
    transport = OpenAIChatTransport(config())
    observed = []
    with patch("urllib.request.urlopen", return_value=sse({"reasoning_details": details, "tool_calls": [CALL]})):
        with pytest.raises(ProviderError, match="容量"):
            for event in transport.chat_stream(request()):
                observed.append(event)
    assert not any(event.type in {"tool_call_start", "done"} for event in observed)


def test_openrouter_budget_does_not_add_a_conflicting_effort() -> None:
    transport = OpenAIChatTransport(config({"reasoning": {"request_format": "openrouter"}}))
    payload = transport._payload(request(reasoning={"max_tokens": 2048}), stream=False)
    assert payload["reasoning"] == {"enabled": True, "max_tokens": 2048}


def test_sse_relay_envelope_is_unwrapped_once() -> None:
    frame = {"data": {"choices": [{"delta": {"content": "OK", "reasoning": "source"}, "finish_reason": "stop"}]}}
    body = f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n".encode()
    with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(body, content_type="text/event-stream")):
        events = list(OpenAIChatTransport(config()).chat_stream(request()))
    assert "".join(event.content for event in events if event.type == "text_delta") == "OK"
    assert "".join(event.content for event in events if event.type == "reasoning_delta") == "source"
