"""Public runtime errors shared by the conversation execution modules."""

from __future__ import annotations


class EngineError(RuntimeError):
    """The run core rejected or failed a conversation request."""


class ContextLengthExceededError(EngineError):
    """The Provider rejected the request because its context is too large."""
