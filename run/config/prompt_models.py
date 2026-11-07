"""Immutable value objects for prompt source descriptions."""

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


__all__ = ["SkillDescriptor"]
