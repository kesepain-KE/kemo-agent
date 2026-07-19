from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from run.knowledge import build_index


_TERM = re.compile(r"[A-Za-z0-9_./-]+|[\u3400-\u9fff]{2,}")


def _terms(text: str) -> set[str]:
    return {item.casefold() for item in _TERM.findall(text)}


def run(
    query: str,
    scopes: list[str] | None = None,
    limit: int = 5,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context["root"])
    user = str(context["user"])
    requested = set(scopes or ["user", "shared", "global"])
    invalid = requested - {"user", "shared", "global"}
    if invalid:
        raise ValueError(f"未知知识范围：{', '.join(sorted(invalid))}")
    granted_raw = context.get("knowledge_scopes")
    granted = (
        set(granted_raw)
        if isinstance(granted_raw, list)
        else {"user", "shared", "global"}
    )
    effective = requested & granted
    needles = _terms(query)
    ranked: list[tuple[int, int, str, Any]] = []
    scope_rank = {"user": 0, "shared": 1, "global": 2}
    for document in build_index(root, user, scopes=effective):
        if document.scope not in effective:
            continue
        title_terms = _terms(f"{document.title} {document.relative_path}")
        body_terms = _terms(document.content)
        score = len(needles & title_terms) * 4 + len(needles & body_terms)
        if score:
            ranked.append((-score, scope_rank[document.scope], document.relative_path.casefold(), document))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    matches = []
    for _, _, _, document in ranked[:limit]:
        content = document.content
        matches.append(
            {
                "scope": document.scope,
                "path": document.relative_path,
                "title": document.title,
                "snippet": content[:800],
                "truncated": len(content) > 800,
            }
        )
    return {
        "query": query,
        "requested_scopes": sorted(requested),
        "effective_scopes": sorted(effective),
        "matches": matches,
    }
