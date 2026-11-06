"""Session control-plane persistence in cards.sqlite3, never the retired DB."""
from __future__ import annotations

from pathlib import Path

from run.cards import CardStore, card_connection as connection
from run.history.registry_store import (
    mutate_registry_record, read_active_binding, read_registry_record,
    upsert_registry_record,
)
from run.history.session_listing import query_session_records, session_page_cursor


def database_path(root: Path, user: str) -> Path:
    return CardStore(root, user).path
