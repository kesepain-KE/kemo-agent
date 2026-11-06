"""Ranked original-text search over each user's CardStream index."""
from __future__ import annotations

from run.history.archive_query_common import CONTENT, ROLE, TEXT_CARDS, VISIBLE, archive_database
from run.infra import compile_fulltext


def search_rows(root, user, query, *, source, session_id, since, until, role, limit, offset):
    clauses, args = [VISIBLE, TEXT_CARDS, "card_text_fts MATCH ?"], []
    with archive_database(root, user) as db:
        if db is None:
            return 0, []
        expression = compile_fulltext(db, query)
        if not expression:
            return 0, []
        args.append(expression)
        for column, value in (("source", source), ("session_id", session_id)):
            if value:
                clauses.append(f"s.{column}=?")
                args.append(value)
        if since:
            clauses.append("date(c.created_at,'+8 hours')>=?")
            args.append(since)
        if until:
            clauses.append("date(c.created_at,'+8 hours')<=?")
            args.append(until)
        if role != "any":
            clauses.append("c.raw_kind=?")
            args.append("user_text" if role == "user" else "agent_text")
        selection = ("FROM card_text_fts JOIN context_cards c ON c.rowid=card_text_fts.rowid "
                     "JOIN card_sessions s USING(session_key) WHERE " + " AND ".join(clauses))
        total = db.execute("SELECT count(*) " + selection, args).fetchone()[0]
        rows = db.execute(
            f"SELECT s.source,s.session_id,c.card_id,c.integration_id,c.seq,c.raw_kind,c.created_at,"
            f"{ROLE} AS role,{CONTENT} AS content,card_text_fts.rank AS rank,"
            "simple_highlight_pos(card_text_fts,0) AS positions " + selection
            + " ORDER BY card_text_fts.rank,c.created_at DESC,c.card_id DESC LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ).fetchall()
        return total, [dict(row) for row in rows]
