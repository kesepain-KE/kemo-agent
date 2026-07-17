"""轻量级文件知识索引和确定性检索。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


_TEXT_SUFFIXES = frozenset({".md", ".txt", ".json"})
_ASCII_WORD = re.compile(r"[A-Za-z0-9_./-]{2,}")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]{2,}")
_INDEX_NAMES = frozenset({"index.md", "data_structure.md", "索引.md", "目录.md"})


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    scope: str
    path: Path
    relative_path: str
    title: str
    content: str


@dataclass(frozen=True, slots=True)
class KnowledgeSelection:
    documents: tuple[KnowledgeDocument, ...]
    text: str


class KnowledgeError(RuntimeError):
    pass


def _terms(text: str) -> set[str]:
    lowered = text.casefold()
    terms = {item.casefold() for item in _ASCII_WORD.findall(lowered)}
    for run in _CJK_RUN.findall(lowered):
        if len(run) <= 4:
            terms.add(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return terms


def _read_text(path: Path, *, max_file_chars: int) -> str:
    try:
        content = path.read_text("utf-8-sig")
    except (OSError, UnicodeError):
        return ""
    return content[:max_file_chars].strip()


def _title(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return path.stem


def _iter_documents(base: Path, scope: str, *, max_file_chars: int) -> Iterable[KnowledgeDocument]:
    if not base.is_dir():
        return
    for path in sorted(base.rglob("*"), key=lambda item: str(item).casefold()):
        if not path.is_file() or path.suffix.casefold() not in _TEXT_SUFFIXES:
            continue
        if any(part.startswith(".") for part in path.relative_to(base).parts):
            continue
        content = _read_text(path, max_file_chars=max_file_chars)
        if not content:
            continue
        yield KnowledgeDocument(
            scope=scope,
            path=path,
            relative_path=path.relative_to(base).as_posix(),
            title=_title(path, content),
            content=content,
        )


def build_index(root: Path, user: str, *, max_file_chars: int = 20000) -> tuple[KnowledgeDocument, ...]:
    """Build an in-memory index. User documents are ordered before shared documents."""
    user_base = root / "users" / user / "knowledge"
    global_base = root / "global_knowledge"
    return tuple(
        [*_iter_documents(user_base, "user", max_file_chars=max_file_chars)]
        + [*_iter_documents(global_base, "global", max_file_chars=max_file_chars)]
    )


def select_knowledge(
    root: Path,
    user: str,
    query: str,
    config: dict,
) -> KnowledgeSelection:
    knowledge_config = config.get("knowledge") or {}
    if not bool(knowledge_config.get("enabled", True)) or not query.strip():
        return KnowledgeSelection((), "")
    max_items = max(0, int(knowledge_config.get("max_items", 4)))
    max_chars = max(0, int(knowledge_config.get("max_chars", 4000)))
    max_file_chars = max(1000, int(knowledge_config.get("max_file_chars", 20000)))
    minimum_score = max(1, int(knowledge_config.get("minimum_score", 2)))
    if max_items == 0 or max_chars == 0:
        return KnowledgeSelection((), "")

    query_terms = _terms(query)
    if not query_terms:
        return KnowledgeSelection((), "")
    ranked: list[tuple[int, int, str, KnowledgeDocument]] = []
    for document in build_index(root, user, max_file_chars=max_file_chars):
        title_terms = _terms(f"{document.title} {document.relative_path}")
        content_terms = _terms(document.content)
        title_hits = len(query_terms & title_terms)
        content_hits = len(query_terms & content_terms)
        score = title_hits * 4 + content_hits
        if document.path.name.casefold() in _INDEX_NAMES:
            score += title_hits
        if score < minimum_score:
            continue
        scope_priority = 0 if document.scope == "user" else 1
        ranked.append((-score, scope_priority, document.relative_path.casefold(), document))
    ranked.sort(key=lambda item: (item[0], item[1], item[2]))

    selected: list[KnowledgeDocument] = []
    sections: list[str] = []
    used = 0
    for _, _, _, document in ranked[:max_items]:
        header = f"[knowledge:{document.scope}:{document.relative_path}]\n"
        remaining = max_chars - used - len(header)
        if remaining <= 0:
            break
        body = document.content[:remaining].strip()
        if not body:
            continue
        section = header + body
        sections.append(section)
        selected.append(document)
        used += len(section) + 2
        if used >= max_chars:
            break
    return KnowledgeSelection(tuple(selected), "\n\n".join(sections))
