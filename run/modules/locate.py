"""Filesystem discovery for registered Sense and Expand roots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from run.config.prompt_source_helpers import natural_path_key
from run.modules.errors import ModuleError, ModuleSpecError
from run.modules.manifest import read_manifest
from run.modules.spec import ModuleKind, ModuleSpec


@dataclass(frozen=True, slots=True)
class Located:
    specs: tuple[ModuleSpec, ...]
    errors: tuple[tuple[str, ModuleError], ...]


def locate_modules(
    root: Path,
    user: str,
    *,
    kind: ModuleKind,
    roots: Iterable[tuple[str, Path]] | None = None,
) -> Located:
    if roots is None:
        roots = (
            (("global", root / "sense"),)
            if kind == "sense"
            else (
                ("global", root / "expand" / "global"),
                ("shared", root / "expand" / "share"),
                ("user", root / "users" / user / "expand"),
            )
        )
    specs: list[ModuleSpec] = []
    errors: list[tuple[str, ModuleError]] = []
    for scope, base in roots:
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir(), key=lambda item: natural_path_key(item.name))
        except OSError:
            errors.append(
                (f"{'sense' if kind == 'sense' else scope}:*", ModuleError("unreadable", None, f"{base.name} 目录不可读"))
            )
            continue
        for module_dir in children:
            if (
                module_dir.name.startswith(".")
                or module_dir.name == "__pycache__"
                or not module_dir.is_dir()
            ):
                continue
            key = f"sense:{module_dir.name}" if kind == "sense" else f"{scope}:{module_dir.name}"
            if module_dir.is_symlink() or getattr(module_dir, "is_junction", lambda: False)():
                errors.append(
                    (key, ModuleError("path_escape", None, "模块目录不能是符号链接或目录联接"))
                )
                continue
            try:
                module_dir.resolve().relative_to(base.resolve())
                specs.append(read_manifest(module_dir, kind_hint=kind, scope=scope))
            except ModuleSpecError as exc:
                errors.append((key, exc.error))
            except ValueError:
                errors.append(
                    (key, ModuleError("path_escape", None, "模块目录越出所属作用域"))
                )
    return Located(tuple(specs), tuple(errors))
