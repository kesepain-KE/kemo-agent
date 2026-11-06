"""Indexed memory lookup; batch queries share one read snapshot."""
from __future__ import annotations

from run.infra.fulltext import compile_fulltext
from run.memory.sqlite_support import entry_from_row
from run.memory.store import connection


def search_batch(root, user, tiers, queries, limit):
    results = []
    with connection(root, user) as db:
        db.execute("BEGIN")
        for title, content in queries:
            fields = []
            for column, query in (("filename", title), ("content", content)):
                if query:
                    expression = compile_fulltext(db, query)
                    if expression:
                        fields.append(f"{column} : ({expression})")
            if not fields:
                results.append((0, []))
                continue
            selection = ("FROM memory_text_fts JOIN memory_fragments f ON f.id=memory_text_fts.rowid "
                         "WHERE memory_text_fts MATCH ? AND f.tier IN ("
                         + ",".join("?" for _ in tiers) + ")")
            args = [" OR ".join(fields), *tiers]
            total = db.execute("SELECT count(*) " + selection, args).fetchone()[0]
            rows = db.execute(
                "SELECT f.*,bm25(memory_text_fts,1.15,1.0) AS rank,"
                "simple_highlight_pos(memory_text_fts,0) AS title_positions,"
                "simple_highlight_pos(memory_text_fts,1) AS content_positions " + selection
                + " ORDER BY rank,f.weight DESC,f.filename_key LIMIT ?", [*args, limit],
            ).fetchall()
            results.append((total, [{
                **entry_from_row(row), "rank": float(row["rank"]),
                "title_positions": row["title_positions"], "content_positions": row["content_positions"],
            } for row in rows]))
    return results
