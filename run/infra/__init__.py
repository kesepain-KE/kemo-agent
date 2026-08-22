"""Shared, dependency-light runtime infrastructure."""

from importlib import import_module as _import_module

from run.infra.atomic_io import replace_with_retry
from run.infra.errors import ContextLengthExceededError, EngineError
from run.infra.process_utils import (
    cancellable_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_process_tree,
)

__all__ = [
    "ContextLengthExceededError",
    "EngineError",
    "cancellable_subprocess_kwargs",
    "hidden_subprocess_kwargs",
    "replace_with_retry",
    "terminate_process_tree",
]

_LAZY_MODULES = ("log_store", "process_execution", "cli")


def __getattr__(name: str):
    for module_name in _LAZY_MODULES:
        module = _import_module(f"run.infra.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
