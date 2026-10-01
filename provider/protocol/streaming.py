"""Typed provider SSE events, framing and sequence validation."""

from __future__ import annotations

import json
import uuid
import base64
import binascii
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import Field, field_validator, model_validator

from provider.protocol.enums import (
    MessageRole,
    MessagePhase,
    ResponseStatus,
    StreamEventType,
    TERMINAL_STREAM_EVENTS,
)
from provider.protocol.diagnostics import sanitize_provider_diagnostic
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
    _validate_identifier,
)


class MessageItemStart(ProtocolModel):
    id: str
    type: Literal["message"] = "message"
    status: Literal["in_progress"] = "in_progress"
    role: Literal["assistant"] = "assistant"
    phase: MessagePhase | None = None
    created_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_identifier(value, "msg_", "message item id")


class ReasoningItemStart(ProtocolModel):
    id: str
    type: Literal["reasoning"] = "reasoning"
    status: Literal["in_progress"] = "in_progress"
    created_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_identifier(value, "rs_", "reasoning item id")


class ToolCallItemStart(ProtocolModel):
    id: str
    type: Literal["tool_call"] = "tool_call"
    status: Literal["in_progress"] = "in_progress"
    call_id: str
    name: str
    created_at: datetime | None = None

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_identifier(value, "call_", "tool call item id")

    @field_validator("call_id")
    @classmethod
    def validate_call_id(cls, value: str) -> str:
        return _validate_identifier(value, "callid_", "tool call id")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("tool call name 不能为空")
        return value


ItemStart = Annotated[
    Union[MessageItemStart, ReasoningItemStart, ToolCallItemStart],
    Field(discriminator="type"),
]


class ProviderStreamEvent(ProtocolModel):
    type: StreamEventType
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    sequence: int = Field(ge=0)
    previous_sequence: int | None = Field(default=None, ge=0)
    request_id: str
    response_id: str
    item_id: str | None = None
    content_index: int | None = Field(default=None, ge=0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    delta: str | None = None
    text: str | None = None
    item: Item | ItemStart | None = None
    usage: Usage | None = None
    response: KemoResponse | None = None
    error: UnifiedError | None = None
    call_id: str | None = None
    name: str | None = None
    run_id: str | None = None
    run_sequence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal(self) -> "ProviderStreamEvent":
        _validate_identifier(self.event_id, "evt_", "event_id")
        _validate_identifier(self.request_id, "req_", "request_id")
        _validate_identifier(self.response_id, "resp_", "response_id")
        expected_previous = None if self.sequence == 0 else self.sequence - 1
        if self.previous_sequence != expected_previous:
            raise ValueError("previous_sequence 必须等于 sequence-1，首帧必须为 null")
        if (self.run_id is None) != (self.run_sequence is None):
            raise ValueError("run_id/run_sequence 必须同时出现或同时省略")
        allowed: dict[StreamEventType, set[str]] = {
            StreamEventType.RESPONSE_CREATED: set(),
            StreamEventType.RESPONSE_IN_PROGRESS: set(),
            StreamEventType.OUTPUT_ITEM_ADDED: {"item_id", "item"},
            StreamEventType.REASONING_SUMMARY_DELTA: {"item_id", "delta"},
            StreamEventType.REASONING_CONTENT_DELTA: {"item_id", "delta"},
            StreamEventType.TOOL_CALL_ARGUMENTS_DELTA: {"item_id", "call_id", "name", "delta"},
            StreamEventType.TOOL_CALL_COMPLETED: {"item_id", "call_id", "name", "item"},
            StreamEventType.OUTPUT_TEXT_DELTA: {"item_id", "content_index", "delta"},
            StreamEventType.OUTPUT_TEXT_DONE: {"item_id", "content_index", "text"},
            StreamEventType.OUTPUT_REFUSAL_DELTA: {"item_id", "delta"},
            StreamEventType.OUTPUT_REFUSAL_DONE: {"item_id", "text"},
            StreamEventType.OUTPUT_AUDIO_DELTA: {"item_id", "content_index", "delta"},
            StreamEventType.OUTPUT_MEDIA_COMPLETED: {"item_id", "content_index", "item"},
            StreamEventType.USAGE_UPDATED: {"usage"},
            StreamEventType.RESPONSE_COMPLETED: {"response"},
            StreamEventType.RESPONSE_INCOMPLETE: {"response"},
            StreamEventType.RESPONSE_FAILED: {"response"},
            StreamEventType.RESPONSE_CANCELLED: {"response"},
            StreamEventType.ERROR: {"error"},
        }
        present = {
            name
            for name in ("item_id", "content_index", "delta", "text", "item", "usage", "response", "error", "call_id", "name")
            if getattr(self, name) is not None
        }
        unexpected = present - allowed.get(self.type, set())
        if unexpected:
            raise ValueError(f"{self.type} 携带未定义事件字段：{sorted(unexpected)}")
        if self.type == StreamEventType.OUTPUT_ITEM_ADDED and not isinstance(
            self.item, (MessageItemStart, ReasoningItemStart, ToolCallItemStart)
        ):
            raise ValueError("output_item.added 必须包含 ItemStart")
        if self.type == StreamEventType.RESPONSE_CREATED and self.sequence != 0:
            raise ValueError("response.created 必须使用 sequence=0")
        if self.type in {
            StreamEventType.OUTPUT_TEXT_DELTA,
            StreamEventType.OUTPUT_AUDIO_DELTA,
            StreamEventType.REASONING_SUMMARY_DELTA,
            StreamEventType.REASONING_CONTENT_DELTA,
            StreamEventType.OUTPUT_REFUSAL_DELTA,
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
        if self.type in {
            StreamEventType.OUTPUT_TEXT_DONE,
        } and (self.item_id is None or self.text is None):
            raise ValueError(f"{self.type} 必须包含 item_id 和 text")
        if self.type == StreamEventType.OUTPUT_TEXT_DONE and self.content_index is None:
            raise ValueError("output_text.done 必须包含 content_index")
        if self.type == StreamEventType.OUTPUT_REFUSAL_DONE and (
            self.item_id is None or self.text is None
        ):
            raise ValueError("output_refusal.done 必须包含 item_id 和 text")
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
            raise StreamProtocolError(
                "SSE data 不是有效 JSON",
                details={
                    "parse_error": {
                        "kind": "invalid_json",
                        "line": exc.lineno,
                        "column": exc.colno,
                        "position": exc.pos,
                    }
                },
            ) from exc
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
                details={
                    "event": event_name,
                    "data": sanitize_provider_diagnostic(value),
                },
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
        self._request_id: str | None = None
        self._response_id: str | None = None
        self._event_ids: dict[str, bytes] = {}
        self._terminal: set[str] = set()
        self._started_items: dict[tuple[str, str], str] = {}
        self._text_parts: dict[tuple[str, str, int], list[str]] = {}
        self._open_text_blocks: set[tuple[str, str, int]] = set()
        self._refusal_parts: dict[tuple[str, str], list[str]] = {}
        self._done_blocks: set[tuple[str, str, int | None, str]] = set()
        self._completed_tool_calls: set[tuple[str, str]] = set()
        self._start_after_sequence = start_after_sequence
        self._allow_initial_offset = allow_initial_offset
        self._initial_offset_consumed = False

    def accept(self, event: ProviderStreamEvent) -> bool:
        if self._request_id is None:
            self._request_id = event.request_id
            self._response_id = event.response_id
        elif event.request_id != self._request_id or event.response_id != self._response_id:
            raise StreamProtocolError(
                "单条流的 request_id/response_id 必须保持不变",
                details={
                    "expected_request_id": self._request_id,
                    "expected_response_id": self._response_id,
                    "received_request_id": event.request_id,
                    "received_response_id": event.response_id,
                },
            )
        payload = event.model_dump_json(
            by_alias=True, exclude_none=True
        ).encode("utf-8")
        previous_payload = self._event_ids.get(event.event_id)
        if previous_payload is not None:
            if previous_payload != payload:
                raise StreamProtocolError(
                    f"event_id 重放内容冲突：{event.event_id}",
                    details={"event_id": event.event_id},
                )
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
        if event.type == StreamEventType.USAGE_UPDATED and self._open_text_blocks:
            raise StreamProtocolError(
                "usage.updated 必须在所有 output_text.done 之后发布"
            )
        if event.type in {
            StreamEventType.RESPONSE_COMPLETED,
        } and self._open_text_blocks:
            raise StreamProtocolError(
                "completed/requires_action 终态前必须闭合全部文本块"
            )
        self._accept_item_state(event)
        self._event_ids[event.event_id] = payload
        self._last_by_response[event.response_id] = event.sequence
        if event.terminal:
            self._terminal.add(event.response_id)
        return True

    def _accept_item_state(self, event: ProviderStreamEvent) -> None:
        if event.type == StreamEventType.OUTPUT_ITEM_ADDED:
            if event.item is None or event.item_id != event.item.id:
                raise StreamProtocolError("output_item.added 的 item_id 与 item 不一致")
            key = (event.response_id, event.item_id)
            item_type = str(event.item.type)
            previous = self._started_items.get(key)
            if previous is not None:
                raise StreamProtocolError(f"Item 重复 added：{event.item_id}")
            self._started_items[key] = item_type
            return

        item_events = {
            StreamEventType.OUTPUT_TEXT_DELTA,
            StreamEventType.OUTPUT_TEXT_DONE,
            StreamEventType.OUTPUT_AUDIO_DELTA,
            StreamEventType.OUTPUT_REFUSAL_DELTA,
            StreamEventType.OUTPUT_REFUSAL_DONE,
            StreamEventType.REASONING_SUMMARY_DELTA,
            StreamEventType.REASONING_CONTENT_DELTA,
            StreamEventType.TOOL_CALL_ARGUMENTS_DELTA,
            StreamEventType.TOOL_CALL_COMPLETED,
            StreamEventType.OUTPUT_MEDIA_COMPLETED,
        }
        if event.type in item_events and event.item_id is not None:
            item_key = (event.response_id, event.item_id)
            if item_key not in self._started_items:
                raise StreamProtocolError(f"Item 尚未 added：{event.item_id}")
            item_type = self._started_items[item_key]
            expected_types = {
                "message": {
                    StreamEventType.OUTPUT_TEXT_DELTA,
                    StreamEventType.OUTPUT_TEXT_DONE,
                    StreamEventType.OUTPUT_AUDIO_DELTA,
                    StreamEventType.OUTPUT_REFUSAL_DELTA,
                    StreamEventType.OUTPUT_REFUSAL_DONE,
                    StreamEventType.OUTPUT_MEDIA_COMPLETED,
                },
                "reasoning": {
                    StreamEventType.REASONING_SUMMARY_DELTA,
                    StreamEventType.REASONING_CONTENT_DELTA,
                },
                "tool_call": {
                    StreamEventType.TOOL_CALL_ARGUMENTS_DELTA,
                    StreamEventType.TOOL_CALL_COMPLETED,
                },
            }
            if event.type not in expected_types.get(item_type, set()):
                raise StreamProtocolError(
                    f"事件 {event.type} 与 Item 类型 {item_type} 不匹配"
                )

        if event.type == StreamEventType.OUTPUT_TEXT_DELTA:
            assert event.item_id is not None and event.content_index is not None
            done_key = (
                event.response_id,
                event.item_id,
                event.content_index,
                "text",
            )
            if done_key in self._done_blocks:
                raise StreamProtocolError("output_text.done 后不得继续 delta")
            key = (event.response_id, event.item_id, event.content_index)
            self._text_parts.setdefault(key, []).append(event.delta or "")
            self._open_text_blocks.add(key)
        elif event.type == StreamEventType.OUTPUT_TEXT_DONE:
            assert event.item_id is not None and event.content_index is not None
            done_key = (
                event.response_id,
                event.item_id,
                event.content_index,
                "text",
            )
            if done_key in self._done_blocks:
                raise StreamProtocolError("output_text.done 不得重复")
            key = (event.response_id, event.item_id, event.content_index)
            if "".join(self._text_parts.get(key, [])) != (event.text or ""):
                raise StreamProtocolError("output_text.done 与 delta 聚合结果不一致")
            self._done_blocks.add(done_key)
            self._open_text_blocks.discard(key)
        elif event.type == StreamEventType.OUTPUT_REFUSAL_DELTA:
            assert event.item_id is not None
            done_key = (event.response_id, event.item_id, None, "refusal")
            if done_key in self._done_blocks:
                raise StreamProtocolError("output_refusal.done 后不得继续 delta")
            key = (event.response_id, event.item_id)
            self._refusal_parts.setdefault(key, []).append(event.delta or "")
        elif event.type == StreamEventType.OUTPUT_REFUSAL_DONE:
            assert event.item_id is not None
            done_key = (event.response_id, event.item_id, None, "refusal")
            if done_key in self._done_blocks:
                raise StreamProtocolError("output_refusal.done 不得重复")
            key = (event.response_id, event.item_id)
            if "".join(self._refusal_parts.get(key, [])) != (event.text or ""):
                raise StreamProtocolError("output_refusal.done 与 delta 聚合结果不一致")
            self._done_blocks.add(done_key)
        elif event.type == StreamEventType.TOOL_CALL_COMPLETED:
            if event.item_id is None:
                raise StreamProtocolError("tool_call.completed 必须包含 item_id")
            key = (event.response_id, event.item_id)
            if key in self._completed_tool_calls:
                raise StreamProtocolError("tool_call.completed 不得重复")
            self._completed_tool_calls.add(key)
