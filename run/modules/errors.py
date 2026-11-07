"""Stable validation errors for directory-owned modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Reason = Literal[
    "missing_manifest", "unreadable", "not_object", "unknown_field",
    "missing_field", "bad_type", "bad_enum", "bad_suffix", "path_escape",
    "file_missing", "kind_mismatch", "read_failed", "budget_exceeded",
]


@dataclass(frozen=True, slots=True)
class ModuleError:
    reason: Reason
    field: str | None
    detail: str

    def __str__(self) -> str:
        return self.detail


class ModuleSpecError(ValueError):
    def __init__(self, error: ModuleError) -> None:
        self.error = error
        super().__init__(f"{error.reason}:{error.field or '-'}:{error.detail}")
