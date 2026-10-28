"""Shared, dependency-light runtime infrastructure."""

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from run.infra.runtime_diagnostics import diagnostic_text as diagnostic_text
    from run.infra.process_identity import process_snapshot as process_snapshot

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from run.infra.cli import (
        handle_cli_request as handle_cli_request, handle_cli_compress as handle_cli_compress,
        handle_cli_status as handle_cli_status, resolve_interactive_context as resolve_interactive_context,
        stream_cli_request as stream_cli_request,
    )

from importlib import import_module as _import_module
from run.infra.read_cache import BoundedReadCache, cached_read_text, invalidate_source_cache

from run.infra.atomic_io import replace_with_retry
from run.infra.async_utils import run_awaitable
from run.infra.errors import ContextLengthExceededError, EngineError
from run.infra.usage import (
    merge_usage,
    merge_usage_dict,
    new_usage_total,
    record_provider_request,
    usage_from_dict,
)
from run.infra.process_utils import (
    cancellable_subprocess_kwargs,
    detached_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_pid_tree,
    terminate_process_tree,
    visible_subprocess_kwargs,
)

__all__ = [
    "BoundedReadCache",
    "cached_read_text",
    "invalidate_source_cache",
    "ContextLengthExceededError",
    "EngineError",
    "merge_usage",
    "merge_usage_dict",
    "new_usage_total",
    "record_provider_request",
    "usage_from_dict",
    "cancellable_subprocess_kwargs",
    "detached_subprocess_kwargs",
    "handle_cli_request",
    "handle_cli_compress",
    "handle_cli_status",
    "hidden_subprocess_kwargs",
    "resolve_interactive_context",
    "replace_with_retry",
    "run_awaitable",
    "stream_cli_request",
    "terminate_pid_tree",
    "terminate_process_tree",
    "visible_subprocess_kwargs",
]

_LAZY_MODULES = (
    "sqlite_migrations",
    "runtime_diagnostics",
    "log_store",
    "process_execution",
    "cli",
    "process_identity",
    "usage",
)


def __getattr__(name: str):
    for module_name in _LAZY_MODULES:
        module = _import_module(f"run.infra.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
