"""Cohesive updater package behind the root ``update.py`` entrypoint."""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from . import core as core, agents as agents, plugins as plugins, web as web

from typing import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """Run the canonical updater without importing CLI code at package import."""

    from .cli import main as cli_main

    return cli_main(list(argv) if argv is not None else None)


__all__ = ["main", "core", "agents", "plugins", "web"]
