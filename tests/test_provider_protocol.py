from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from events import RunEvent
from provider.adapters.compat import (
    chat_request_to_kemo,
    chat_stream_to_protocol,
    kemo_request_to_chat,
    kemo_response_to_chat,
)
from provider.adapters.gateway import KemoGatewayAdapter
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus, StreamEventType
from provider.protocol.errors import CapabilityError, ProtocolValidationError, StreamProtocolError
from provider.protocol.models import (
    AudioContent,
    ImageContent,
    JsonContent,
    KemoRequest,
    KemoResponse,
    Measurement,
    MessageItem,
    ReasoningItem,
    TextContent,
    ToolCallItem,
    ToolResultItem,
    Usage,
)
from provider.protocol.serialization import parse_request, parse_response, to_json_bytes
from provider.protocol.streaming import (
    ProviderStreamEvent,
    StreamSequenceGuard,
    encode_sse,
    parse_sse_events,
)
from provider.protocol.validation import validate_request
from provider.schema import ChatRequest, ChatResponse, Usage as ChatUsage
from run.engine import handle_request, iter_request_events
from run.history import commit_window, empty_window, find_window, load_window


def make_request(*, stream: bool = True) -> KemoRequest:
    return KemoRequest(
        request_id="req_test",
        model="gateway/test",
        stream=stream,
        system_prompt="system",
        input=[
            MessageItem(
                id="msg_user",
                role=MessageRole.USER,
                content=[
                    TextContent(text="look"),
                    ImageContent(asset_id="asset_image", mime_type="image/png"),
                    JsonContent(data={"mode": "detail"}),
                ],
            )
        ],
        metadata={"session_id": "s1"},
    )


def make_response(request: KemoRequest, *, response_id: str = "resp_test") -> KemoResponse:
    return KemoResponse(
        id=response_id,
        request_id=request.request_id,
        status=ResponseStatus.COMPLETED,
        model=request.model,
        output=[
            ReasoningItem(id="rs_answer", summary="checked"),
            MessageItem.text(
                MessageRole.ASSISTANT,
                "answer",
                phase=MessagePhase.FINAL_ANSWER,
                item_id="msg_answer",
            ),
        ],
        usage=Usage(
            input_tokens=10,
            cached_input_tokens=3,
            output_tokens=4,
            reasoning_tokens=2,
            total_tokens=14,
            measurement=Measurement(
                mode="gateway",
                exact=True,
                exact_fields=["input_tokens", "output_tokens", "total_tokens"],
            ),
        ),
    )


class FakeHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.closed = False

    def read(self) -> bytes:
        return self._stream.read()

    def readline(self) -> bytes:
        return self._stream.readline()

    def close(self) -> None:
        self.closed = True

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class NativeProvider:
    def __init__(self, *, stream: bool = False) -> None:
        self.requests: list[KemoRequest] = []
        self.use_stream = stream

    def create(self, request: KemoRequest) -> KemoResponse:
        self.requests.append(request)
        return make_response(request, response_id=f"resp_{len(self.requests)}")

    def stream(self, request: KemoRequest):
        self.requests.append(request)
        response = make_response(request, response_id=f"resp_{len(self.requests)}")
        yield ProviderStreamEvent(
            type=StreamEventType.RESPONSE_CREATED,
            sequence=0,
            request_id=request.request_id,
            response_id=response.id,
        )
        yield ProviderStreamEvent(
            type=StreamEventType.OUTPUT_TEXT_DELTA,
            sequence=1,
            request_id=request.request_id,
            response_id=response.id,
            item_id="msg_answer",
            content_index=0,
            delta="answer",
        )
        yield ProviderStreamEvent(
            type=StreamEventType.USAGE_UPDATED,
            sequence=2,
            request_id=request.request_id,
            response_id=response.id,
            usage=response.usage,
        )
        yield ProviderStreamEvent(
            type=StreamEventType.RESPONSE_COMPLETED,
            sequence=3,
            request_id=request.request_id,
            response_id=response.id,
            response=response,
        )


class ChatOnlyProvider:
    def __init__(self) -> None:
        self.requests: list[ChatRequest] = []

    def chat(self, request: ChatRequest) -> ChatResponse:
        self.requests.append(request)
        return ChatResponse(
            text="legacy",
            model=request.model,
            usage=ChatUsage(2, 1, 3, source="mock"),
        )

    def chat_stream(self, request: ChatRequest):
        self.requests.append(request)
        yield RunEvent(type="text_delta", content="legacy")
        yield RunEvent(type="usage", usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})
        yield RunEvent(type="done", usage={"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3})


class UnifiedProtocolTests(unittest.TestCase):
    def make_root(self, *, stream: bool = False) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "users" / "alice" / "history").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps({"tools": {"enabled": False}}), "utf-8"
        )
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:1",
                        "api_key_env": "TEST_KEMO_KEY",
                        "model": "gateway/test",
                        "stream": stream,
                    },
                }
            ),
            "utf-8",
        )
        return temporary, root

    def test_chat_stream_usage_promotes_cache_and_reasoning_fields(self) -> None:
        request = KemoRequest(
            request_id="req_cache",
            model="chat-model",
            stream=True,
            system_prompt="system",
            input=[],
        )
        events = [
            RunEvent(
                type="usage",
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 4,
                    "total_tokens": 104,
                    "prompt_tokens_details": {"cached_tokens": 60},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
            ),
            RunEvent(type="done"),
        ]

        converted = list(chat_stream_to_protocol(events, request))
        usage_event = next(
            event
            for event in converted
            if event.type == StreamEventType.USAGE_UPDATED
        )
        self.assertEqual(usage_event.usage.cached_input_tokens, 60)
        self.assertEqual(usage_event.usage.reasoning_tokens, 3)
        completed = converted[-1]
        self.assertEqual(completed.response.usage.cached_input_tokens, 60)

    def test_chat_stream_usage_promotes_prompt_cache_hit_alias(self) -> None:
        request = KemoRequest(
            request_id="req_cache_alias",
            model="chat-model",
            stream=True,
            system_prompt="system",
            input=[],
        )
        events = [
            RunEvent(
                type="usage",
                usage={
                    "prompt_tokens": 8,
                    "completion_tokens": 1,
                    "prompt_cache_hit_tokens": 5,
                },
            ),
            RunEvent(type="done"),
        ]

        converted = list(chat_stream_to_protocol(events, request))
        usage_event = next(
            event
            for event in converted
            if event.type == StreamEventType.USAGE_UPDATED
        )
        self.assertEqual(usage_event.usage.cached_input_tokens, 5)

    def test_json_roundtrip_and_protocol_version_validation(self) -> None:
        request = make_request()
        parsed = parse_request(to_json_bytes(request))
        self.assertEqual(parsed, request)
        response = make_response(request)
        self.assertEqual(parse_response(to_json_bytes(response)), response)
        invalid = request.model_dump(mode="json")
        invalid["protocol_version"] = "2.0"
        with self.assertRaises(ProtocolValidationError):
            parse_request(invalid)

    def test_ids_tool_linkage_and_asset_path_are_strict(self) -> None:
        with self.assertRaises(ValidationError):
            ImageContent(asset_id="C:\\secret\\image.png")
        call = ToolCallItem(
            id="call_item", call_id="call_1", name="lookup", arguments={"q": "x"}
        )
        result = ToolResultItem(
            id="result_item",
            call_id="call_1",
            name="lookup",
            content=[JsonContent(data={"ok": True})],
        )
        request = KemoRequest(
            model="gateway/test",
            system_prompt="",
            input=[call, result],
        )
        self.assertIs(validate_request(request), request)
        invalid_name = request.model_copy(
            update={
                "input": [
                    call,
                    result.model_copy(update={"name": "other"}),
                ]
            }
        )
        with self.assertRaisesRegex(Exception, "不一致"):
            validate_request(invalid_name)
        with self.assertRaises(ValidationError):
            KemoRequest(
                model="gateway/test",
                system_prompt="",
                input=[
                    MessageItem.text("user", "a", item_id="msg_same"),
                    MessageItem.text("assistant", "b", item_id="msg_same"),
                ],
            )

    def test_sse_roundtrip_sequence_dedup_and_terminal_guard(self) -> None:
        request = make_request()
        response = make_response(request)
        events = [
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_CREATED,
                event_id="evt_0",
                sequence=0,
                request_id=request.request_id,
                response_id=response.id,
            ),
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_COMPLETED,
                event_id="evt_1",
                sequence=1,
                request_id=request.request_id,
                response_id=response.id,
                response=response,
            ),
        ]
        payload = b"".join(encode_sse(event) for event in events)
        parsed = list(parse_sse_events(payload.splitlines(keepends=True)))
        self.assertEqual([event.event_id for event in parsed], ["evt_0", "evt_1"])
        guard = StreamSequenceGuard()
        self.assertTrue(guard.accept(parsed[0]))
        self.assertFalse(guard.accept(parsed[0]))
        self.assertTrue(guard.accept(parsed[1]))
        after = ProviderStreamEvent(
            type=StreamEventType.USAGE_UPDATED,
            sequence=2,
            request_id=request.request_id,
            response_id=response.id,
            usage=response.usage,
        )
        with self.assertRaises(StreamProtocolError):
            guard.accept(after)
        gap_guard = StreamSequenceGuard()
        with self.assertRaises(StreamProtocolError):
            gap_guard.accept(parsed[1])

    def test_chat_bridge_mapping_and_multimodal_capability_error(self) -> None:
        chat = ChatRequest(
            model="test",
            stream=False,
            messages=[
                {"role": "system", "content": "system"},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "look"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.test/a.png", "detail": "low"},
                        },
                    ],
                },
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{\"q\":\"x\"}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "name": "lookup", "content": "ok"},
            ],
        )
        request = chat_request_to_kemo(chat)
        self.assertTrue(request.reasoning.enabled)
        self.assertEqual(request.reasoning.effort, "medium")
        self.assertEqual(request.reasoning.return_mode, "content")
        restored = kemo_request_to_chat(request)
        self.assertEqual(restored.extra["reasoning_effort"], "medium")
        self.assertEqual(restored.messages[0]["role"], "system")
        self.assertEqual(restored.messages[1]["content"][1]["type"], "image_url")
        self.assertEqual(restored.messages[-1]["tool_call_id"], "call_1")
        response = make_response(request)
        self.assertEqual(kemo_response_to_chat(response).text, "answer")

        unsupported = KemoRequest(
            model="chat-only",
            system_prompt="",
            input=[
                MessageItem(
                    id="msg_audio",
                    role="user",
                    content=[AudioContent(asset_id="asset_audio", mime_type="audio/wav")],
                )
            ],
        )
        with self.assertRaises(CapabilityError):
            kemo_request_to_chat(unsupported)

    def test_chat_bridge_skips_legacy_empty_native_reasoning(self) -> None:
        chat = ChatRequest(
            model="test",
            messages=[
                {
                    "role": "assistant",
                    "content": "legacy answer",
                    "_kemo_reasoning": {
                        "id": "rs_empty",
                        "type": "reasoning",
                        "status": "completed",
                        "content": "",
                        "summary": None,
                        "provider_state": None,
                        "metadata": {"round": 1},
                    },
                },
                {
                    "role": "assistant",
                    "content": "current answer",
                    "_kemo_reasoning": {
                        "id": "rs_valid",
                        "type": "reasoning",
                        "status": "completed",
                        "summary": "valid summary",
                        "metadata": {"round": 2},
                    },
                },
            ],
        )

        request = chat_request_to_kemo(chat)

        reasoning = [item for item in request.input if isinstance(item, ReasoningItem)]
        self.assertEqual([item.id for item in reasoning], ["rs_valid"])
        self.assertEqual(reasoning[0].summary, "valid summary")

    def test_gateway_native_json_and_sse_transport(self) -> None:
        request = make_request()
        response = make_response(request)
        adapter = KemoGatewayAdapter(
            {
                "base_url": "https://gateway.test/v1",
                "api_key": "secret",
                "model": request.model,
            }
        )
        seen = []

        def open_json(http_request):
            seen.append(http_request)
            return FakeHTTPResponse(to_json_bytes(response))

        adapter._open = open_json
        created = adapter.create(request)
        self.assertEqual(created.id, response.id)
        self.assertEqual(seen[0].full_url, "https://gateway.test/v1/model/responses")
        self.assertFalse(json.loads(seen[0].data)["stream"])

        adapter._open = lambda capability_request: FakeHTTPResponse(
            json.dumps(
                {
                    "model": request.model,
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                }
            ).encode("utf-8")
        )
        self.assertEqual(adapter.capabilities(request.model).input_modalities, ["text", "image"])

        stream_events = [
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_CREATED,
                sequence=0,
                request_id=request.request_id,
                response_id=response.id,
            ),
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_COMPLETED,
                sequence=1,
                request_id=request.request_id,
                response_id=response.id,
                response=response,
            ),
        ]
        adapter._open = lambda _request: FakeHTTPResponse(
            b"".join(encode_sse(event) for event in stream_events)
        )
        self.assertEqual(len(list(adapter.stream(request))), 2)

    def test_run_native_protocol_history_items_and_rejects_chat_only_provider(self) -> None:
        _, root = self.make_root(stream=False)
        native = NativeProvider()
        request = {
            "user": "alice",
            "source": "web",
            "session_id": "native",
            "prompt": "describe",
            "content": [{"type": "image", "asset_id": "asset_image"}],
            "run_id": "run_native_123",
        }
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            events = list(iter_request_events(request, root=root, provider_factory=lambda _: native))
        self.assertEqual(events[-1].type, "done")
        self.assertEqual([event.run_sequence for event in events], list(range(len(events))))
        self.assertTrue(all(event.event_id for event in events))
        self.assertEqual(native.requests[0].metadata["iteration"], 1)
        user_item = native.requests[0].input[-1]
        self.assertIsInstance(user_item, MessageItem)
        self.assertEqual([block.type for block in user_item.content], ["text", "image"])
        window_path = find_window(root, "alice", "web", "native")
        self.assertTrue((window_path / "items.json").is_file())
        window = load_window(window_path)
        self.assertEqual(window["items"]["schema_version"], 2)
        self.assertTrue(any(item.get("type") == "reasoning" for item in window["items"]["items"]))

        chat_only = ChatOnlyProvider()
        with patch.dict(os.environ, {"TEST_KEMO_KEY": "secret"}, clear=False):
            with self.assertRaisesRegex(Exception, "Kemo create"):
                handle_request(
                    {
                        "user": "alice",
                        "source": "web",
                        "session_id": "legacy",
                        "prompt": "hello",
                    },
                    root=root,
                    provider_factory=lambda _: chat_only,
                )
        self.assertEqual(len(chat_only.requests), 0)

    def test_v1_history_loads_without_items_and_is_dual_written_on_commit(self) -> None:
        _, root = self.make_root()
        directory = root / "users" / "alice" / "history" / "legacy-window"
        directory.mkdir()
        window = empty_window("alice", "web", "old")
        window["text"]["messages"] = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]
        window["data"]["rounds"] = 1
        commit_window(directory, window)
        (directory / "items.json").unlink()
        migrated = load_window(directory)
        self.assertEqual(len(migrated["items"]["items"]), 2)
        commit_window(directory, migrated)
        self.assertTrue((directory / "items.json").is_file())


if __name__ == "__main__":
    unittest.main()
