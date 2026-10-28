"""Source-scoped presence keys shared by lifecycle and transactional deletion."""
from __future__ import annotations

import sqlite3

_SEPARATOR = "\x1f"


def lease_client_key(source: str, client_id: str) -> str:
    if _SEPARATOR in source or _SEPARATOR in client_id:
        raise ValueError("租约身份不能包含来源分隔符")
    return client_id if source == "web" else f"{source}{_SEPARATOR}{client_id}"


def lease_scope_sql(source: str) -> tuple[str, str]:
    # Historical Web clients have no prefix. Exact comparison rather than LIKE
    # prevents '%'/'_' in another source from acting as wildcard identities.
    if source == "web":
        return "(instr(l.client_id, char(31))=0 AND ?='web')", source
    return "substr(l.client_id, 1, instr(l.client_id, char(31))-1)=?", source


def delete_session_leases(database: sqlite3.Connection, source: str, session_id: str) -> None:
    scope_sql, scope_value = lease_scope_sql(source)
    database.execute(
        f"DELETE FROM history_web_leases AS l WHERE l.session_id=? AND {scope_sql}",
        (session_id, scope_value),
    )
