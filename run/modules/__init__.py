"""Shared discovery, validation and prompt collection for Sense and Expand."""

from .collect import Collected, Piece, Placed, collect
from .errors import ModuleError, ModuleSpecError
from .locate import Located, locate_modules
from .manifest import read_manifest, write_manifest
from .spec import ModuleSpec

__all__ = [
    "Collected",
    "Located",
    "ModuleError",
    "ModuleSpec",
    "ModuleSpecError",
    "Piece",
    "Placed",
    "collect",
    "locate_modules",
    "read_manifest",
    "write_manifest",
]
