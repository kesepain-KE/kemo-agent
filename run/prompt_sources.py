"""Common prompt helpers and dynamic registration of directory-owned sources."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


class PromptSourceError(RuntimeError):
    """A configured prompt source exists but cannot be consumed safely."""


class PromptRegistrationError(PromptSourceError):
    """A directory-owned registration module is missing a valid contract."""


class PerceptionError(PromptSourceError):
    pass


_NUMBER_PART = re.compile(r"(\d+)")
_TITLE = re.compile(r"^#\s+(.+?)\s*$")
_SECONDARY_HEADING = re.compile(r"^##\s+")
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


@dataclass(frozen=True, slots=True)
class SkillDescriptor:
    title: str
    description: str
    path: Path
    relative_path: str
    scope: str


@dataclass(frozen=True, slots=True)
class ExpandSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class PerceptionSelection:
    text: str
    source_files: tuple[str, ...]
    original_chars: int
    injected_chars: int
    original_items: int
    injected_items: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class SenseMeta:
    name: str
    data_md: str
    recent_update: str
    health: str
    start_update: str
    data_md_path: Path
    valid: bool
    error: str = ""


@dataclass(frozen=True, slots=True)
class ExpandMeta:
    name: str
    explain: str
    open_input: bool
    input_data: str
    input_health: str
    start_update: str
    open_control: bool
    start_expand: str
    start_control: str
    module_dir: Path
    valid: bool
    error: str = ""


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
        raw = json.loads(json_path.read_text("utf-8-sig"))
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
    if not json_path.is_file():
        return _invalid_sense_meta(module_dir, "sense.json 缺失")
    try:
        raw = json.loads(json_path.read_text("utf-8-sig"))
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
    data_md_path = (module_dir / data_md).resolve()
    try:
        data_md_path.relative_to(module_dir.resolve())
    except ValueError:
        return _invalid_sense_meta(module_dir, "data_md 不得跳出模块目录", name=display_name)
    if not data_md_path.is_file():
        return _invalid_sense_meta(
            module_dir, f"data_md 指向的文件不存在：{data_md}", name=display_name
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
        return path.read_text("utf-8-sig").strip()
    except FileNotFoundError:
        return ""
    except (OSError, UnicodeError) as exc:
        raise PromptSourceError(f"提示词来源不可读：{path}（{exc}）") from exc


def read_required_text(path: Path) -> str:
    try:
        return path.read_text("utf-8-sig").strip()
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
    title_index = -1
    title = ""
    for index, line in enumerate(lines):
        matched = _TITLE.fullmatch(line.strip())
        if matched:
            title_index = index
            title = matched.group(1).strip()
            break
    if title_index < 0 or not title:
        raise PromptRegistrationError(f"SKILL.md 缺少一级标题：{path}")
    description_lines: list[str] = []
    for line in lines[title_index + 1 :]:
        stripped = line.strip()
        if _SECONDARY_HEADING.match(stripped) or stripped == "---":
            break
        description_lines.append(line)
    return SkillDescriptor(
        title=title,
        description="\n".join(description_lines).strip(),
        path=path,
        relative_path=relative_path(path, root),
        scope=scope,
    )


class PromptSourceRegistry:
    """Collect prompt-source layers registered by directory-local Python modules."""

    def __init__(self, root: Path, user: str) -> None:
        self.root = root.resolve()
        self.user = user
        self._expand_entries: list[tuple[str, str, Path]] = []
        self._expand_roots: list[tuple[str, Path]] = []
        self._skill_layers: list[tuple[str, Path]] = []
        self._perception_roots: list[Path] = []
        self._selection_diagnostics: dict[str, Any] = {}

    def _add_layer(
        self,
        target: list[tuple[str, Path]],
        *,
        kind: str,
        scope: str,
        base: Path,
        expected: Path,
    ) -> None:
        resolved = base.resolve()
        if resolved != expected.resolve():
            raise PromptRegistrationError(
                f"{kind} 注册目录与模块位置不一致：{resolved}（应为 {expected.resolve()}）"
            )
        key = (scope, resolved)
        if key in target:
            raise PromptRegistrationError(f"{kind} 来源重复注册：{scope}:{resolved}")
        target.append(key)

    def _expand_base(self, scope: str) -> Path:
        base = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / self.user / "expand",
        }.get(scope)
        if base is None:
            raise PromptRegistrationError(f"expand scope 无效：{scope}")
        return base

    def add_expand_root(self, scope: str, base: Path) -> None:
        self._add_layer(
            self._expand_roots,
            kind="expand",
            scope=scope,
            base=base,
            expected=self._expand_base(scope),
        )

    def add_expand_module(self, scope: str, module: str, module_dir: Path) -> None:
        base = self._expand_base(scope)
        if not isinstance(module, str) or not module.strip():
            raise PromptRegistrationError("expand module 必须是非空字符串")
        module = module.strip()
        module_relative = Path(module)
        if module_relative.is_absolute() or len(module_relative.parts) != 1 or module in {".", ".."}:
            raise PromptRegistrationError(f"expand module 必须是来源根目录的直接子目录：{module!r}")
        expected_module = (base / module).resolve()
        resolved = module_dir.resolve()
        if resolved != expected_module:
            raise PromptRegistrationError(
                f"expand module 目录与模块名不一致：{resolved}（应为 {expected_module}）"
            )
        entry = (scope, module, resolved)
        if any(item[:2] == entry[:2] for item in self._expand_entries):
            raise PromptRegistrationError(f"expand 模块重复注册：{scope}:{module}")
        self._expand_entries.append(entry)

    def add_expand(self, scope: str, module: str, inject_file: str | Path) -> None:
        """Compatibility entrypoint; expand.json remains the only injection authority."""

        if not isinstance(inject_file, (str, Path)):
            raise PromptRegistrationError("expand inject_file 必须是路径")
        base = self._expand_base(scope)
        module_dir = (base / str(module)).resolve()
        selected = Path(inject_file)
        selected = selected.resolve() if selected.is_absolute() else (module_dir / selected).resolve()
        try:
            selected.relative_to(module_dir)
        except ValueError as exc:
            raise PromptRegistrationError(f"expand 兼容路径不得跳出模块目录：{selected}") from exc
        self.add_expand_module(scope, module, module_dir)

    def add_skills(self, scope: str, base: Path) -> None:
        expected = {
            "shared": self.root / "shared_skills",
            "user": self.root / "users" / self.user / "user_skills",
        }.get(scope)
        if expected is None:
            raise PromptRegistrationError(f"skills scope 无效：{scope}")
        self._add_layer(
            self._skill_layers,
            kind="skills",
            scope=scope,
            base=base,
            expected=expected,
        )

    def add_user_expands(self, base: Path) -> None:
        expected = (self.root / "users" / self.user / "expand").resolve()
        if base.resolve() != expected:
            raise PromptRegistrationError(
                f"user expand 解析目录不一致：{base.resolve()}（应为 {expected}）"
            )
        self.add_expand_root("user", expected)

    def _expand_module_entries(self) -> tuple[tuple[str, str, Path], ...]:
        entries: dict[tuple[str, str], tuple[str, str, Path]] = {
            (scope, module): (scope, module, path)
            for scope, module, path in self._expand_entries
        }
        for scope, base in self._expand_roots:
            if not base.is_dir():
                continue
            modules = [
                path
                for path in base.iterdir()
                if path.is_dir()
                and not path.name.startswith(".")
                and path.name != "__pycache__"
            ]
            modules.sort(key=lambda path: natural_path_key(path.name))
            for module in modules:
                entries.setdefault(
                    (scope, module.name), (scope, module.name, module.resolve())
                )
        scope_rank = {"global": 0, "shared": 1, "user": 2}
        return tuple(
            sorted(
                entries.values(),
                key=lambda item: (scope_rank[item[0]], natural_path_key(item[1])),
            )
        )

    def add_perception(self, base: Path) -> None:
        resolved = base.resolve()
        expected = (self.root / "global_sense").resolve()
        if resolved != expected:
            raise PromptRegistrationError(
                f"perception 注册目录与模块位置不一致：{resolved}（应为 {expected}）"
            )
        if resolved in self._perception_roots:
            raise PromptRegistrationError(f"perception 来源重复注册：{resolved}")
        self._perception_roots.append(resolved)

    @staticmethod
    def _allowed(name: str, values: tuple[str, ...] | None) -> bool:
        return values is None or "*" in values or name in values

    def select_skills(
        self,
        *,
        allow: dict[str, tuple[str, ...] | None] | None = None,
    ) -> tuple[SkillDescriptor, ...]:
        descriptors: list[SkillDescriptor] = []
        diagnostics: dict[str, Any] = {}
        for scope, base in self._skill_layers:
            values = None if allow is None else allow.get(scope, ())
            discovered: list[str] = []
            selected: list[str] = []
            for path in iter_files(base, names={"SKILL.md"}):
                definition = parse_skill_descriptor(path, scope=scope, root=self.root)
                logical_name = path.parent.relative_to(base).as_posix()
                discovered.append(logical_name)
                if allow is None or self._allowed(logical_name, values):
                    descriptors.append(definition)
                    selected.append(logical_name)
            configured = [] if values is None else [item for item in values if item != "*"]
            discovered_set = set(discovered)
            selected_set = set(selected)
            diagnostics[scope] = {
                "mode": "all" if values is None else "allowlist",
                "discovered": discovered,
                "selected": selected,
                "filtered": [item for item in discovered if item not in selected_set],
                "unmatched": [item for item in configured if item not in discovered_set],
            }
        self._selection_diagnostics["skills"] = diagnostics
        return tuple(descriptors)

    def select_expand(
        self,
        *,
        max_chars: int,
        mode: str = "full",
        allow: dict[str, tuple[str, ...] | None] | None = None,
    ) -> ExpandSelection:
        if mode != "full":
            raise PromptSourceError(f"expand_data 注入模式暂不支持：{mode}")
        entries = self._expand_module_entries()
        diagnostics: dict[str, Any] = {}
        candidates: list[tuple[str, str, Path, ExpandMeta]] = []
        for scope in ("global", "shared", "user"):
            scope_entries = [item for item in entries if item[0] == scope]
            discovered = [item[1] for item in scope_entries]
            values = None if allow is None else allow.get(scope, ())
            selected: list[str] = []
            filtered: list[str] = []
            invalid: list[str] = []
            health_status: dict[str, dict[str, Any]] = {}
            for entry_scope, module, module_dir in scope_entries:
                meta = read_expand_meta(module_dir)
                health_status[module] = {
                    "name": meta.name,
                    "explain": meta.explain,
                    "valid": meta.valid,
                    "input_health": meta.input_health,
                    "open_input": meta.open_input,
                    "open_control": meta.open_control,
                    "input_data": meta.input_data,
                    "start_update": meta.start_update,
                    "start_expand": meta.start_expand,
                    "start_control": meta.start_control,
                    "control_file": (
                        relative_path(module_dir / meta.start_control, self.root)
                        if meta.valid
                        else ""
                    ),
                    "error": meta.error,
                }
                if allow is not None and not self._allowed(module, values):
                    filtered.append(module)
                    continue
                if not meta.valid:
                    invalid.append(module)
                    continue
                selected.append(module)
                candidates.append((entry_scope, module, module_dir, meta))
            configured = [] if values is None else [item for item in values if item != "*"]
            discovered_set = set(discovered)
            diagnostics[scope] = {
                "mode": "all" if values is None else "allowlist",
                "discovered": discovered,
                "selected": selected,
                "filtered": filtered,
                "invalid": invalid,
                "unmatched": [item for item in configured if item not in discovered_set],
                "health_status": health_status,
            }
        self._selection_diagnostics["expand"] = diagnostics
        pieces: list[str] = []
        module_files: list[list[str]] = []
        offsets: list[int] = []
        used = 0
        for scope, module, module_dir, meta in candidates:
            if max_chars == 0:
                continue
            module_pieces: list[str] = []
            files: list[str] = []
            try:
                if meta.open_input and meta.input_health == "正常":
                    input_path = module_dir / meta.input_data
                    if input_path.is_file():
                        content = read_required_text(input_path)
                        if content:
                            module_pieces.append(f"## 数据采集\n{content}")
                            files.append(relative_path(input_path, self.root))
                if meta.open_control:
                    control_path = module_dir / meta.start_control
                    if control_path.is_file():
                        control_text = read_required_text(control_path)
                        injection_layer = _extract_expand_injection_layer(control_text)
                        if injection_layer:
                            module_pieces.append(
                                "## 操控能力\n"
                                f"{injection_layer}\n\n"
                                f"调用入口：使用 `expand_call`，传入 `scope={scope}`、"
                                f"`module={module}`，具体命令和参数按需读取操作层。"
                            )
                            files.append(relative_path(control_path, self.root))
            except PromptSourceError:
                scope_diagnostics = diagnostics[scope]
                if module in scope_diagnostics["selected"]:
                    scope_diagnostics["selected"].remove(module)
                if module not in scope_diagnostics["invalid"]:
                    scope_diagnostics["invalid"].append(module)
                scope_diagnostics["health_status"][module].update(
                    {
                        "valid": False,
                        "input_health": "异常",
                        "error": "拓展数据或操控手册不可读",
                    }
                )
                continue
            if not module_pieces:
                continue
            piece = f"[{scope}:{module}]\n" + "\n\n".join(module_pieces)
            offsets.append(used + (2 if pieces else 0))
            pieces.append(piece)
            module_files.append(files)
            used += len(piece) + (2 if len(pieces) > 1 else 0)
        full_text = "\n\n".join(pieces)
        text, truncated = truncate_chars(full_text, max_chars)
        injected_count = sum(offset < len(text) for offset in offsets)
        source_files = tuple(
            path
            for files in module_files[:injected_count]
            for path in files
        )
        return ExpandSelection(
            text,
            source_files,
            len(full_text),
            len(text),
            len(pieces),
            injected_count,
            truncated,
        )

    def perception_inventory(
        self,
        *,
        allow_modules: tuple[str, ...] | None = None,
    ) -> tuple[dict[str, Any], ...]:
        """Return public module metadata from successfully registered roots."""

        inventory: list[dict[str, Any]] = []
        for base, module in self._perception_module_dirs():
            meta = _read_sense_meta(module)
            selected = self._allowed(module.name, allow_modules)
            active = selected and meta.valid
            updated_at = 0.0
            if meta.valid:
                try:
                    updated_at = meta.data_md_path.stat().st_mtime
                except OSError:
                    pass
            inventory.append(
                {
                    "name": module.name,
                    "display_name": meta.name,
                    "data_md": meta.data_md,
                    "files": 1 if meta.valid else 0,
                    "recent_update": meta.recent_update,
                    "updated_at": updated_at,
                    "health": meta.health,
                    "valid": meta.valid,
                    "error": meta.error,
                    "start_update": meta.start_update,
                    "data_items": [meta.data_md] if meta.valid else [],
                    "selected": selected,
                    "active": active,
                    "status": (
                        "active"
                        if active
                        else "invalid"
                        if not meta.valid
                        else "filtered"
                    ),
                    "root": relative_path(base, self.root),
                }
            )
        return tuple(inventory)

    def _perception_module_dirs(self) -> tuple[tuple[Path, Path], ...]:
        result: list[tuple[Path, Path]] = []
        for base in self._perception_roots:
            if not base.is_dir():
                continue
            modules = [
                path
                for path in base.iterdir()
                if path.is_dir()
                and not path.name.startswith(".")
                and path.name != "__pycache__"
            ]
            modules.sort(key=lambda path: natural_path_key(path.name))
            result.extend((base, module) for module in modules)
        return tuple(result)

    def select_perception(
        self,
        *,
        max_chars: int,
        mode: str = "full",
        allow_modules: tuple[str, ...] | None = None,
    ) -> PerceptionSelection:
        if mode != "full":
            raise PerceptionError(f"perception 注入模式暂不支持：{mode}")
        paths: list[Path] = []
        pieces: list[str] = []
        offsets: list[int] = []
        used = 0
        discovered_modules: list[str] = []
        selected_modules: list[str] = []
        health_status: dict[str, dict[str, Any]] = {}
        filtered_modules: list[str] = []
        invalid_modules: list[str] = []
        for _base, module in self._perception_module_dirs():
            discovered_modules.append(module.name)
            meta = _read_sense_meta(module)
            health_status[module.name] = {
                "display_name": meta.name,
                "health": meta.health,
                "recent_update": meta.recent_update,
                "valid": meta.valid,
                "error": meta.error,
            }
            if not self._allowed(module.name, allow_modules):
                filtered_modules.append(module.name)
                continue
            if not meta.valid:
                invalid_modules.append(module.name)
                continue
            selected_modules.append(module.name)
            if max_chars == 0:
                continue
            try:
                content = read_required_text(meta.data_md_path)
            except PromptSourceError:
                invalid_modules.append(module.name)
                selected_modules.pop()
                health_status[module.name] = {
                    **health_status[module.name],
                    "health": "异常",
                    "valid": False,
                    "error": "data_md 不可读",
                }
                continue
            if not content:
                continue
            piece = f"[{module.name}]\n{content}"
            offsets.append(used + (2 if pieces else 0))
            pieces.append(piece)
            paths.append(meta.data_md_path)
            used += len(piece) + (2 if len(pieces) > 1 else 0)
        configured = [] if allow_modules is None else [
            item for item in allow_modules if item != "*"
        ]
        discovered_set = set(discovered_modules)
        self._selection_diagnostics["perception"] = {
            "global": {
                "mode": "all" if allow_modules is None else "allowlist",
                "discovered": discovered_modules,
                "selected": selected_modules,
                "filtered": filtered_modules,
                "invalid": invalid_modules,
                "unmatched": [
                    item for item in configured if item not in discovered_set
                ],
                "health_status": health_status,
            }
        }
        full_text = "\n\n".join(pieces)
        text, truncated = truncate_chars(full_text, max_chars)
        injected_count = sum(offset < len(text) for offset in offsets)
        return PerceptionSelection(
            text=text,
            source_files=tuple(relative_path(path, self.root) for path in paths[:injected_count]),
            original_chars=len(full_text),
            injected_chars=len(text),
            original_items=len(pieces),
            injected_items=injected_count,
            truncated=truncated,
        )

    def selection_diagnostics(self) -> dict[str, Any]:
        return {
            kind: {
                scope: {
                    key: list(value) if isinstance(value, list) else value
                    for key, value in detail.items()
                }
                for scope, detail in scopes.items()
            }
            for kind, scopes in self._selection_diagnostics.items()
        }


def _load_registration_module(path: Path, registry: PromptSourceRegistry) -> None:
    module_name = f"kemo_prompt_register_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PromptRegistrationError(f"无法加载提示词注册模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise PromptRegistrationError(f"提示词注册模块缺少 register(registry)：{path}")
        register(registry)
    except PromptRegistrationError:
        raise
    except Exception as exc:
        raise PromptRegistrationError(f"提示词注册模块执行失败：{path}（{exc}）") from exc
    finally:
        sys.modules.pop(module_name, None)


def load_prompt_source_registry(root: Path, user: str) -> PromptSourceRegistry:
    """Load directory-owned registrars in fixed layer order."""

    base = root.resolve()
    registry = PromptSourceRegistry(base, user)
    paths = (
        base / "global_expand" / "register.py",
        base / "shared_expand" / "register.py",
        base / "shared_skills" / "register.py",
        base / "global_sense" / "register.py",
    )
    for path in paths:
        if path.is_file():
            _load_registration_module(path, registry)
    from agents._runtime.user_resources import attach_user_prompt_sources

    attach_user_prompt_sources(registry, base, user)
    return registry
