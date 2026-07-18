"""Register global perception Markdown with the prompt pipeline."""

from pathlib import Path


def register(registry) -> None:
    registry.add_perception(Path(__file__).resolve().parent)
