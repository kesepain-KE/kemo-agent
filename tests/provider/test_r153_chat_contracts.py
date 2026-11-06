"""R4: Chat must adapt to the native contract without weakening it."""
from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Generator
from unittest.mock import patch

import pytest

from events import RunEvent
from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.adapters.compat import (
    _content_blocks,
    chat_request_to_kemo,
    chat_response_to_kemo,
    chat_stream_to_protocol,
    kemo_request_to_chat,
)
from provider.chat_compat import ChatCompatibility
from provider.chat_deepseek import DEEPSEEK_REASONING_PLACEHOLDER
from provider.chat_profiles import resolve_chat_profile
from provider.openai_chat import OpenAIChatTransport
from provider.protocol.errors import CapabilityError
from provider.protocol.enums import MessagePhase
from provider.protocol.models import (
    AssetContent,
    ImageContent,
    KemoRequest,
    KemoResponse,
    MessageItem,
    final_messages,
)
from provider.protocol.streaming import (
    MessageItemStart,
    ProviderStreamEvent,
    StreamSequenceGuard,
)
from provider.schema import ChatRequest, ChatResponse
from tests.provider.test_provider_protocol import FakeHTTPResponse


def config(**updates):
    return {"base_url": "https://relay.test/v1", "model": "unknown", "api_key": "offline", **updates}


def wire(**extra):
    return ChatRequest(model="unknown", messages=[{"role": "user", "content": "hello"}], extra=extra)


def terminal_response(events: list[ProviderStreamEvent]) -> KemoResponse:
    """终态事件里的响应快照；None 表示该事件不是终止帧，直接失败。"""
    response = events[-1].response
    assert response is not None
    return response


def test_stream_converter_uses_explicit_capabilities_before_consuming_upstream():
    capabilities = resolve_chat_profile(config(), "different-model").capabilities
    consumed = []

    def upstream():
        consumed.append(True)
        yield RunEvent(type="text_delta", content="must not appear")

    with pytest.raises(CapabilityError):
        list(chat_stream_to_protocol(upstream(), KemoRequest.create(model="unknown"), capabilities=capabilities))
    assert consumed == []


@pytest.mark.parametrize("tools_first", [False, True])
def test_stream_terminal_phase_reflects_final_tool_calls(tools_first):
    req = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=[{
        "type": "function", "function": {"name": "echo", "strict": False},
    }]))
    call = RunEvent(type="tool_call_start", tool_call_id="echo", tool_name="echo", arguments={})
    text = RunEvent(type="text_delta", content="Checking")
    events = list(chat_stream_to_protocol(
        [call, text] if tools_first else [text, call], req,
    ))
    start = next(e.item for e in events if isinstance(e.item, MessageItemStart))
    terminal = next(i for i in terminal_response(events).output if isinstance(i, MessageItem))
    # 终态必须给出确定的 phase：有工具调用即 commentary
    assert terminal.phase == MessagePhase.COMMENTARY
    if tools_first:
        assert start.phase == MessagePhase.COMMENTARY
    else:
        # 文本先到时首帧无法预知后续工具，允许缺省，且终态不回改已发帧
        assert start.phase is None


def test_stream_terminal_phase_final_answer_when_tools_declared_and_unused():
    """声明 tools、模型零 tool call：终态必须与非流式一致为 final_answer。"""
    req = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=[{
        "type": "function", "function": {"name": "echo", "strict": False},
    }]))
    events = list(chat_stream_to_protocol(
        [RunEvent(type="text_delta", content="Plain answer"),
         RunEvent(type="done", metadata={"finish_reason": "stop"})], req,
    ))
    start = next(e.item for e in events if isinstance(e.item, MessageItemStart))
    terminal = next(i for i in terminal_response(events).output if isinstance(i, MessageItem))
    assert start.phase is None
    assert terminal.phase == MessagePhase.FINAL_ANSWER
    assert final_messages(terminal_response(events)) == [terminal]


def test_stream_and_non_stream_agree_for_plain_text_with_tools():
    """同一语义内容，两条路径的终态 phase 必须相同。"""
    tools = [{"type": "function", "function": {"name": "echo", "strict": False}}]
    req = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=tools))
    stream_events = list(chat_stream_to_protocol(
        [RunEvent(type="text_delta", content="Plain answer"),
         RunEvent(type="done", metadata={"finish_reason": "stop"})], req,
    ))
    non_stream = chat_response_to_kemo(
        ChatResponse(text="Plain answer", finish_reason="stop", model="unknown"), req,
    )
    stream_terminal = next(
        i for i in terminal_response(stream_events).output if isinstance(i, MessageItem)
    )
    non_stream_terminal = next(i for i in non_stream.output if isinstance(i, MessageItem))
    assert stream_terminal.phase == non_stream_terminal.phase == MessagePhase.FINAL_ANSWER


def test_stream_terminal_phase_final_answer_when_tool_choice_none():
    """显式 tool_choice=none 时，首帧与终态都应是 final_answer。"""
    req = chat_request_to_kemo(ChatRequest(
        model="unknown", messages=[],
        tools=[{"type": "function", "function": {"name": "echo", "strict": False}}],
        extra={"tool_choice": "none"},
    ))
    events = list(chat_stream_to_protocol(
        [RunEvent(type="text_delta", content="Plain answer"),
         RunEvent(type="done", metadata={"finish_reason": "stop"})], req,
    ))
    start = next(e.item for e in events if isinstance(e.item, MessageItemStart))
    terminal = next(i for i in terminal_response(events).output if isinstance(i, MessageItem))
    assert start.phase == MessagePhase.FINAL_ANSWER
    assert terminal.phase == MessagePhase.FINAL_ANSWER


def test_stream_terminal_phase_commentary_when_invalid_tool_call_present():
    """解析失败的工具调用仍算出现工具调用，与非流式 response.tool_calls 口径一致。"""
    req = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=[{
        "type": "function", "function": {"name": "echo", "strict": False},
    }]))
    bad = RunEvent(
        type="tool_call_start", tool_call_id="echo", tool_name="echo",
        arguments={}, metadata={"parse_error": {"reason": "invalid_json"}},
    )
    events = list(chat_stream_to_protocol(
        [RunEvent(type="text_delta", content="Checking"), bad,
         RunEvent(type="done", metadata={"finish_reason": "tool_calls"})], req,
    ))
    terminal = next(i for i in terminal_response(events).output if isinstance(i, MessageItem))
    assert terminal.phase == MessagePhase.COMMENTARY


def test_refusal_uses_native_events_and_sequence_validation():
    events = list(chat_stream_to_protocol([
        RunEvent(type="done", metadata={"finish_reason": "stop", "refusal": "Cannot comply"}),
    ], KemoRequest.create(model="unknown")))
    assert [e.type for e in events if "refusal" in e.type] == [
        "output_refusal.delta", "output_refusal.done",
    ]
    guard = StreamSequenceGuard()
    assert all(guard.accept(e) for e in events)
    refusal_item = terminal_response(events).output[0]
    assert isinstance(refusal_item, MessageItem)
    assert refusal_item.refusal == "Cannot comply"


@pytest.mark.parametrize("content", [
    [{"type": "vendor_unknown", "payload": "keep me"}], [42], {"unknown": "keep me"},
    [{"type": "image_url", "image_url": {}}],
])
def test_unknown_or_malformed_content_is_not_silently_erased(content):
    with pytest.raises(CapabilityError):
        chat_request_to_kemo(ChatRequest(model="unknown", messages=[{"role": "user", "content": content}]))


@pytest.mark.parametrize("text", [123, None, ["nested"]])
def test_text_content_block_payload_must_be_a_string(text):
    with pytest.raises(CapabilityError, match="text.*字符串"):
        chat_request_to_kemo(ChatRequest(
            model="unknown",
            messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        ))


@pytest.mark.parametrize("content", ["", [], [{"type": "text", "text": ""}],
                                    [{"type": "input_text", "text": ""}, {"type": "output_text", "text": ""}]])
def test_empty_text_does_not_create_placeholder_content(content):
    assert _content_blocks(content) == []


def test_empty_tool_text_is_rejected_without_placeholder_content():
    with pytest.raises(CapabilityError, match="tool content.*非空字符串"):
        chat_request_to_kemo(ChatRequest(model="unknown", messages=[
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "empty-call", "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "empty-call", "name": "echo", "content": ""},
        ]))


def test_legacy_reasoning_details_without_state_replay_only_safe_identifiers():
    policy = ChatCompatibility("https://relay.test/v1", {})
    payload = policy.payload(
        {
            "model": "unknown",
            "messages": [{
                "role": "assistant",
                "content": "answer",
                "reasoning_details": [{
                    "type": "reasoning.text",
                    "text": "private old thought",
                    "signature": "keep-this",
                    "index": 0,
                }],
            }],
        },
        default_effort="medium",
        enabled_field="reasoning_enabled",
        effort_field="reasoning_effort",
    )
    assert payload["messages"][0]["reasoning_details"] == [{
        "type": "reasoning.text",
        "signature": "keep-this",
        "index": 0,
    }]
    assert "private old thought" not in json.dumps(payload, ensure_ascii=False)


@pytest.mark.parametrize("strict", [None, False, True])
def test_tool_strict_defaults_to_native_true(strict):
    function = {"name": "echo", "parameters": {
        "type": "object", "properties": {}, "additionalProperties": False,
    }}
    if strict is not None:
        function["strict"] = strict
    request = chat_request_to_kemo(ChatRequest(model="unknown", messages=[], tools=[
        {"type": "function", "function": function},
    ]))
    assert request.tools[0].strict is (strict is not False)


def test_persisted_continuation_keeps_signatures_without_reasoning_body():
    policy = ChatCompatibility("https://relay.test/v1", {"reasoning": {"history_field": "reasoning_content"}})
    state = policy.state({
        "reasoning_content": "private-thought-body",
        "reasoning_details": [
            {"type": "reasoning.text", "text": "private-thought-body", "signature": "signature-id", "index": 0},
            {"type": "reasoning.encrypted", "data": "opaque-state", "index": 1},
        ],
    }, "unknown")
    assert state is not None
    decoded = zlib.decompress(base64.b64decode(state["data"])).decode()
    assert "private-thought-body" not in decoded
    assert "signature-id" in decoded and "opaque-state" in decoded
    assert policy.state({"reasoning_content": "private-thought-body"}, "unknown") is None


def test_native_image_models_have_no_adapter_limit_hook():
    assert not hasattr(AssetContent, "_max_inline_image_bytes")
    assert not hasattr(ImageContent, "_max_inline_image_bytes")


@pytest.mark.parametrize("enabled", [None, False, True])
def test_reasoning_switch_preserves_three_states_on_final_http_body(enabled):
    extra = {} if enabled is None else {"reasoning_enabled": enabled}
    native = chat_request_to_kemo(wire(**extra))
    assert (native.reasoning.enabled if native.reasoning else None) is enabled
    profile = {"transport": {"reasoning": {"enabled_field": "enable_thinking"}}}
    mapped = kemo_request_to_chat(native, provider_profile=profile)
    payload = OpenAIChatTransport(config(capabilities=profile))._payload(mapped, stream=False)
    if enabled is None:
        assert "enable_thinking" not in payload
    else:
        assert payload["enable_thinking"] is enabled


def test_single_response_bridge_normalizes_multiple_choices_honestly():
    bridge = ChatBridgeProvider(config())
    native = KemoRequest.create(model="unknown", stream=False, generation={"n": 2})
    reply = {"choices": [
        {"index": 0, "message": {"content": "first"}, "finish_reason": "stop"},
        {"index": 1, "message": {"content": "second"}, "finish_reason": "stop"},
    ]}
    with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(
        json.dumps(reply).encode(), content_type="application/json",
    )) as opened:
        response = bridge.create(native)
    assert json.loads(opened.call_args.args[0].data).get("n", 1) == 1
    assert response.choice_index == 0 and response.choice_count == 1
    assert native.generation.n == 2


def test_text_delta_arrives_before_the_next_sse_frame_is_read():
    body = b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
    body += b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'

    class ObservedResponse(FakeHTTPResponse):
        reads = 0

        def readline(self, *args, **kwargs):
            self.reads += 1
            return super().readline(*args, **kwargs)

    response = ObservedResponse(body, content_type="text/event-stream")
    with patch("urllib.request.urlopen", return_value=response):
        stream = OpenAIChatTransport(config()).chat_stream(wire())
        assert isinstance(stream, Generator)  # chat_stream 是生成器函数，需按生成器关闭
        try:
            event = next(stream)
            assert event.type == "text_delta" and event.content == "first"
            assert response.reads <= 2
        finally:
            stream.close()


def test_multiline_sse_data_is_joined_before_json_parsing():
    body = b'data: {"choices": [\ndata: {"delta":{"content":"joined"},"finish_reason":"stop"}]}\n\n'
    body += b'data: [DONE]\n\n'
    with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(body, content_type="text/event-stream")):
        events = list(OpenAIChatTransport(config()).chat_stream(wire()))
    assert "".join(e.content for e in events if e.type == "text_delta") == "joined"


def test_user_capability_claims_are_deprecated_and_do_not_change_automatic_profile(caplog):
    baseline = resolve_chat_profile(config(), "unknown")
    declared = resolve_chat_profile(config(capabilities={
        "tools": {"function_calling": False}, "reasoning": {"supported": False},
        "supports_prediction": True, "supports_multiple_choices": True,
    }), "unknown")
    assert declared.capabilities == baseline.capabilities
    assert baseline.capabilities.reasoning.supported
    assert baseline.capabilities.structured_output.schema_subset == ["json_object", "json_schema"]
    assert not baseline.capabilities.supports_prediction
    assert "deprecated" in caplog.text


@pytest.mark.parametrize("enabled", [None, False, True])
def test_deepseek_final_wire_keeps_unspecified_disabled_and_enabled_distinct(enabled):
    native = chat_request_to_kemo(ChatRequest(
        model="deepseek-v4-flash", messages=[],
        extra={} if enabled is None else {"reasoning_enabled": enabled},
    ))
    mapped = kemo_request_to_chat(native, deepseek=True)
    payload = OpenAIChatTransport(config(model=native.model))._payload(mapped, stream=False)
    if enabled is None:
        assert "thinking" not in payload
    else:
        assert payload["thinking"] == {"type": "enabled" if enabled else "disabled"}


def test_legacy_placeholder_cannot_become_protocol_reasoning_or_upstream_history():
    message = {"role": "assistant", "content": "old", "reasoning_content": DEEPSEEK_REASONING_PLACEHOLDER}
    native = chat_request_to_kemo(ChatRequest(model="deepseek-v4-flash", messages=[message]))
    assert DEEPSEEK_REASONING_PLACEHOLDER not in native.model_dump_json()
    payload = OpenAIChatTransport(config(model=native.model))._payload(ChatRequest(
        model=native.model, messages=[message], extra={"reasoning_enabled": True},
        tools=[{"type": "function", "function": {"name": "echo"}}],
    ), stream=False)
    assert payload["messages"][0]["reasoning_content"] == ""
    assert message["reasoning_content"] == DEEPSEEK_REASONING_PLACEHOLDER


def test_multichoice_stream_never_merges_an_unselected_candidate():
    frame = {"choices": [
        {"index": 1, "delta": {"content": "must not merge"}, "finish_reason": "stop"},
        {"index": 0, "delta": {"content": "chosen"}, "finish_reason": "stop"},
    ]}
    body = f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n".encode()
    with patch("urllib.request.urlopen", return_value=FakeHTTPResponse(body, content_type="text/event-stream")):
        events = list(OpenAIChatTransport(config()).chat_stream(wire(n=2)))
    assert "".join(event.content for event in events if event.type == "text_delta") == "chosen"


def test_legacy_encoded_state_is_sanitized_before_replay():
    policy = ChatCompatibility("https://relay.test/v1", {"reasoning": {"history_field": "reasoning_content"}})
    body = {"route": policy.route("unknown"), "fields": {
        "reasoning_content": "legacy-thought",
        "reasoning_details": [{"type": "reasoning.text", "text": "legacy-thought", "signature": "keep"}],
    }}
    state = {"kind": "opaque", "provider": "chat-adapter", "model": "unknown", "version": "1",
             "data": base64.b64encode(zlib.compress(json.dumps(body).encode())).decode()}
    decoded = policy._decode_state(state, "unknown")
    assert "legacy-thought" not in json.dumps(decoded)
    assert decoded["reasoning_details"][0]["signature"] == "keep"


def test_legacy_capability_claim_warns_when_loading_config(tmp_path, caplog):
    from run.config import load_config

    (tmp_path / "config").mkdir()
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "config/global_config.yaml").write_text("{}", "utf-8")
    (tmp_path / "users/alice/user_config.yaml").write_text(json.dumps({"provider": {
        "type": "chat", "model": "unknown", "base_url": "http://127.0.0.1:1",
        "capabilities": {"legacy_startup_claim": True},
    }}), "utf-8")
    load_config("alice", tmp_path)
    assert "deprecated" in caplog.text and "legacy_startup_claim" not in caplog.text
