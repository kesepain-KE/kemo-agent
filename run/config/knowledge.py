"""Full, deterministic injection of lightweight knowledge index files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from run.config.prompt_sources import PromptSourceError, iter_files, read_required_text


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


def _is_link_or_junction(path: Path) -> bool:
    """Return whether ``path`` redirects outside the configured tree.

    ``Path.is_symlink()`` does not report Windows junctions.  Treat an
    inspection error as unsafe as well: prompt sources are untrusted input and
    must fail closed when their link metadata cannot be inspected.
    """

    try:
        is_junction = getattr(path, "is_junction", None)
        return path.is_symlink() or bool(is_junction() if is_junction else False)
    except OSError:
        return True


def _contains_link_or_junction(path: Path, base: Path) -> bool:
    """Check ``path`` and every parent up to (and including) ``base``."""

    current = path
    try:
        while True:
            if _is_link_or_junction(current):
                return True
            if current == base:
                return False
            if base not in current.parents:
                return True
            current = current.parent
    except OSError:
        return True


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
        # Knowledge indexes are prompt input.  Do not follow a symlink from
        # the configured scope (including a symlinked scope directory) into
        # an unrelated tree, even though ``os.walk`` itself does not descend
        # into symlink directories.
        try:
            if _is_link_or_junction(base) or not base.is_dir():
                continue
            resolved_base = base.resolve()
        except OSError:
            continue
        try:
            candidates = iter_files(base, names=_INDEX_NAMES)
        except (OSError, UnicodeError, ValueError):
            # Directory enumeration can race with user cleanup or a mounted
            # knowledge source disappearing.  Treat that scope as unavailable
            # instead of failing the entire prompt build.
            continue
        for path in candidates:
            try:
                if _contains_link_or_junction(path, base):
                    continue
                resolved_path = path.resolve()
                resolved_path.relative_to(resolved_base)
            except (OSError, ValueError):
                continue
            try:
                content = read_required_text(path)
            except (OSError, UnicodeError, ValueError, PromptSourceError):
                # A file can disappear or become inaccessible between the
                # directory scan and the read.  Skip that source rather than
                # failing the whole prompt build or reading outside scope.
                continue
            if not content:
                continue
            try:
                relative = path.relative_to(base).as_posix()
            except ValueError:
                continue
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
