"""Support ``python -m update`` through the same canonical entrypoint."""

from __future__ import annotations

from .cli import main


if __name__ == "__main__":
    raise SystemExit(main())

