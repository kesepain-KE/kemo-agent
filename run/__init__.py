"""Lazy public API for the run core.

Keeping imports lazy prevents package initialization from pulling the complete
engine into plugin and agent discovery paths.
"""

from typing import Any


__all__ = ["EngineError", "handle_request"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from run.engine import EngineError, handle_request

        return {"EngineError": EngineError, "handle_request": handle_request}[name]
    raise AttributeError(name)
