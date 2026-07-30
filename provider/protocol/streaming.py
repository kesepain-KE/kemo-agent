"""Typed provider SSE events, framing and sequence validation."""

from __future__ import annotations

import json
import uuid
import base64
import binascii
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from pydantic import Field, model_validator

from provider.protocol.enums import (
    MessageRole,
    ResponseStatus,
    StreamEventType,
    TERMINAL_STREAM_EVENTS,
)
from provider.protocol.errors import StreamProtocolError
from provider.protocol.models import (
    Item,
    KemoResponse,
    MessageItem,
    ProtocolModel,
    ToolCallItem,
    UnifiedError,
    Usage,
    _validate_output_media_item,
)


class ProviderStreamEvent(ProtocolModel):
    type: StreamEventType
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    sequence: int = Field(ge=0)
    request_id: str
    response_id: str
    item_id: str | None = None
    content_index: int | None = Field(default=None, ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)
    delta: str | None = None
    item: Item | None = None
    usage: Usage | None = None
    response: KemoResponse | None = None
    error: UnifiedError | None = None
    call_id: str | None = None
    name: str | None = None
    run_id: str | None = None
    run_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal(self) -> "ProviderStreamEvent":
        if self.type in {
            StreamEventType.OUTPUT_TEXT_DELTA,
            StreamEventType.OUTPUT_AUDIO_DELTA,
            StreamEventType.REASONING_SUMMARY_DELTA,
            StreamEventType.REASONING_CONTENT_DELTA,
        } and (self.item_id is None or self.delta is None):
            raise ValueError(f"{self.type} 必须包含 item_id 和 delta")
        if self.type in {
            StreamEventType.OUTPUT_TEXT_DELTA,
            StreamEventType.OUTPUT_AUDIO_DELTA,
        } and self.content_index is None:
            raise ValueError(f"{self.type} 必须包含 content_index")
        if self.type == StreamEventType.OUTPUT_AUDIO_DELTA and self.delta is not None:
            try:
                decoded = base64.b64decode(self.delta, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("output_audio.delta 必须是有效 Base64") from exc
            if not decoded:
                raise ValueError("output_audio.delta 不能是空音频片段")
        if self.type == StreamEventType.TOOL_CALL_ARGUMENTS_DELTA and not all(
            (self.item_id, self.call_id, self.name, self.delta is not None)
        ):
            raise ValueError(
                "tool_call.arguments.delta 必须包含 item_id/call_id/name/delta"
            )
        if self.type == StreamEventType.TOOL_CALL_COMPLETED:
            if not isinstance(self.item, ToolCallItem):
                raise ValueError("tool_call.completed 必须包含完整 ToolCallItem")
            if self.call_id is not None and self.item.call_id != self.call_id:
                raise ValueError("tool_call.completed 的 call_id 与 item 不一致")
        if self.type == StreamEventType.OUTPUT_MEDIA_COMPLETED:
            if not isinstance(self.item, MessageItem) or self.item.role != MessageRole.ASSISTANT:
                raise ValueError("output_media.completed 必须包含 assistant MessageItem")
            if self.item_id != self.item.id:
                raise ValueError("output_media.completed 的 item_id 与 item 不一致")
            _validate_output_media_item(self.item, require_media=True)
        if self.type == StreamEventType.USAGE_UPDATED and self.usage is None:
            raise ValueError("usage.updated 必须包含 usage")
        if self.type in {
            StreamEventType.RESPONSE_COMPLETED,
            StreamEventType.RESPONSE_INCOMPLETE,
            StreamEventType.RESPONSE_FAILED,
            StreamEventType.RESPONSE_CANCELLED,
        } and self.response is None:
            raise ValueError(f"{self.type} 必须包含完整 response")
        expected_statuses = {
            StreamEventType.RESPONSE_COMPLETED: {
                ResponseStatus.COMPLETED,
                ResponseStatus.REQUIRES_ACTION,
            },
            StreamEventType.RESPONSE_INCOMPLETE: {ResponseStatus.INCOMPLETE},
            StreamEventType.RESPONSE_FAILED: {ResponseStatus.FAILED},
            StreamEventType.RESPONSE_CANCELLED: {ResponseStatus.CANCELLED},
        }
        expected = expected_statuses.get(self.type)
        if expected is not None and (
            self.response is None or self.response.status not in expected
        ):
            raise ValueError(f"{self.type} 必须包含匹配状态的完整 KemoResponse")
        if self.type == StreamEventType.ERROR and self.error is None:
            raise ValueError("error 事件必须包含 error 对象")
        return self

    @property
    def terminal(self) -> bool:
        return self.type in TERMINAL_STREAM_EVENTS


def encode_sse(event: ProviderStreamEvent) -> bytes:
    payload = event.model_dump_json(by_alias=True, exclude_none=True)
    return (
        f"id: {event.event_id}\n"
        f"event: {event.type}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


def iter_sse_payloads(lines: Iterable[bytes | str]) -> Iterator[tuple[str, str, str]]:
    event_id = ""
    event_name = "message"
    data_lines: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if not line:
            if data_lines:
                yield event_id, event_name, "\n".join(data_lines)
            event_id = ""
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, separator, value = line.partition(":")
        if not separator:
            continue
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield event_id, event_name, "\n".join(data_lines)


def parse_sse_events(lines: Iterable[bytes | str]) -> Iterator[ProviderStreamEvent]:
    for event_id, event_name, payload in iter_sse_payloads(lines):
        if payload == "[DONE]":
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise StreamProtocolError("SSE data 不是有效 JSON", details={"data": payload[:500]}) from exc
        if not isinstance(value, dict):
            raise StreamProtocolError("SSE data 根节点必须是对象")
        if event_id and "event_id" not in value:
            value["event_id"] = event_id
        if event_name != "message" and "type" not in value:
            value["type"] = event_name
        try:
            yield ProviderStreamEvent.model_validate(value)
        except Exception as exc:
            raise StreamProtocolError(
                f"统一流事件校验失败：{exc}",
                details={"event": event_name, "data": value},
            ) from exc


class StreamSequenceGuard:
    """Validate per-response ordering and de-duplicate event IDs."""

    def __init__(
        self,
        *,
        start_after_sequence: int | None = None,
        allow_initial_offset: bool = False,
    ) -> None:
        if start_after_sequence is not None and start_after_sequence < 0:
            raise ValueError("start_after_sequence 不能小于 0")
        self._last_by_response: dict[str, int] = {}
        self._event_ids: set[str] = set()
        self._terminal: set[str] = set()
        self._start_after_sequence = start_after_sequence
        self._allow_initial_offset = allow_initial_offset
        self._initial_offset_consumed = False

    def accept(self, event: ProviderStreamEvent) -> bool:
        if event.event_id in self._event_ids:
            return False
        if event.response_id in self._terminal:
            raise StreamProtocolError(
                f"终态后仍收到事件：{event.response_id}",
                details={"event_id": event.event_id},
            )
        previous = self._last_by_response.get(event.response_id)
        if previous is not None:
            expected = previous + 1
        elif not self._initial_offset_consumed and self._start_after_sequence is not None:
            expected = self._start_after_sequence + 1
            self._initial_offset_consumed = True
        elif not self._initial_offset_consumed and self._allow_initial_offset:
            expected = event.sequence
            self._initial_offset_consumed = True
        else:
            expected = 0
        if event.sequence != expected:
            raise StreamProtocolError(
                f"sequence 不连续：期望 {expected}，收到 {event.sequence}",
                details={"response_id": event.response_id},
            )
        self._event_ids.add(event.event_id)
        self._last_by_response[event.response_id] = event.sequence
        if event.terminal:
            self._terminal.add(event.response_id)
        return True
