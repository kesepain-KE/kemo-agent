"""Common prompt helpers and dynamic registration of directory-owned sources."""

from __future__ import annotations

import importlib.util
import re
import sys
import uuid
from dataclasses import dataclass
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
    candidates = base.rglob("*") if recursive else base.glob("*")
    result: list[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if skip_hidden and any(part.startswith(".") for part in relative.parts):
            continue
        if allowed_suffixes and path.suffix.casefold() not in allowed_suffixes:
            continue
        if allowed_names and path.name.casefold() not in allowed_names:
            continue
        result.append(path)
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

    def add_expand(self, scope: str, module: str, inject_file: str | Path) -> None:
        base = {
            "global": self.root / "global_expand",
            "shared": self.root / "shared_expand",
            "user": self.root / "users" / self.user / "expand",
        }.get(scope)
        if base is None:
            raise PromptRegistrationError(f"expand scope 无效：{scope}")
        if not isinstance(module, str) or not module.strip():
            raise PromptRegistrationError("expand module 必须是非空字符串")
        module = module.strip()
        module_relative = Path(module)
        if module_relative.is_absolute() or len(module_relative.parts) != 1 or module in {".", ".."}:
            raise PromptRegistrationError(f"expand module 必须是来源根目录的直接子目录：{module!r}")
        module_dir = (base / module).resolve()
        try:
            module_dir.relative_to(base.resolve())
        except ValueError as exc:
            raise PromptRegistrationError(f"expand module 不得跳出来源根目录：{module!r}") from exc
        if not isinstance(inject_file, (str, Path)):
            raise PromptRegistrationError("expand inject_file 必须是路径")
        raw_path = Path(inject_file)
        selected = raw_path.resolve() if raw_path.is_absolute() else (module_dir / raw_path).resolve()
        try:
            selected.relative_to(module_dir)
        except ValueError as exc:
            raise PromptRegistrationError(f"expand 注入文件不得跳出模块目录：{selected}") from exc
        if not selected.is_file():
            raise PromptRegistrationError(f"已注册的 expand 注入文件不存在：{selected}")
        entry = (scope, module, selected)
        if entry in self._expand_entries:
            raise PromptRegistrationError(f"expand 注入文件重复注册：{selected}")
        self._expand_entries.append(entry)

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
        if not expected.is_dir():
            return
        modules = [path for path in expected.iterdir() if path.is_dir() and not path.name.startswith(".")]
        modules.sort(key=lambda path: natural_path_key(path.name))
        for module in modules:
            inject_file = module / "inject.md"
            if inject_file.is_file():
                self.add_expand("user", module.name, inject_file)

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
        if max_chars == 0:
            return ExpandSelection("", (), 0, 0, 0, 0, False)
        scope_rank = {"global": 0, "shared": 1, "user": 2}
        candidates: list[tuple[str, str, Path]] = []
        diagnostics: dict[str, Any] = {}
        for scope in ("global", "shared", "user"):
            discovered = [item[1] for item in self._expand_entries if item[0] == scope]
            values = None if allow is None else allow.get(scope, ())
            selected_entries = [
                item
                for item in self._expand_entries
                if item[0] == scope
                and (allow is None or self._allowed(item[1], values))
            ]
            candidates.extend(selected_entries)
            selected = [item[1] for item in selected_entries]
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
        self._selection_diagnostics["expand"] = diagnostics
        entries = sorted(
            candidates,
            key=lambda item: (
                scope_rank[item[0]],
                natural_path_key(relative_path(item[2], self.root)),
            ),
        )
        pieces: list[str] = []
        files: list[str] = []
        offsets: list[int] = []
        used = 0
        for scope, module, path in entries:
            content = read_required_text(path)
            if not content:
                continue
            piece = f"[{scope}:{module}]\n{content}"
            offsets.append(used + (2 if pieces else 0))
            pieces.append(piece)
            files.append(relative_path(path, self.root))
            used += len(piece) + (2 if len(pieces) > 1 else 0)
        full_text = "\n\n".join(pieces)
        text, truncated = truncate_chars(full_text, max_chars)
        injected_count = sum(offset < len(text) for offset in offsets)
        return ExpandSelection(
            text,
            tuple(files[:injected_count]),
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
            for module in modules:
                files = iter_files(module, suffixes={".md"}, skip_hidden=True)
                updated_at = 0.0
                for path in files:
                    try:
                        updated_at = max(updated_at, path.stat().st_mtime)
                    except OSError:
                        continue
                selected = self._allowed(module.name, allow_modules)
                active = selected and bool(files)
                inventory.append(
                    {
                        "name": module.name,
                        "files": len(files),
                        "data_items": [
                            path.relative_to(module).as_posix() for path in files
                        ],
                        "updated_at": updated_at,
                        "selected": selected,
                        "active": active,
                        "status": (
                            "active"
                            if active
                            else "empty"
                            if not files
                            else "filtered"
                        ),
                    }
                )
        return tuple(inventory)

    def select_perception(
        self,
        *,
        max_chars: int,
        mode: str = "full",
        allow_modules: tuple[str, ...] | None = None,
    ) -> PerceptionSelection:
        if mode != "full":
            raise PerceptionError(f"perception 注入模式暂不支持：{mode}")
        if max_chars == 0:
            return PerceptionSelection("", (), 0, 0, 0, 0, False)
        paths: list[Path] = []
        pieces: list[str] = []
        offsets: list[int] = []
        used = 0
        discovered_modules: list[str] = []
        selected_modules: list[str] = []
        for base in self._perception_roots:
            modules = (
                [
                    path
                    for path in base.iterdir()
                    if path.is_dir()
                    and not path.name.startswith(".")
                    and path.name != "__pycache__"
                ]
                if base.is_dir()
                else []
            )
            modules.sort(key=lambda path: natural_path_key(path.name))
            for module in modules:
                discovered_modules.append(module.name)
                if not self._allowed(module.name, allow_modules):
                    continue
                selected_modules.append(module.name)
                for path in iter_files(module, suffixes={".md"}, skip_hidden=True):
                    content = read_required_text(path)
                    if not content:
                        continue
                    piece = f"[{path.relative_to(base).as_posix()}]\n{content}"
                    offsets.append(used + (2 if pieces else 0))
                    pieces.append(piece)
                    paths.append(path)
                    used += len(piece) + (2 if len(pieces) > 1 else 0)
        configured = [] if allow_modules is None else [
            item for item in allow_modules if item != "*"
        ]
        discovered_set = set(discovered_modules)
        selected_set = set(selected_modules)
        self._selection_diagnostics["perception"] = {
            "global": {
                "mode": "all" if allow_modules is None else "allowlist",
                "discovered": discovered_modules,
                "selected": selected_modules,
                "filtered": [
                    item for item in discovered_modules if item not in selected_set
                ],
                "unmatched": [
                    item for item in configured if item not in discovered_set
                ],
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
