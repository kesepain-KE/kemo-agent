"""Shared, dependency-light runtime infrastructure."""

from importlib import import_module as _import_module

from run.infra.atomic_io import replace_with_retry
from run.infra.errors import ContextLengthExceededError, EngineError
from run.infra.process_utils import (
    cancellable_subprocess_kwargs,
    detached_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_pid_tree,
    terminate_process_tree,
    visible_subprocess_kwargs,
)

__all__ = [
    "ContextLengthExceededError",
    "EngineError",
    "cancellable_subprocess_kwargs",
    "detached_subprocess_kwargs",
    "handle_cli_request",
    "handle_cli_compress",
    "handle_cli_status",
    "hidden_subprocess_kwargs",
    "resolve_interactive_context",
    "replace_with_retry",
    "stream_cli_request",
    "terminate_pid_tree",
    "terminate_process_tree",
    "visible_subprocess_kwargs",
]

_LAZY_MODULES = ("log_store", "process_execution", "cli", "process_identity")


def __getattr__(name: str):
    for module_name in _LAZY_MODULES:
        module = _import_module(f"run.infra.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
