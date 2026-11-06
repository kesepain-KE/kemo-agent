"""Public history API: immutable CardStream RAW plus session metadata."""
from run.history.index import (
    chain_for_source, claim_pending_memory, claim_pending_summary, close_session,
    defer_memory_claim, defer_summary_claim, find_record, finish_memory_claim,
    finish_summary_claim, get_active, get_or_reserve_active, index_lock, list_records,
    list_records_page, new_conversation_id, queue_summary, reopen_session,
    reserve_session, retry_summary, set_active, update_run_state,
    update_summary_checkpoint, update_title,
)
from run.history.session_api import (
    HistoryError, clear_session, delete_all_sessions, delete_session,
    ensure_session_appendable, list_sessions, list_sessions_page,
    queue_memory_extraction, rename_session, session_messages, withdraw_last_integration,
    session_statistics, integration_usage_rows,
)
from run.history.session_payload import session_payload
from run.history.session_marks import favorite_count, update_session_marks
from run.history.store import (
    connection, database_path, mutate_registry_record, read_registry_record,
    session_page_cursor, upsert_registry_record,
)
from run.history.archive_query_common import raw_search_rows, raw_text_context
from run.history.fulltext import search_rows as fulltext_search_rows

_DOMAIN_MODULES = (
    "archive_catalog", "archive_reader", "lease_presence", "web_lifecycle",
    "retention", "summary_scheduler",
)


def __getattr__(name):
    from importlib import import_module
    for module in _DOMAIN_MODULES:
        value = import_module(f"run.history.{module}")
        if hasattr(value, name):
            return getattr(value, name)
    raise AttributeError(name)
