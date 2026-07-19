"""Stable enums for the kemo unified provider protocol."""

from __future__ import annotations

from enum import StrEnum


class ItemStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class ResponseStatus(StrEnum):
    COMPLETED = "completed"
    REQUIRES_ACTION = "requires_action"
    INCOMPLETE = "incomplete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessagePhase(StrEnum):
    COMMENTARY = "commentary"
    FINAL_ANSWER = "final_answer"


class MeasurementMode(StrEnum):
    PROVIDER = "provider"
    GATEWAY = "gateway"
    ESTIMATED = "estimated"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class StreamEventType(StrEnum):
    RESPONSE_CREATED = "response.created"
    OUTPUT_ITEM_ADDED = "output_item.added"
    REASONING_SUMMARY_DELTA = "reasoning.summary.delta"
    REASONING_CONTENT_DELTA = "reasoning.content.delta"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call.arguments.delta"
    TOOL_CALL_COMPLETED = "tool_call.completed"
    OUTPUT_TEXT_DELTA = "output_text.delta"
    OUTPUT_AUDIO_DELTA = "output_audio.delta"
    OUTPUT_MEDIA_COMPLETED = "output_media.completed"
    USAGE_UPDATED = "usage.updated"
    RESPONSE_COMPLETED = "response.completed"
    RESPONSE_INCOMPLETE = "response.incomplete"
    RESPONSE_FAILED = "response.failed"
    RESPONSE_CANCELLED = "response.cancelled"
    ERROR = "error"


TERMINAL_STREAM_EVENTS = frozenset(
    {
        StreamEventType.RESPONSE_COMPLETED,
        StreamEventType.RESPONSE_INCOMPLETE,
        StreamEventType.RESPONSE_FAILED,
        StreamEventType.RESPONSE_CANCELLED,
        StreamEventType.ERROR,
    }
)
