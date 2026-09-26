#!/usr/bin/env python3
"""Independent kemo-agent installation entrypoint (Python 3.10+)."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10+ is required')

from core.cli import main

if __name__ == '__main__':
    raise SystemExit(main())
