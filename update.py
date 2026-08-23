#!/usr/bin/env python3
"""Compatibility entrypoint for the kemo-agent updater.

All updater behavior lives in :mod:`update.cli`.  Keep this file deliberately
thin so scripts can continue using ``python update.py ...`` while tests and
other callers share the same implementation through ``python -m update``.
"""

from __future__ import annotations

from update.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
