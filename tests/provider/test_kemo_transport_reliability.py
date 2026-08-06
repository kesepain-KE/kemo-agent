from __future__ import annotations

import io
import json
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from provider.adapters.gateway import KemoGatewayAdapter
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus, StreamEventType
from provider.protocol.errors import StreamProtocolError
from provider.protocol.models import (
    EmbeddingInput,
    EmbeddingRequest,
    KemoRequest,
    KemoResponse,
    MessageItem,
    RerankDocument,
    RerankRequest,
)
from provider.protocol.serialization import to_json_bytes
from provider.protocol.streaming import ProviderStreamEvent, encode_sse
from provider.schema import ProviderError, ProviderTimeoutError


def _request(*, stream: bool = True) -> KemoRequest:
    return KemoRequest(
        request_id="req_transport",
        model="gateway/test",
        stream=stream,
        system_prompt="system",
        input=[MessageItem.text(MessageRole.USER, "hello", item_id="msg_user")],
    )


def _response(request: KemoRequest, response_id: str = "resp_transport") -> KemoResponse:
    return KemoResponse(
        id=response_id,
        request_id=request.request_id,
        status=ResponseStatus.COMPLETED,
        model=request.model,
        output=[
            MessageItem.text(
                MessageRole.ASSISTANT,
                "done",
                phase=MessagePhase.FINAL_ANSWER,
                item_id="msg_answer",
            )
        ],
    )


class _HTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.closed = False

    def read(self, size: int = -1) -> bytes:
        return self._stream.read(size)

    def readline(self) -> bytes:
        return self._stream.readline()

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class _BlockingResponse:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.closed = threading.Event()

    def readline(self) -> bytes:
        self.started.set()
        self.closed.wait(2.0)
        return b""

    def read(self, _size: int = -1) -> bytes:
        self.started.set()
        self.closed.wait(2.0)
        return b""

    def close(self) -> None:
        self.closed.set()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()


class _BlockingAfterPayloadResponse(_BlockingResponse):
    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self._stream = io.BytesIO(payload)

    def readline(self) -> bytes:
        line = self._stream.readline()
        if line:
            return line
        return super().readline()


class KemoTransportReliabilityTests(unittest.TestCase):
    def make_adapter(self) -> KemoGatewayAdapter:
        adapter = KemoGatewayAdapter(
            {
                "base_url": "https://gateway.test",
                "api_key": "secret",
                "model": "gateway/test",
            }
        )
        adapter._retry_policy.base_seconds = 0
        return adapter

    def test_stream_reconnects_with_same_request_and_last_event_id(self) -> None:
        request = _request()
        response = _response(request)
        first = [
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_CREATED,
                event_id="evt_0",
                sequence=0,
                request_id=request.request_id,
                response_id=response.id,
            ),
            ProviderStreamEvent(
                type=StreamEventType.OUTPUT_TEXT_DELTA,
                event_id="evt_1",
                sequence=1,
                request_id=request.request_id,
                response_id=response.id,
                item_id="msg_answer",
                content_index=0,
                delta="done",
            ),
        ]
        second = [
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_COMPLETED,
                event_id="evt_2",
                sequence=2,
                request_id=request.request_id,
                response_id=response.id,
                response=response,
            )
        ]
        replies = [
            _HTTPResponse(b"".join(encode_sse(event) for event in first)),
            _HTTPResponse(
                b": heartbeat\n\n"
                + b"".join(encode_sse(event) for event in second)
            ),
        ]
        seen = []
        adapter = self.make_adapter()

        def open_response(http_request):
            seen.append(http_request)
            return replies.pop(0)

        adapter._open = open_response
        events = list(adapter.stream(request))

        self.assertEqual([event.sequence for event in events], [0, 1, 2])
        self.assertEqual(len(seen), 2)
        self.assertEqual(
            json.loads(seen[0].data)["request_id"],
            json.loads(seen[1].data)["request_id"],
        )
        self.assertEqual(seen[1].get_header("Last-event-id"), "evt_1")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[1].full_url).query)
        self.assertEqual(query, {})
        self.assertEqual(seen[1].get_header("Idempotency-key"), request.request_id)

    def test_stream_refuses_to_join_a_restarted_response(self) -> None:
        request = _request()
        events = [
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_CREATED,
                event_id="evt_original",
                sequence=0,
                request_id=request.request_id,
                response_id="resp_original",
            ),
            ProviderStreamEvent(
                type=StreamEventType.RESPONSE_CREATED,
                event_id="evt_restarted",
                sequence=1,
                request_id=request.request_id,
                response_id="resp_restarted",
            ),
        ]
        replies = [
            _HTTPResponse(encode_sse(events[0])),
            _HTTPResponse(encode_sse(events[1])),
        ]
        adapter = self.make_adapter()
        adapter._open = lambda _request: replies.pop(0)

        with self.assertRaisesRegex(StreamProtocolError, "不同的 response_id"):
            list(adapter.stream(request))

    def test_non_stream_request_retries_with_the_same_idempotency_key(self) -> None:
        request = _request(stream=False)
        response = _response(request)
        seen = []
        adapter = self.make_adapter()

        def open_response(http_request):
            seen.append(http_request)
            if len(seen) == 1:
                raise ProviderTimeoutError("temporary timeout")
            return _HTTPResponse(to_json_bytes(response))

        adapter._open = open_response
        result = adapter.create(request)

        self.assertEqual(result.id, response.id)
        self.assertEqual(len(seen), 2)
        self.assertEqual(
            [item.get_header("Idempotency-key") for item in seen],
            [request.request_id, request.request_id],
        )
        self.assertEqual(seen[0].data, seen[1].data)

    def test_http_retry_classification_respects_transient_statuses(self) -> None:
        adapter = self.make_adapter()
        request = urllib.request.Request("https://gateway.test/model/responses")
        transient = urllib.error.HTTPError(
            request.full_url,
            503,
            "unavailable",
            {"Retry-After": "5"},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "message": "gateway draining",
                            "retryable": False,
                        }
                    }
                ).encode("utf-8")
            ),
        )
        with patch("urllib.request.urlopen", side_effect=transient):
            with self.assertRaises(ProviderError) as raised:
                adapter._open(request)
        self.assertFalse(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after_ms, 5000)

        implicit_transient = urllib.error.HTTPError(
            request.full_url,
            503,
            "unavailable",
            {},
            io.BytesIO(
                json.dumps(
                    {"error": {"message": "temporary upstream failure"}}
                ).encode("utf-8")
            ),
        )
        with patch("urllib.request.urlopen", side_effect=implicit_transient):
            with self.assertRaises(ProviderError) as raised:
                adapter._open(request)
        self.assertTrue(raised.exception.retryable)

        conflict = urllib.error.HTTPError(
            request.full_url,
            409,
            "conflict",
            {},
            io.BytesIO(
                json.dumps(
                    {
                        "error": {
                            "message": "idempotency conflict",
                            "retryable": False,
                        }
                    }
                ).encode("utf-8")
            ),
        )
        with patch("urllib.request.urlopen", side_effect=conflict):
            with self.assertRaises(ProviderError) as raised:
                adapter._open(request)
        self.assertFalse(raised.exception.retryable)

    def test_embedding_retries_with_identical_body_and_idempotency_key(self) -> None:
        request = EmbeddingRequest(
            request_id="req_embed_retry",
            model="gateway/embed",
            input_type="query",
            inputs=[EmbeddingInput(id="query_1", text="hello")],
        )
        payload = {
            "protocol_version": "1.0",
            "object": "kemo.embedding_list",
            "request_id": request.request_id,
            "model": request.model,
            "vector_space_id": "space-v1",
            "dimensions": 2,
            "data": [{"id": "query_1", "index": 0, "vector": [0.1, 0.2]}],
        }
        seen = []
        adapter = self.make_adapter()

        def open_response(http_request):
            seen.append(http_request)
            if len(seen) == 1:
                raise ProviderTimeoutError("temporary timeout")
            return _HTTPResponse(json.dumps(payload).encode("utf-8"))

        adapter._open = open_response
        result = adapter.embeddings(request)

        self.assertEqual(result.vector_space_id, "space-v1")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].data, seen[1].data)
        self.assertEqual(
            [item.get_header("Idempotency-key") for item in seen],
            [request.request_id, request.request_id],
        )

    def test_rerank_retries_with_identical_body_and_idempotency_key(self) -> None:
        request = RerankRequest(
            request_id="req_rerank_retry",
            model="gateway/rerank",
            query="hello",
            documents=[RerankDocument(id="doc_1", text="hello world")],
        )
        payload = {
            "protocol_version": "1.0",
            "object": "kemo.rerank",
            "request_id": request.request_id,
            "model": request.model,
            "results": [
                {
                    "rank": 1,
                    "document_id": "doc_1",
                    "index": 0,
                    "relevance_score": 0.99,
                }
            ],
        }
        seen = []
        adapter = self.make_adapter()

        def open_response(http_request):
            seen.append(http_request)
            if len(seen) == 1:
                raise ProviderTimeoutError("temporary timeout")
            return _HTTPResponse(json.dumps(payload).encode("utf-8"))

        adapter._open = open_response
        result = adapter.rerank(request)

        self.assertEqual(result.results[0].document_id, "doc_1")
        self.assertEqual(len(seen), 2)
        self.assertEqual(seen[0].data, seen[1].data)
        self.assertEqual(
            [item.get_header("Idempotency-key") for item in seen],
            [request.request_id, request.request_id],
        )

    def test_protocol_corruption_is_not_retried(self) -> None:
        adapter = self.make_adapter()
        calls = 0

        def open_response(_request):
            nonlocal calls
            calls += 1
            return _HTTPResponse(b"event: response.created\ndata: {bad json}\n\n")

        adapter._open = open_response
        with self.assertRaises(StreamProtocolError):
            list(adapter.stream(_request()))
        self.assertEqual(calls, 1)

    def test_truncated_sse_frame_is_resumed_from_last_complete_event(self) -> None:
        request = _request()
        response = _response(request)
        created = ProviderStreamEvent(
            type=StreamEventType.RESPONSE_CREATED,
            event_id="evt_complete",
            sequence=0,
            request_id=request.request_id,
            response_id=response.id,
        )
        completed = ProviderStreamEvent(
            type=StreamEventType.RESPONSE_COMPLETED,
            event_id="evt_terminal",
            sequence=1,
            request_id=request.request_id,
            response_id=response.id,
            response=response,
        )
        replies = [
            _HTTPResponse(
                encode_sse(created)
                + b"event: response.output_text.delta\ndata: {\"type\":"
            ),
            _HTTPResponse(encode_sse(completed)),
        ]
        adapter = self.make_adapter()
        adapter._open = lambda _request: replies.pop(0)

        events = list(adapter.stream(request))

        self.assertEqual([event.event_id for event in events], [
            "evt_complete",
            "evt_terminal",
        ])

    def test_cancel_event_interrupts_a_blocked_sse_read(self) -> None:
        adapter = self.make_adapter()
        response = _BlockingResponse()
        adapter._open = lambda _request: response
        cancel_event = threading.Event()
        errors: list[BaseException] = []

        def consume() -> None:
            try:
                list(adapter.stream(_request(), cancel_event=cancel_event))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=consume)
        worker.start()
        self.assertTrue(response.started.wait(1.0))
        cancel_event.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ProviderError)
        self.assertEqual(getattr(errors[0], "category", ""), "cancelled")

    def test_cancel_event_interrupts_a_blocked_non_stream_read(self) -> None:
        adapter = self.make_adapter()
        response = _BlockingResponse()
        adapter._open = lambda _request: response
        cancel_event = threading.Event()
        errors: list[BaseException] = []

        def consume() -> None:
            try:
                adapter.create(_request(stream=False), cancel_event=cancel_event)
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=consume)
        worker.start()
        self.assertTrue(response.started.wait(1.0))
        cancel_event.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ProviderError)
        self.assertEqual(getattr(errors[0], "category", ""), "cancelled")

    def test_cancel_event_propagates_to_the_remote_response(self) -> None:
        request = _request()
        created = ProviderStreamEvent(
            type=StreamEventType.RESPONSE_CREATED,
            event_id="evt_created",
            sequence=0,
            request_id=request.request_id,
            response_id="resp_to_cancel",
        )
        response = _BlockingAfterPayloadResponse(encode_sse(created))
        adapter = self.make_adapter()
        adapter._open = lambda _request: response
        cancelled_ids: list[str] = []
        adapter._cancel_response_best_effort = cancelled_ids.append
        cancel_event = threading.Event()
        errors: list[BaseException] = []

        def consume() -> None:
            try:
                list(adapter.stream(request, cancel_event=cancel_event))
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=consume)
        worker.start()
        self.assertTrue(response.started.wait(1.0))
        cancel_event.set()
        worker.join(1.0)

        self.assertFalse(worker.is_alive())
        self.assertEqual(cancelled_ids, ["resp_to_cancel"])
        self.assertEqual(getattr(errors[0], "category", ""), "cancelled")

    def test_kemo_1_0_rejects_sequence_without_last_event_id(self) -> None:
        adapter = self.make_adapter()

        with self.assertRaisesRegex(ValueError, "Last-Event-ID"):
            list(adapter.stream(_request(), resume_from_sequence=2))

    def test_kemo_1_0_uses_last_event_id_without_legacy_query(self) -> None:
        request = _request()
        response = _response(request)
        completed = ProviderStreamEvent(
            type=StreamEventType.RESPONSE_COMPLETED,
            event_id="evt_3",
            sequence=3,
            request_id=request.request_id,
            response_id=response.id,
            response=response,
        )
        seen = []
        adapter = self.make_adapter()

        def open_response(http_request):
            seen.append(http_request)
            return _HTTPResponse(encode_sse(completed))

        adapter._open = open_response
        events = list(
            adapter.stream(
                request,
                last_event_id="evt_2",
                resume_from_sequence=2,
            )
        )

        self.assertEqual([event.sequence for event in events], [3])
        self.assertEqual(seen[0].get_header("Last-event-id"), "evt_2")
        self.assertEqual(urllib.parse.urlsplit(seen[0].full_url).query, "")


if __name__ == "__main__":
    unittest.main()
