"""Immutable value objects shared by prompt-source discovery and injection."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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


__all__ = [
    "ExpandMeta",
    "ExpandSelection",
    "PerceptionSelection",
    "SenseMeta",
    "SkillDescriptor",
]
