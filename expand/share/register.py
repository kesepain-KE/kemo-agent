"""Register the standardized shared expand root."""

from pathlib import Path


def register(registry) -> None:
    registry.add_expand_root("shared", Path(__file__).resolve().parent)
