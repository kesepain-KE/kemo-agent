"""Conversation orchestration domain."""

from importlib import import_module
from typing import Any

__all__ = ["EngineError", "handle_request"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from run.conversation.engine import EngineError, handle_request

        return {"EngineError": EngineError, "handle_request": handle_request}[name]
    for module_name in (
        "request_input",
        "guidance",
        "guidance_runtime",
        "usage",
        "run_state",
        "session_runtime",
        "provider_events",
        "round_finalizer",
        "runtime",
    ):
        module = import_module(f"run.conversation.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
