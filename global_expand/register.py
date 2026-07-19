"""Register the standardized global expand root."""

from pathlib import Path


def register(registry) -> None:
    registry.add_expand_root("global", Path(__file__).resolve().parent)
