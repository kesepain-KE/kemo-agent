"""Common prompt helpers and dynamic registration of directory-owned sources."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable
from run.infra import cached_read_text

from run.config.markdown import scan_markdown_structure
from run.config.prompt_models import SkillDescriptor


class PromptSourceError(RuntimeError):
    """A configured prompt source exists but cannot be consumed safely."""


class PromptRegistrationError(PromptSourceError):
    """A directory-owned registration module is missing a valid contract."""


class PerceptionError(PromptSourceError):
    pass


_NUMBER_PART = re.compile(r"(\d+)")
_EXPAND_INJECTION_HEADING = re.compile(r"^##\s+注入层\s*$", re.MULTILINE)
_EXPAND_OPERATION_HEADING = re.compile(r"^##\s+操作层\s*$", re.MULTILINE)


def _extract_expand_injection_layer(text: str) -> str:
    match = _EXPAND_INJECTION_HEADING.search(text)
    if match is None:
        return ""
    operation = _EXPAND_OPERATION_HEADING.search(text, match.end())
    return text[match.end() : operation.start() if operation else len(text)].strip()


def natural_path_key(value: str | Path) -> tuple[tuple[int, object], ...]:
    """Return a case-insensitive natural key for a complete relative path."""

    rendered = value.as_posix() if isinstance(value, Path) else str(value).replace("\\", "/")
    parts: list[tuple[int, object]] = []
    for part in _NUMBER_PART.split(rendered.casefold()):
        if not part:
            continue
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)


def relative_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def read_optional_text(path: Path) -> str:
    try:
        return cached_read_text(path, "utf-8-sig").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as exc:
        raise PromptSourceError(f"提示词来源不可读：{path}（{exc}）") from exc


def read_required_text(path: Path) -> str:
    try:
        return cached_read_text(path, "utf-8-sig").strip()
    except (OSError, UnicodeError) as exc:
        raise PromptSourceError(f"提示词来源不可读：{path}（{exc}）") from exc


IGNORED_RUNTIME_DIRECTORY_NAMES = frozenset({"kemo-graph-storage"})


def iter_files(
    base: Path,
    *,
    suffixes: Iterable[str] | None = None,
    names: Iterable[str] | None = None,
    recursive: bool = True,
    skip_hidden: bool = True,
) -> tuple[Path, ...]:
    if not base.is_dir():
        return ()
    allowed_suffixes = {item.casefold() for item in suffixes or ()}
    allowed_names = {item.casefold() for item in names or ()}
    result: list[Path] = []
    ignored_directories = {
        "__pycache__",
        "node_modules",
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
    } | IGNORED_RUNTIME_DIRECTORY_NAMES

    def include(path: Path) -> None:
        if allowed_suffixes and path.suffix.casefold() not in allowed_suffixes:
            return
        if allowed_names and path.name.casefold() not in allowed_names:
            return
        result.append(path)

    if recursive:
        for current, directories, files in os.walk(base, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if name.casefold() not in ignored_directories
                and (not skip_hidden or not name.startswith("."))
            ]
            current_path = Path(current)
            for name in files:
                if skip_hidden and name.startswith("."):
                    continue
                include(current_path / name)
    else:
        for path in base.iterdir():
            if not path.is_file() or (skip_hidden and path.name.startswith(".")):
                continue
            include(path)
    result.sort(key=lambda item: natural_path_key(item.relative_to(base)))
    return tuple(result)


def truncate_chars(text: str, max_chars: int) -> tuple[str, bool]:
    if max_chars < 0:
        raise ValueError("max_chars must be non-negative")
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def parse_skill_descriptor(path: Path, *, scope: str, root: Path) -> SkillDescriptor:
    text = read_required_text(path)
    lines = text.splitlines()
    structure = scan_markdown_structure(text)
    title_heading = next(
        (heading for heading in structure.headings if heading.level == 1),
        None,
    )
    if title_heading is None:
        raise PromptRegistrationError(f"SKILL.md 缺少一级标题：{path}")
    stop_lines = {
        heading.line
        for heading in structure.headings
        if heading.line > title_heading.line and heading.level == 2
    }
    stop_lines.update(
        index
        for index, line in enumerate(lines)
        if index > title_heading.line
        and index not in structure.code_lines
        and line.strip() == "---"
    )
    description_end = min(stop_lines, default=len(lines))
    return SkillDescriptor(
        title=title_heading.text,
        description="\n".join(
            lines[title_heading.line + 1 : description_end]
        ).strip(),
        path=path,
        relative_path=relative_path(path, root),
        scope=scope,
    )

__all__ = [
    "PromptSourceError", "PromptRegistrationError", "PerceptionError",
    "_extract_expand_injection_layer",
    "natural_path_key", "relative_path", "read_optional_text", "read_required_text",
    "iter_files", "truncate_chars", "parse_skill_descriptor",
]
