"""Register shared prompt-only skills with the prompt pipeline."""

from pathlib import Path


def register(registry) -> None:
    registry.add_skills("shared", Path(__file__).resolve().parent)
