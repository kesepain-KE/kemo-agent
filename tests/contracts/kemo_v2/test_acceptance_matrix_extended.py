"""Executable coverage for the remaining model/serialization P.4 rows.

The gateway owns the durable execution store and HTTP-only checks; this file
keeps the Agent-side portion explicit rather than pretending a Pydantic model
test proves a cross-process guarantee.  Runtime rows are exercised in the
gateway contract suite, while these tests pin the shared wire decisions.
"""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError

from provider.protocol.enums import ErrorCode, StreamEventType
from provider.protocol.errors import CapabilityError, ProtocolValidationError, StreamProtocolError
from provider.protocol.models import (
    Annotation,
    EmbeddingData,
    EmbeddingResponse,
    ImageContent,
    KemoRequest,
    KemoResponse,
    KemoResponseBatch,
    MessageItem,
    ModelCapabilities,
    RerankResponse,
    RerankResultItem,
    StructuredOutputConfig,
    ToolChoice,
    ToolDefinition,
    Usage,
)
from provider.protocol.serialization import request_fingerprint
from provider.protocol.validation import validate_request_against_capabilities
from provider.protocol.streaming import (
    MessageItemStart,
    ProviderStreamEvent,
    StreamSequenceGuard,
)


def _request(**updates: object) -> KemoRequest:
    value: dict[str, object] = {
        "protocol_version": "2.0",
        "request_id": "req_matrix_extended",
        "model": "demo-model",
        "stream": False,
    }
    value.update(updates)
    return KemoRequest.model_validate(value)


def _response(*, request_id: str = "req_batch", index: int = 0, count: int = 1) -> KemoResponse:
    return KemoResponse(
        protocol_version="2.0",
        id=f"resp_batch_{index}",
        request_id=request_id,
        model="demo-model",
        status="completed",
        choice_index=index,
        choice_count=count,
        output=[MessageItem.text("assistant", f"answer-{index}", item_id=f"msg_batch_{index}")],
        usage=Usage(input_tokens=100, output_tokens=0, total_tokens=100),
    )


def test_r02_allowed_modes_are_closed_and_request_tools_are_known() -> None:
    choice = ToolChoice(mode="allowed", allowed_tools=["lookup"], allowed_mode="required")
    assert choice.allowed_mode == "required"
    with pytest.raises(ValidationError):
        ToolChoice(mode="allowed", allowed_tools=["lookup"], allowed_mode="required", name="lookup")
    with pytest.raises(ValidationError):
        _request(tool_choice={"mode": "allowed", "allowed_tools": ["missing"], "allowed_mode": "auto"})


def test_r03_custom_tools_cannot_cross_the_function_only_boundary() -> None:
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(
            {
                "type": "custom",
                "name": "lookup",
                "description": "lookup",
                "parameters": {"type": "object"},
            }
        )


def test_r04_media_references_and_unicode_annotation_indices() -> None:
    with pytest.raises(ValidationError):
        ImageContent(
            asset_id="asset_image",
            mime_type="image/png",
            source={"kind": "url", "uri": "https://example.test/a.png", "data": "bad"},
        )
    item = MessageItem(
        id="msg_annotation",
        role="assistant",
        content=[{"type": "text", "text": "猫😀"}],
        annotations=[
            Annotation(type="url_citation", content_index=0, url="https://example.test", start_index=1, end_index=2)
        ],
    )
    assert item.annotations[0].end_index == 2
    with pytest.raises(ValidationError):
        MessageItem(
            id="msg_bad_annotation",
            role="assistant",
            content=[{"type": "text", "text": "猫😀"}],
            annotations=[Annotation(type="url_citation", content_index=0, url="https://example.test", start_index=0, end_index=5)],
        )


def test_r05_batch_usage_is_one_request_level_total() -> None:
    batch = KemoResponseBatch(
        request_id="req_batch",
        model="demo-model",
        responses=[_response(index=0, count=2), _response(index=1, count=2)],
        usage=Usage(input_tokens=100, output_tokens=20, total_tokens=120),
    )
    assert batch.usage.total_tokens == 120
    assert [response.usage.total_tokens for response in batch.responses] == [100, 100]


def test_r06_batch_rejects_missing_or_duplicate_choice_indices() -> None:
    with pytest.raises(ValidationError):
        KemoResponseBatch(
            request_id="req_batch",
            model="demo-model",
            responses=[_response(index=0, count=2), _response(index=0, count=2)],
        )
    with pytest.raises(ValidationError):
        KemoResponseBatch(
            request_id="req_batch",
            model="demo-model",
            responses=[_response(index=0, count=2)],
        )


def test_r11_resume_cursor_uses_local_anchor_and_rejects_other_response() -> None:
    guard = StreamSequenceGuard(start_after_sequence=3)
    event = ProviderStreamEvent(
        type=StreamEventType.OUTPUT_ITEM_ADDED,
        event_id="evt_resume",
        sequence=4,
        previous_sequence=3,
        request_id="req_resume",
        response_id="resp_resume",
        item_id="msg_resume",
        item=MessageItemStart(id="msg_resume"),
    )
    assert guard.accept(event)
    with pytest.raises(ValidationError):
        ProviderStreamEvent.model_validate(event.model_dump() | {"previous_sequence": 1})
    with pytest.raises(StreamProtocolError):
        guard.accept(event.model_copy(update={"event_id": "evt_other", "response_id": "resp_other"}))


def test_r12_and_r13_tool_call_streams_are_not_executable_before_terminal_confirmation() -> None:
    guard = StreamSequenceGuard()
    start = ProviderStreamEvent(
        type=StreamEventType.OUTPUT_ITEM_ADDED,
        event_id="evt_tool_start",
        sequence=0,
        request_id="req_tool_stream",
        response_id="resp_tool_stream",
        item_id="call_stream",
        item={"id": "call_stream", "type": "tool_call", "status": "in_progress", "call_id": "callid_stream", "name": "lookup"},
    )
    guard.accept(start)
    completed = ProviderStreamEvent(
        type=StreamEventType.TOOL_CALL_COMPLETED,
        event_id="evt_tool_completed",
        sequence=1,
        previous_sequence=0,
        request_id="req_tool_stream",
        response_id="resp_tool_stream",
        item_id="call_stream",
        call_id="callid_stream",
        item={"id": "call_stream", "type": "tool_call", "status": "completed", "call_id": "callid_stream", "name": "lookup", "arguments": {}},
    )
    assert guard.accept(completed)
    # A second completed event is a protocol violation; the executor may only
    # execute after the response.completed(requires_action) boundary.
    with pytest.raises(StreamProtocolError):
        guard.accept(completed.model_copy(update={"event_id": "evt_tool_completed_again", "sequence": 2, "previous_sequence": 1}))


def test_r14_usage_must_follow_text_done_and_missing_usage_is_nullable() -> None:
    guard = StreamSequenceGuard()
    guard.accept(ProviderStreamEvent(type="output_item.added", event_id="evt_u0", sequence=0, request_id="req_u", response_id="resp_u", item_id="msg_u", item=MessageItemStart(id="msg_u")))
    guard.accept(ProviderStreamEvent(type="output_text.delta", event_id="evt_u1", sequence=1, previous_sequence=0, request_id="req_u", response_id="resp_u", item_id="msg_u", content_index=0, delta="ok"))
    with pytest.raises(StreamProtocolError):
        guard.accept(ProviderStreamEvent(type="usage.updated", event_id="evt_u2", sequence=2, previous_sequence=1, request_id="req_u", response_id="resp_u", usage=Usage(total_tokens=1)))


def test_r15_request_conflicts_are_rejected_without_downgrade() -> None:
    with pytest.raises(ValidationError):
        _request(generation={"n": 2}, structured_output={"type": "json_object"})
    with pytest.raises(ValidationError):
        _request(stream=True, generation={"logprobs": {"enabled": True}})


def test_r16_parallel_default_is_harmless_without_tools() -> None:
    request = _request()
    assert request.parallel_tool_calls is True
    assert request.tools == []


def test_r17_structured_output_schema_shape_is_explicit() -> None:
    with pytest.raises(ValidationError):
        StructuredOutputConfig(type="json_schema", schema_name="x", schema={"type": "array"})
    with pytest.raises(ValidationError):
        StructuredOutputConfig(type="json_object", strict=True)


def test_r18_diagnostics_do_not_replace_business_metadata() -> None:
    request = _request(metadata={"top_k": 3}, extensions={"kemo.diagnostics": {"top_k": "unsupported"}})
    assert request.metadata["top_k"] == 3
    assert request.extensions["kemo.diagnostics"]["top_k"] == "unsupported"


def test_r19_image_inline_limit_is_exact() -> None:
    exact = base64.b64encode(b"x" * (1024 * 1024)).decode()
    value = ImageContent(type="image", mime_type="image/png", source={"kind": "inline_base64", "data": exact})
    assert value.source is not None
    with pytest.raises(ValidationError):
        ImageContent(type="image", mime_type="image/png", source={"kind": "inline_base64", "data": base64.b64encode(b"x" * (1024 * 1024 + 1)).decode()})


def test_r23_request_fingerprint_is_default_and_key_order_stable() -> None:
    left = {"protocol_version": "2.0", "request_id": "req_fp", "model": "m", "metadata": {"a": 1, "b": None}}
    right = {"model": "m", "metadata": {"b": None, "a": 1}, "request_id": "req_fp", "protocol_version": "2.0"}
    assert request_fingerprint(left) == request_fingerprint(right)
    assert request_fingerprint({**left, "metadata": {"a": 1.0, "b": None}}) != request_fingerprint(left)
    with pytest.raises(ProtocolValidationError):
        request_fingerprint('{"protocol_version":"2.0","protocol_version":"2.0","request_id":"req_fp","model":"m"}')


def test_r24_embedding_and_rerank_response_bounds_are_explicit() -> None:
    embedding = EmbeddingResponse(
        request_id="req_embed_response",
        model="embed",
        vector_space_id="space",
        dimensions=2,
        data=[EmbeddingData(id="q1", index=0, vector=[0.1, 0.2])],
        truncated_ids=["q2"],
    )
    assert embedding.truncated_ids == ["q2"]
    with pytest.raises(ValidationError):
        EmbeddingResponse(request_id="req_embed_bad", model="embed", vector_space_id="space", dimensions=2, data=[EmbeddingData(id="q1", index=0, vector=[0.1])])
    rerank = RerankResponse(
        request_id="req_rank_response",
        model="rank",
        results=[RerankResultItem(rank=1, document_id="d1", index=0, relevance_score=0.8)],
    )
    assert rerank.results[0].rank == 1
    with pytest.raises(ValidationError):
        RerankResponse(request_id="req_rank_bad", model="rank", results=[RerankResultItem(rank=2, document_id="d1", index=0, relevance_score=0.8)])


def test_r25_error_and_response_terminal_shapes_are_disjoint() -> None:
    with pytest.raises(ValidationError):
        KemoResponse(request_id="req_failed", model="m", status="failed", output=[])
    failed = KemoResponse(
        request_id="req_failed",
        model="m",
        status="failed",
        output=[],
        error={"type": "provider", "code": ErrorCode.PROVIDER_UNAVAILABLE, "message": "down"},
    )
    assert failed.error is not None


def test_l3_capability_gate_rejects_unsupported_request_without_mutating_it() -> None:
    request = _request(
        stream=True,
        output={"modalities": ["text"]},
        generation={"temperature": 0.5},
    )
    capabilities = ModelCapabilities.model_validate({
        "protocol_version": "2.0",
        "model": "demo-model",
        "provider_id": "demo",
        "provider_model": "demo-model",
        "streaming": False,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "decoding": {"temperature": False},
    })
    with pytest.raises(CapabilityError) as caught:
        validate_request_against_capabilities(request, capabilities)
    assert getattr(caught.value, "path", "") == "stream"
    assert request.stream is True


def test_inflight_response_does_not_fabricate_completed_at() -> None:
    response = KemoResponse(
        request_id="req_running",
        model="m",
        status="incomplete",
        output=[],
        incomplete_details={"reason": "running"},
    )
    assert response.completed_at is None
