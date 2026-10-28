"""Automatic Chat profiles tested through final HTTP bodies, not gates alone."""
from __future__ import annotations

import copy
import json
from typing import Any
from unittest.mock import patch

import pytest

from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import chat_request_to_kemo
from provider.chat_profiles import resolve_chat_profile
from provider.protocol.errors import CapabilityError, ProtocolValidationError
from provider.protocol.models import (
    AudioContent, ContentBlock, FileContent, ImageContent, KemoRequest, KemoResponse,
    MediaSource, MessageItem, MessageRole, ProviderState, ReasoningConfig, ReasoningItem,
    TextContent, ToolCallItem, ToolResultItem, VideoContent,
)
from provider.schema import ChatRequest
from run.extensions.model_capabilities import resolve_reasoning_selection
from tests.provider.test_provider_protocol import FakeHTTPResponse


def config(model: str = "unknown") -> dict[str, Any]:
    return {"type": "chat", "base_url": "https://api.deepseek.com/v1" if model.startswith("deepseek-")
            else "https://relay.test/proxy/v1", "model": model,
            "api_key": "offline-only", "reasoning_effort": "medium"}


def request(model: str = "unknown", **updates: Any) -> KemoRequest:
    native = chat_request_to_kemo(ChatRequest(model=model,
        messages=[{"role": "user", "content": "lookup"}],
        tools=[{"type": "function", "function": {"name": "echo",
                 "parameters": {"type": "object", "properties": {"value": {"type": "string"}}}}}]))
    return native.model_copy(update=updates)


def response(message: dict[str, Any], *, stream: bool, tool: bool = False) -> FakeHTTPResponse:
    finish = "tool_calls" if tool else "stop"
    if stream:
        frames = [{"choices": [{"delta": message}]},
                  {"choices": [{"delta": {}, "finish_reason": finish}]}]
        data = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
        return FakeHTTPResponse(data.encode(), content_type="text/event-stream")
    return FakeHTTPResponse(json.dumps({"choices": [{"message": message, "finish_reason": finish}]}).encode(),
                            content_type="application/json")


@pytest.mark.parametrize("model", ["unknown", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner", "deepseek-r1-old"])
def test_resolution_never_probes_or_mutates_configuration(model: str) -> None:
    cfg = config(model)
    original = copy.deepcopy(cfg)
    with patch("urllib.request.urlopen", side_effect=AssertionError("unexpected probe")):
        bridge = ChatBridgeProvider(cfg)
        bridge.validate(request(model))
        caps = bridge.capabilities(model)
        assert caps.tools.function_calling and not caps.tools.multimodal_results
        assert not caps.reasoning.summary and not caps.reasoning.persisted_state
        assert not caps.structured_output.strict
    assert cfg == original and "capabilities" not in cfg


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("model", ["unknown", "deepseek-flash", "deepseek-v4-pro"])
def test_automatic_profile_full_tool_roundtrip_on_final_wire(model: str, stream: bool) -> None:
    cfg = config(model)
    bridge = ChatBridgeProvider(cfg)
    native = request(model)
    selected = resolve_reasoning_selection({}, cfg, bridge)
    if selected.enabled:
        from provider.protocol.models import ReasoningConfig
        native = native.model_copy(update={"reasoning": ReasoningConfig(enabled=True, effort=selected.effort or "medium")})
    bodies: list[dict[str, Any]] = []

    def endpoint(req: Any, **kwargs: Any) -> FakeHTTPResponse:
        payload = json.loads(req.data)
        bodies.append(payload)
        assert req.full_url.endswith("/chat/completions")
        assert payload["model"] == model and payload["tools"]
        assert "reasoning_enabled" not in payload and "metadata" not in payload
        if model.startswith("deepseek-"):
            assert payload["thinking"] == {"type": "enabled"}
            assert payload["reasoning_effort"] == "high"  # logical medium -> wire high
        else:
            assert not selected.enabled and "reasoning_effort" not in payload
        if len(bodies) == 1:
            return response({"content": "Checking", "reasoning_content": "Observed thought" if selected.enabled else "",
                             "tool_calls": [{"index": 0, "id": "wire-call", "type": "function",
                                             "function": {"name": "echo", "arguments": '{"value":"ok"}'}}]},
                            stream=stream, tool=True)
        assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
        tool_messages = [m for m in assistants if m.get("tool_calls")]
        assert len(tool_messages) == 1
        assert tool_messages[0]["tool_calls"][0]["id"] == payload["messages"][-1]["tool_call_id"]
        assert payload["messages"][-1]["content"] == "ok"
        if selected.enabled:
            assert len(assistants) == 1 and tool_messages[0]["reasoning_content"] == "Observed thought"
        return response({"content": "Complete"}, stream=stream)

    def invoke(req: KemoRequest) -> KemoResponse:
        if not stream:
            return bridge.create(req)
        result = list(bridge.stream(req))[-1].response
        assert isinstance(result, KemoResponse)
        return result

    with patch("urllib.request.urlopen", side_effect=endpoint):
        first = invoke(native)
        assert first.status == "requires_action"
        call = next(item for item in first.output if isinstance(item, ToolCallItem))
        result = ToolResultItem(id="result_echo", call_id=call.call_id, name=call.name,
                                content=[TextContent(text=call.arguments["value"])])
        followup = native.model_copy(update={"input": [*native.input, *first.output, result]})
        finished = invoke(followup)
        assert finished.status == "completed"
    assert len(bodies) == 2


@pytest.mark.parametrize("mode", ["auto", "none", "required", "named"])
def test_unknown_basic_tool_modes_and_generation_reach_http(mode: str) -> None:
    bridge = ChatBridgeProvider(config())
    req = request()
    req = req.model_copy(update={"tool_choice": req.tool_choice.model_copy(update={
        "mode": mode, "name": "echo" if mode == "named" else None}),
        "generation": req.generation.model_copy(update={"temperature": 0.7, "top_p": 0.9})})
    with patch("urllib.request.urlopen", return_value=response({"content": "ok"}, stream=False)) as http:
        bridge.create(req)
    wire = json.loads(http.call_args.args[0].data)
    assert wire["temperature"] == 0.7 and wire["top_p"] == 0.9
    assert wire["tool_choice"] == ({"type": "function", "function": {"name": "echo"}} if mode == "named" else mode)


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_deepseek_constraints_fail_before_http(model: str) -> None:
    bridge = ChatBridgeProvider(config(model))
    req = request(model)
    req = req.model_copy(update={"tool_choice": req.tool_choice.model_copy(update={"mode": "required"})})
    with patch("urllib.request.urlopen") as http, pytest.raises(CapabilityError, match="tool_choice"):
        bridge.create(req)
    http.assert_not_called()


@pytest.mark.parametrize("wrapped", [False, True])
def test_explicit_tool_denial_has_priority_and_does_not_mutate(wrapped: bool) -> None:
    cfg = config("deepseek-flash")
    data = {"tools": {"function_calling": False}}
    cfg["capabilities"] = {"capabilities": data} if wrapped else data
    before = copy.deepcopy(cfg)
    with pytest.raises(CapabilityError, match="工具调用"):
        ChatBridgeProvider(cfg).validate(request("deepseek-flash"))
    assert cfg == before


def test_model_switch_does_not_reuse_main_model_policy() -> None:
    bridge = ChatBridgeProvider(config("deepseek-flash"))
    assert bridge.capabilities("deepseek-flash").reasoning.supported
    assert not bridge.capabilities("other-model").reasoning.supported
    assert bridge.capabilities("other-model").input_modalities == ["text"]
    assert bridge.capabilities("deepseek-v4-pro").input_modalities == ["text"]
    assert bridge.capabilities("deepseek-flash").input_modalities == ["text", "image"]
    with patch("urllib.request.urlopen", return_value=response({"content": "ok"}, stream=False)) as http:
        bridge.create(request("other-model"))
    wire = json.loads(http.call_args.args[0].data)
    assert wire["model"] == "other-model" and wire["thinking"]["type"] == "disabled"


def test_unmapped_explicit_reasoning_is_not_falsely_advertised() -> None:
    cfg = config()
    cfg["capabilities"] = {"reasoning": {"supported": True, "efforts": ["high"]}}
    caps = ChatBridgeProvider(cfg).capabilities("unknown")
    assert not caps.reasoning.supported and caps.extensions["reasoning_unmapped"]


@pytest.mark.parametrize("url,base,expected", [
    ("https://relay.test/proxy/api", "https://relay.test/proxy/api", "https://relay.test/proxy/api/chat/completions"),
    ("https://relay.test/proxy/chat/completions?region=a", "https://relay.test/proxy/chat/completions?region=a", "https://relay.test/proxy/chat/completions?region=a"),
    ("https://api.deepseek.com/v1?region=a", "https://api.deepseek.com/v1?region=a", "https://api.deepseek.com/chat/completions?region=a"),
    ("https://chat.test", "https://chat.test/v1", "https://chat.test/v1/chat/completions"),
    ("https://api.deepseek.com?region=a", "https://api.deepseek.com/v1?region=a", "https://api.deepseek.com/chat/completions?region=a"),
])
def test_runtime_configuration_preserves_routes(url: str, base: str, expected: str) -> None:
    from run.config import provider_runtime_config
    cfg = provider_runtime_config({"provider": {**config("deepseek-flash"), "base_url": url}})
    bridge = ChatBridgeProvider(cfg)
    assert cfg["base_url"] == base
    assert bridge._transport._url() == expected


def test_relay_exact_alias_is_labelled_not_claimed_as_verified_endpoint() -> None:
    cfg = {**config("deepseek-flash"), "base_url": "https://relay.test/proxy/v1"}
    caps = ChatBridgeProvider(cfg).capabilities("deepseek-flash")
    assert caps.extensions["capability_verification"] == "assumed-relay"
    assert caps.reasoning.supported


def test_raw_media_tool_result_is_rejected_not_stringified() -> None:
    req = request("deepseek-flash")
    content: list[ContentBlock] = [ImageContent(source=MediaSource(kind="url", uri="https://example.invalid/p.png"))]
    req = req.model_copy(update={"input": [*req.input,
        ToolCallItem(id="call_image", call_id="callid_image", name="echo"),
        ToolResultItem(id="result_image", call_id="callid_image", name="echo", content=content)
    ]})
    with pytest.raises(CapabilityError, match="媒体输入队列"):
        ChatBridgeProvider(config("deepseek-flash")).validate(req)


@pytest.mark.parametrize("options", [{"vendor.test.minimum": 0.1}, {"reasoning_effort": "high"}, {"tool_choice": "required"}])
def test_automatic_profiles_do_not_grant_option_side_channels(options: dict[str, Any]) -> None:
    bridge = ChatBridgeProvider(config("deepseek-flash"))
    with patch("urllib.request.urlopen") as http, pytest.raises(ProtocolValidationError):
        bridge.create(request("deepseek-flash", provider_options=options))
    http.assert_not_called()


@pytest.mark.parametrize("media_type", [ImageContent, AudioContent, VideoContent, FileContent])
@pytest.mark.parametrize("stream", [False, True])
def test_unknown_advanced_media_is_rejected_before_http(media_type: Any, stream: bool) -> None:
    bridge = ChatBridgeProvider(config())
    content = media_type(source=MediaSource(kind="url", uri="https://example.invalid/media"))
    req = request(input=[MessageItem(id="msg_media", role=MessageRole.USER, content=[content])])
    with patch("urllib.request.urlopen") as http, pytest.raises(CapabilityError):
        if stream:
            list(bridge.stream(req))
        else:
            bridge.create(req)
    http.assert_not_called()


@pytest.mark.parametrize("model", ["unknown", "deepseek-flash"])
@pytest.mark.parametrize("shape", ["summary", "all_turns", "persisted_state"])
def test_automatic_profile_does_not_invent_advanced_reasoning(model: str, shape: str) -> None:
    bridge = ChatBridgeProvider(config(model))
    req = request(model)
    if shape == "persisted_state":
        req = req.model_copy(update={"input": [*req.input, ReasoningItem(
            id="rs_prior", provider_state=ProviderState(kind="opaque", provider="test", data="offline"))]})
    else:
        reasoning = ReasoningConfig.model_validate({"enabled": True, "effort": "medium",
            **({"return": "summary"} if shape == "summary" else {"context": "all_turns"})})
        req = req.model_copy(update={"reasoning": reasoning})
    with patch("urllib.request.urlopen") as http, pytest.raises(CapabilityError):
        bridge.create(req)
    http.assert_not_called()
