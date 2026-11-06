"""Errors belonging to CardStream, independent of the legacy window runtime."""


class CardError(RuntimeError):
    """CardStream cannot safely complete an operation."""


class CardConflictError(CardError):
    """The caller's snapshot or event identity is no longer current."""


class CardCapacityError(CardError):
    """The complete request cannot fit without losing historical semantics."""


class CardCompactionError(CardError):
    """No valid, sufficiently small replacement view could be produced."""


class CardSessionClosedError(CardError):
    """Closed/deleted sessions cannot silently acquire new historical events."""
