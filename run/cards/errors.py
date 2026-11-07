"""Errors belonging to CardStream, independent of the legacy window runtime."""


from typing import TYPE_CHECKING, Any


class CardError(RuntimeError):
    """CardStream cannot safely complete an operation."""

    if TYPE_CHECKING:
        compression_usage: dict[str, Any]
        compression_latency_ms: int


class CardConflictError(CardError):
    """The caller's snapshot or event identity is no longer current."""


class CardCapacityError(CardError):
    """The complete request cannot fit without losing historical semantics."""


class CardCompactionError(CardError):
    """No valid, sufficiently small replacement view could be produced."""

    if TYPE_CHECKING:
        response_status: str
        incomplete_reason: str
        finish_reason: str
        provider_response_id: str
        retry_recovery: str
        retry_output_tokens: int
        retry_candidate: bool
        repair_feedback: str


class CardSessionClosedError(CardError):
    """Closed/deleted sessions cannot silently acquire new historical events."""
