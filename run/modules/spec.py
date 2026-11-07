"""Validated module metadata shared by runtime, Web and scheduled updates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


ModuleKind = Literal["sense", "expand"]


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    kind: ModuleKind
    key: str
    scope: str
    name: str
    explain: str
    health: str
    recent_update: str | None
    module_dir: Path
    update_script: Path
    input_open: bool
    input_file: Path | None
    control_open: bool
    control_script: Path | None
    control_entry: Path | None
