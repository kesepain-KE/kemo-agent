"""轻量级文件知识索引和确定性检索。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

from run.prompt_sources import iter_files, read_required_text, truncate_chars
from run.source_policy import MainAgentSourcePolicy


_TEXT_SUFFIXES = frozenset({".md", ".txt", ".json"})
_ASCII_WORD = re.compile(r"[A-Za-z0-9_./-]{2,}")
_CJK_RUN = re.compile(r"[\u3400-\u9fff]{2,}")
_INDEX_NAMES = frozenset({"index.md", "data_structure.md", "索引.md", "目录.md"})
DEFAULT_KNOWLEDGE_MAX_ITEMS = 4
DEFAULT_KNOWLEDGE_MAX_CHARS = 4000
DEFAULT_KNOWLEDGE_MAX_FILE_CHARS = 20000
DEFAULT_KNOWLEDGE_MINIMUM_SCORE = 2


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


@dataclass(frozen=True, slots=True)
class KnowledgeIndexSelection:
    documents: tuple[KnowledgeDocument, ...]
    text: str
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool


class KnowledgeError(RuntimeError):
    pass


def select_knowledge_index(
    root: Path,
    user: str,
    *,
    max_chars: int,
    mode: str = "full",
    scopes: tuple[str, ...] = ("user", "shared", "global"),
) -> KnowledgeIndexSelection:
    """Select only named index files in user → shared → global order."""

    if mode != "full":
        raise KnowledgeError(f"knowledge_index 注入模式暂不支持：{mode}")
    if max_chars == 0:
        return KnowledgeIndexSelection((), "", 0, 0, 0, 0, False)
    available_scopes = (
        ("user", root / "users" / user / "knowledge"),
        ("shared", root / "shared_knowledge"),
        ("global", root / "global_knowledge"),
    )
    documents: list[KnowledgeDocument] = []
    pieces: list[str] = []
    offsets: list[int] = []
    used = 0
    allowed_scopes = set(scopes)
    for scope, base in available_scopes:
        if scope not in allowed_scopes:
            continue
        for path in iter_files(base, names=_INDEX_NAMES):
            content = read_required_text(path)
            if not content:
                continue
            relative = path.relative_to(base).as_posix()
            document = KnowledgeDocument(
                scope=scope,
                path=path,
                relative_path=relative,
                title=_title(path, content),
                content=content,
            )
            piece = f"[{scope}:{relative}]\n{content}"
            offsets.append(used + (2 if pieces else 0))
            pieces.append(piece)
            documents.append(document)
            used += len(piece) + (2 if len(pieces) > 1 else 0)
    full_text = "\n\n".join(pieces)
    text, truncated = truncate_chars(full_text, max_chars)
    injected_count = sum(offset < len(text) for offset in offsets)
    injected_documents = tuple(documents[:injected_count])
    return KnowledgeIndexSelection(
        injected_documents,
        text,
        len(full_text),
        len(text),
        len(documents),
        injected_count,
        truncated,
    )


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


def build_index(
    root: Path,
    user: str,
    *,
    scopes: Iterable[str] = ("user", "shared", "global"),
    max_file_chars: int = 20000,
) -> tuple[KnowledgeDocument, ...]:
    """Build an in-memory index ordered by user, shared, then global scope."""
    allowed = set(scopes)
    user_base = root / "users" / user / "knowledge"
    shared_base = root / "shared_knowledge"
    global_base = root / "global_knowledge"
    return tuple(
        ([*_iter_documents(user_base, "user", max_file_chars=max_file_chars)] if "user" in allowed else [])
        + ([*_iter_documents(shared_base, "shared", max_file_chars=max_file_chars)] if "shared" in allowed else [])
        + ([*_iter_documents(global_base, "global", max_file_chars=max_file_chars)] if "global" in allowed else [])
    )


def select_knowledge(
    root: Path,
    user: str,
    query: str,
    config: dict,
) -> KnowledgeSelection:
    source_policy = MainAgentSourcePolicy.from_config(config)
    if not query.strip():
        return KnowledgeSelection((), "")
    max_items = DEFAULT_KNOWLEDGE_MAX_ITEMS
    max_chars = DEFAULT_KNOWLEDGE_MAX_CHARS
    max_file_chars = DEFAULT_KNOWLEDGE_MAX_FILE_CHARS
    minimum_score = DEFAULT_KNOWLEDGE_MINIMUM_SCORE
    if max_items == 0 or max_chars == 0:
        return KnowledgeSelection((), "")

    query_terms = _terms(query)
    if not query_terms:
        return KnowledgeSelection((), "")
    ranked: list[tuple[int, int, str, KnowledgeDocument]] = []
    for document in build_index(
        root,
        user,
        scopes=source_policy.knowledge_scopes,
        max_file_chars=max_file_chars,
    ):
        title_terms = _terms(f"{document.title} {document.relative_path}")
        content_terms = _terms(document.content)
        title_hits = len(query_terms & title_terms)
        content_hits = len(query_terms & content_terms)
        score = title_hits * 4 + content_hits
        if document.path.name.casefold() in _INDEX_NAMES:
            score += title_hits
        if score < minimum_score:
            continue
        scope_priority = {"user": 0, "shared": 1, "global": 2}.get(document.scope, 3)
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
