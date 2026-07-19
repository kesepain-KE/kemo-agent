"""Full, deterministic injection of lightweight knowledge index files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from run.prompt_sources import iter_files, read_required_text


_INDEX_NAMES = frozenset({"index.md", "data_structure.md", "索引.md", "目录.md"})


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    scope: str
    path: Path
    relative_path: str
    title: str
    content: str


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


def _title(path: Path, content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return path.stem


def select_knowledge_index(
    root: Path,
    user: str,
    *,
    scopes: tuple[str, ...] = ("user", "shared", "global"),
) -> KnowledgeIndexSelection:
    """Inject every named index file in user → shared → global order."""
    available_scopes = (
        ("user", root / "users" / user / "knowledge"),
        ("shared", root / "shared_knowledge"),
        ("global", root / "global_knowledge"),
    )
    allowed_scopes = set(scopes)
    documents: list[KnowledgeDocument] = []
    pieces: list[str] = []
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
            documents.append(document)
            pieces.append(f"[{scope}:{relative}]\n{content}")
    full_text = "\n\n".join(pieces)
    total = len(full_text)
    return KnowledgeIndexSelection(
        documents=tuple(documents),
        text=full_text,
        original_chars=total,
        injected_chars=total,
        original_items=len(documents),
        injected_items=len(documents),
        truncated=False,
    )
