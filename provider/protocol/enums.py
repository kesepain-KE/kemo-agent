"""Stable enums for the kemo unified provider protocol."""

from __future__ import annotations

try:
    from enum import StrEnum
except ImportError:  # Python 3.10 compatibility
    from enum import Enum

    class StrEnum(str, Enum):
        """Minimal stdlib-compatible fallback for enums with explicit values."""

        def __str__(self) -> str:
            return self.value


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
    DEVELOPER = "developer"
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
    RESPONSE_IN_PROGRESS = "response.in_progress"
    OUTPUT_ITEM_ADDED = "output_item.added"
    REASONING_SUMMARY_DELTA = "reasoning.summary.delta"
    REASONING_CONTENT_DELTA = "reasoning.content.delta"
    TOOL_CALL_ARGUMENTS_DELTA = "tool_call.arguments.delta"
    TOOL_CALL_COMPLETED = "tool_call.completed"
    OUTPUT_TEXT_DELTA = "output_text.delta"
    OUTPUT_TEXT_DONE = "output_text.done"
    OUTPUT_REFUSAL_DELTA = "output_refusal.delta"
    OUTPUT_REFUSAL_DONE = "output_refusal.done"
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


class IncompleteReason(StrEnum):
    OUTPUT_TRUNCATED = "output_truncated"
    CONTENT_FILTERED = "content_filtered"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    MISSING_TOOL_CALL = "missing_tool_call"
    EMPTY_OUTPUT = "empty_output"
    UPSTREAM_STOPPED = "upstream_stopped"
    CANCELLED = "cancelled"
    RUNNING = "running"
    OTHER = "other"


class ToolChoiceMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    REQUIRED = "required"
    NAMED = "named"
    ALLOWED = "allowed"


class AnnotationType(StrEnum):
    URL_CITATION = "url_citation"
    FILE_CITATION = "file_citation"


class ErrorCode(StrEnum):
    PROTOCOL_ERROR = "PROTOCOL_ERROR"
    UNSUPPORTED_PROTOCOL_VERSION = "UNSUPPORTED_PROTOCOL_VERSION"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    TOOL_LINKAGE_ERROR = "TOOL_LINKAGE_ERROR"
    STREAM_PROTOCOL_ERROR = "STREAM_PROTOCOL_ERROR"
    CAPABILITY_ERROR = "CAPABILITY_ERROR"
    CAPABILITIES_MODEL_MISMATCH = "CAPABILITIES_MODEL_MISMATCH"
    MODEL_TASK_MISMATCH = "MODEL_TASK_MISMATCH"
    UNSUPPORTED_INPUT_MODALITY = "UNSUPPORTED_INPUT_MODALITY"
    UNSUPPORTED_OUTPUT_MODALITY = "UNSUPPORTED_OUTPUT_MODALITY"
    STREAMING_UNSUPPORTED = "STREAMING_UNSUPPORTED"
    TOOLS_UNSUPPORTED = "TOOLS_UNSUPPORTED"
    PARALLEL_TOOLS_UNSUPPORTED = "PARALLEL_TOOLS_UNSUPPORTED"
    REASONING_UNSUPPORTED = "REASONING_UNSUPPORTED"
    REASONING_EFFORT_UNSUPPORTED = "REASONING_EFFORT_UNSUPPORTED"
    STRUCTURED_OUTPUT_UNSUPPORTED = "STRUCTURED_OUTPUT_UNSUPPORTED"
    PREDICTION_UNSUPPORTED = "PREDICTION_UNSUPPORTED"
    ASSET_ERROR = "ASSET_ERROR"
    ASSET_NOT_FOUND = "ASSET_NOT_FOUND"
    ASSET_NOT_READY = "ASSET_NOT_READY"
    ASSET_DELETED = "ASSET_DELETED"
    ASSET_EXPIRED = "ASSET_EXPIRED"
    ASSET_CONTENT_MISSING = "ASSET_CONTENT_MISSING"
    ASSET_DELETE_FAILED = "ASSET_DELETE_FAILED"
    ASSET_API_UNAVAILABLE = "ASSET_API_UNAVAILABLE"
    INVALID_MEDIA = "INVALID_MEDIA"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_BAD_RESPONSE = "PROVIDER_BAD_RESPONSE"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    GATEWAY_OVERLOADED = "GATEWAY_OVERLOADED"
    GATEWAY_DRAINING = "GATEWAY_DRAINING"
    STREAM_RESUME_CONFLICT = "STREAM_RESUME_CONFLICT"
    CANCELLED = "CANCELLED"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    AUTHENTICATION_ERROR = "AUTHENTICATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    MODEL_NOT_FOUND = "MODEL_NOT_FOUND"
    RATE_LIMITED = "RATE_LIMITED"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
