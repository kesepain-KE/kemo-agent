"""Public facade for conversation history and its SQLite-backed domains."""

from run.history.history_models import HistoryError, empty_window, synthesize_items
from run.history.commit_ops import (
    append_round_items,
    commit_terminal_windows,
    commit_window,
    patch_archive_metadata,
)
from run.history.runtime_window import (
    _trim_to_max_rounds,
    load_runtime_window,
    load_window,
    runtime_window_path,
    undo_last_round,
)
from run.history.session_api import (
    clear_session,
    delete_all_sessions,
    delete_session,
    find_window,
    get_or_create_window,
    list_sessions,
    list_sessions_page,
    prepare_window,
    queue_memory_extraction,
    rename_session,
    session_messages,
)

_DOMAIN_MODULES = ("index", "store", "summary_scheduler")


def __getattr__(name: str):
    from importlib import import_module

    for module_name in _DOMAIN_MODULES:
        module = import_module(f"run.history.{module_name}")
        if hasattr(module, name):
            return getattr(module, name)
    raise AttributeError(name)
