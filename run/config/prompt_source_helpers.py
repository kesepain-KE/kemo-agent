"""Common prompt helpers and dynamic registration of directory-owned sources."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from run.infra import cached_read_text

from run.config.markdown import scan_markdown_structure
from run.config.prompt_models import (
    ExpandMeta,
    ExpandSelection,
    InjectedPiece,
    PerceptionSelection,
    SenseMeta,
    SkillDescriptor,
)


class PromptSourceError(RuntimeError):
    """A configured prompt source exists but cannot be consumed safely."""


class PromptRegistrationError(PromptSourceError):
    """A directory-owned registration module is missing a valid contract."""


class PerceptionError(PromptSourceError):
    pass


_NUMBER_PART = re.compile(r"(\d+)")
_SENSE_JSON_FIELDS = {
    "name",
    "data_md",
    "recent_update",
    "health",
    "start_update",
}
_SENSE_HEALTH = {"正常", "异常"}
_SENSE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_EXPAND_JSON_FIELDS = {
    "name",
    "explain",
    "open_input",
    "input_data",
    "input_health",
    "start_update",
    "open_control",
    "start_expand",
    "start_control",
}
_EXPAND_JSON_OPTIONAL_FIELDS = {"recent_update"}
_EXPAND_HEALTH = {"正常", "异常"}
_EXPAND_INJECTION_HEADING = re.compile(r"^##\s+注入层\s*$", re.MULTILINE)
_EXPAND_OPERATION_HEADING = re.compile(r"^##\s+操作层\s*$", re.MULTILINE)


def _invalid_expand_meta(module_dir: Path, error: str, *, name: str = "") -> ExpandMeta:
    return ExpandMeta(
        name=name or module_dir.name,
        explain="",
        open_input=False,
        input_data="",
        input_health="异常",
        start_update="",
        open_control=False,
        start_expand="",
        start_control="",
        module_dir=module_dir,
        valid=False,
        error=error,
    )


def read_expand_meta(module_dir: Path) -> ExpandMeta:
    """Read one standardized expand module without failing the whole registry."""

    json_path = module_dir / "expand.json"
    if not json_path.is_file():
        return _invalid_expand_meta(module_dir, "expand.json 缺失")
    try:
        raw = json.loads(cached_read_text(json_path, "utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _invalid_expand_meta(module_dir, "expand.json 不可读或 JSON 无效")
    if not isinstance(raw, dict):
        return _invalid_expand_meta(module_dir, "expand.json 根节点必须是对象")
    missing = sorted(_EXPAND_JSON_FIELDS - set(raw))
    unknown = sorted(set(raw) - _EXPAND_JSON_FIELDS - _EXPAND_JSON_OPTIONAL_FIELDS)
    raw_name = raw.get("name")
    display_name = raw_name.strip() if isinstance(raw_name, str) else module_dir.name
    if missing:
        return _invalid_expand_meta(
            module_dir,
            "expand.json 缺少字段：" + ", ".join(missing),
            name=display_name,
        )
    if unknown:
        return _invalid_expand_meta(
            module_dir,
            "expand.json 包含未知字段：" + ", ".join(unknown),
            name=display_name,
        )
    if not isinstance(raw.get("name"), str) or not raw["name"].strip():
        return _invalid_expand_meta(module_dir, "name 必须是非空字符串")
    if not isinstance(raw.get("explain"), str) or not raw["explain"].strip():
        return _invalid_expand_meta(module_dir, "explain 必须是非空字符串", name=display_name)
    for field in ("open_input", "open_control"):
        if not isinstance(raw.get(field), bool):
            return _invalid_expand_meta(
                module_dir, f"{field} 必须是布尔值", name=display_name
            )
    string_fields = (
        "input_data",
        "input_health",
        "start_update",
        "start_expand",
        "start_control",
    )
    invalid_strings = [
        field
        for field in string_fields
        if not isinstance(raw.get(field), str) or not raw[field].strip()
    ]
    if invalid_strings:
        return _invalid_expand_meta(
            module_dir,
            "字段必须是非空字符串：" + ", ".join(sorted(invalid_strings)),
            name=display_name,
        )
    input_health = raw["input_health"].strip()
    if input_health not in _EXPAND_HEALTH:
        return _invalid_expand_meta(
            module_dir, "input_health 必须是“正常”或“异常”", name=display_name
        )
    recent_update = raw.get("recent_update")
    if recent_update is not None:
        if not isinstance(recent_update, str) or not recent_update.strip():
            return _invalid_expand_meta(
                module_dir, "recent_update 必须是非空字符串", name=display_name
            )
        try:
            datetime.strptime(recent_update.strip(), _SENSE_TIME_FORMAT)
        except ValueError:
            return _invalid_expand_meta(
                module_dir,
                f"recent_update 必须符合 {_SENSE_TIME_FORMAT}",
                name=display_name,
            )
    file_fields = {
        "input_data": (raw["input_data"].strip(), ".md"),
        "start_update": (raw["start_update"].strip(), ".py"),
        "start_expand": (raw["start_expand"].strip(), ".py"),
        "start_control": (raw["start_control"].strip(), ".md"),
    }
    for field, (file_name, suffix) in file_fields.items():
        if Path(file_name).name != file_name or Path(file_name).suffix.casefold() != suffix:
            return _invalid_expand_meta(
                module_dir,
                f"{field} 必须是模块目录内的 {suffix} 文件名",
                name=display_name,
            )
        try:
            (module_dir / file_name).resolve().relative_to(module_dir.resolve())
        except ValueError:
            return _invalid_expand_meta(
                module_dir, f"{field} 不得跳出模块目录", name=display_name
            )
    return ExpandMeta(
        name=display_name,
        explain=raw["explain"].strip(),
        open_input=raw["open_input"],
        input_data=file_fields["input_data"][0],
        input_health=input_health,
        start_update=file_fields["start_update"][0],
        open_control=raw["open_control"],
        start_expand=file_fields["start_expand"][0],
        start_control=file_fields["start_control"][0],
        module_dir=module_dir,
        valid=True,
    )


def _extract_expand_injection_layer(text: str) -> str:
    match = _EXPAND_INJECTION_HEADING.search(text)
    if match is None:
        return ""
    operation = _EXPAND_OPERATION_HEADING.search(text, match.end())
    return text[match.end() : operation.start() if operation else len(text)].strip()


def _invalid_sense_meta(module_dir: Path, error: str, *, name: str = "") -> SenseMeta:
    return SenseMeta(
        name=name or module_dir.name,
        data_md="",
        recent_update="",
        health="异常",
        start_update="",
        data_md_path=module_dir / "sense.md",
        valid=False,
        error=error,
    )


def _read_sense_meta(module_dir: Path) -> SenseMeta:
    """Read one standardized perception module without failing the whole registry."""

    json_path = module_dir / "sense.json"
    try:
        json_exists = json_path.is_file()
    except OSError:
        return _invalid_sense_meta(module_dir, "sense.json 不可访问")
    if not json_exists:
        return _invalid_sense_meta(module_dir, "sense.json 缺失")
    try:
        raw = json.loads(cached_read_text(json_path, "utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _invalid_sense_meta(module_dir, "sense.json 不可读或 JSON 无效")
    if not isinstance(raw, dict):
        return _invalid_sense_meta(module_dir, "sense.json 根节点必须是对象")
    missing = sorted(_SENSE_JSON_FIELDS - set(raw))
    unknown = sorted(set(raw) - _SENSE_JSON_FIELDS)
    display_name = str(raw.get("name") or module_dir.name).strip()
    if missing:
        return _invalid_sense_meta(
            module_dir,
            "sense.json 缺少字段：" + ", ".join(missing),
            name=display_name,
        )
    if unknown:
        return _invalid_sense_meta(
            module_dir,
            "sense.json 包含未知字段：" + ", ".join(unknown),
            name=display_name,
        )
    values = {field: raw.get(field) for field in _SENSE_JSON_FIELDS}
    invalid_strings = [
        field
        for field, value in values.items()
        if not isinstance(value, str) or not value.strip()
    ]
    if invalid_strings:
        return _invalid_sense_meta(
            module_dir,
            "字段必须是非空字符串：" + ", ".join(sorted(invalid_strings)),
            name=display_name,
        )
    data_md = str(raw["data_md"]).strip()
    recent_update = str(raw["recent_update"]).strip()
    health = str(raw["health"]).strip()
    start_update = str(raw["start_update"]).strip()
    if Path(data_md).name != data_md or Path(data_md).suffix.casefold() != ".md":
        return _invalid_sense_meta(
            module_dir, "data_md 必须是模块目录内的 Markdown 文件名", name=display_name
        )
    if Path(start_update).name != start_update or Path(start_update).suffix.casefold() != ".py":
        return _invalid_sense_meta(
            module_dir, "start_update 必须是模块目录内的 Python 文件名", name=display_name
        )
    if health not in _SENSE_HEALTH:
        return _invalid_sense_meta(
            module_dir, "health 必须是“正常”或“异常”", name=display_name
        )
    try:
        datetime.strptime(recent_update, _SENSE_TIME_FORMAT)
    except ValueError:
        return _invalid_sense_meta(
            module_dir,
            f"recent_update 必须符合 {_SENSE_TIME_FORMAT}",
            name=display_name,
        )
    try:
        data_md_path = (module_dir / data_md).resolve()
    except OSError:
        return _invalid_sense_meta(
            module_dir, "data_md 路径解析失败", name=display_name
        )
    try:
        module_root = module_dir.resolve()
        data_md_path.relative_to(module_root)
    except ValueError:
        return _invalid_sense_meta(module_dir, "data_md 不得跳出模块目录", name=display_name)
    except OSError:
        return _invalid_sense_meta(
            module_dir, "模块目录解析失败", name=display_name
        )
    try:
        data_md_exists = data_md_path.is_file()
    except OSError:
        return _invalid_sense_meta(module_dir, "data_md 不可访问", name=display_name)
    if not data_md_exists:
        return _invalid_sense_meta(
            module_dir, f"data_md 指向的文件不存在：{data_md}", name=display_name
        )
    try:
        start_update_path = (module_dir / start_update).resolve()
    except OSError:
        return _invalid_sense_meta(
            module_dir, "start_update 路径解析失败", name=display_name
        )
    try:
        start_update_path.relative_to(module_root)
    except ValueError:
        return _invalid_sense_meta(
            module_dir, "start_update 不得跳出模块目录", name=display_name
        )
    except OSError:
        return _invalid_sense_meta(
            module_dir, "模块目录解析失败", name=display_name
        )
    try:
        start_update_exists = start_update_path.is_file()
    except OSError:
        return _invalid_sense_meta(
            module_dir, "start_update 不可访问", name=display_name
        )
    if not start_update_exists:
        return _invalid_sense_meta(
            module_dir,
            f"start_update 文件不存在：{start_update}",
            name=display_name,
        )
    return SenseMeta(
        name=display_name,
        data_md=data_md,
        recent_update=recent_update,
        health=health,
        start_update=start_update,
        data_md_path=data_md_path,
        valid=True,
    )


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
    "read_expand_meta", "_extract_expand_injection_layer", "_read_sense_meta",
    "natural_path_key", "relative_path", "read_optional_text", "read_required_text",
    "iter_files", "truncate_chars", "parse_skill_descriptor",
]
