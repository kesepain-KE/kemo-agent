"""Executable subset of the Kemo 2.0 P.4 acceptance matrix.

These tests intentionally exercise cross-field/stream invariants that a JSON
Schema alone cannot express.  The IDs in the test names map directly to the
R01/R08/R09/R10/R19 rows in the design specification.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from provider.protocol.enums import StreamEventType
from provider.protocol.errors import StreamProtocolError
from provider.protocol.models import AudioContent, KemoRequest, MessageItem
from provider.protocol.streaming import (
    MessageItemStart,
    ProviderStreamEvent,
    StreamSequenceGuard,
)


def _event(
    event_type: StreamEventType,
    sequence: int,
    *,
    event_id: str,
    item_id: str | None = None,
    content_index: int | None = None,
    delta: str | None = None,
    text: str | None = None,
    item: object | None = None,
) -> ProviderStreamEvent:
    return ProviderStreamEvent(
        type=event_type,
        event_id=event_id,
        sequence=sequence,
        previous_sequence=None if sequence == 0 else sequence - 1,
        request_id="req_matrix",
        response_id="resp_matrix",
        item_id=item_id,
        content_index=content_index,
        delta=delta,
        text=text,
        item=item,
    )


def test_r01_request_version_is_required_and_exact() -> None:
    with pytest.raises(ValidationError):
        KemoRequest.model_validate(
            {
                "request_id": "req_matrix",
                "model": "m",
                "input": [],
            }
        )
    with pytest.raises(ValidationError):
        KemoRequest.model_validate(
            {
                "protocol_version": "2.1",
                "request_id": "req_matrix",
                "model": "m",
                "input": [],
            }
        )


def test_r08_item_start_is_not_a_final_empty_item() -> None:
    start = MessageItemStart(id="msg_matrix")
    assert start.status == "in_progress"
    with pytest.raises(ValidationError):
        MessageItem(id="msg_matrix", role="assistant", content=[])


def test_r09_content_index_is_scoped_by_item_and_done_closes_only_that_block() -> None:
    guard = StreamSequenceGuard()
    guard.accept(
        _event(
            StreamEventType.OUTPUT_ITEM_ADDED,
            0,
            event_id="evt_start_a",
            item_id="msg_a",
            item=MessageItemStart(id="msg_a"),
        )
    )
    guard.accept(
        _event(
            StreamEventType.OUTPUT_TEXT_DELTA,
            1,
            event_id="evt_delta_a",
            item_id="msg_a",
            content_index=0,
            delta="a",
        )
    )
    guard.accept(
        _event(
            StreamEventType.OUTPUT_ITEM_ADDED,
            2,
            event_id="evt_start_b",
            item_id="msg_b",
            item=MessageItemStart(id="msg_b"),
        )
    )
    guard.accept(
        _event(
            StreamEventType.OUTPUT_TEXT_DELTA,
            3,
            event_id="evt_delta_b",
            item_id="msg_b",
            content_index=0,
            delta="b",
        )
    )
    guard.accept(
        _event(
            StreamEventType.OUTPUT_TEXT_DONE,
            4,
            event_id="evt_done_a",
            item_id="msg_a",
            content_index=0,
            text="a",
        )
    )
    with pytest.raises(StreamProtocolError):
        guard.accept(
            _event(
                StreamEventType.OUTPUT_TEXT_DELTA,
                5,
                event_id="evt_delta_after_done",
                item_id="msg_a",
                content_index=0,
                delta="!",
            )
        )


def test_r10_event_replay_is_idempotent_but_payload_conflict_is_rejected() -> None:
    guard = StreamSequenceGuard()
    event = _event(
        StreamEventType.OUTPUT_ITEM_ADDED,
        0,
        event_id="evt_replay",
        item_id="msg_replay",
        item=MessageItemStart(id="msg_replay"),
    )
    assert guard.accept(event) is True
    assert guard.accept(event) is False
    with pytest.raises(StreamProtocolError):
        guard.accept(event.model_copy(update={"item_id": "msg_other"}))


def test_r19_non_image_inline_media_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AudioContent(
            asset_id="asset_audio",
            mime_type="audio/wav",
            source={"kind": "inline_base64", "data": "UklGRg=="},
        )
