#!/usr/bin/env python3
"""Independent kemo-agent installation entrypoint (Python 3.10+)."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10+ is required')

from core.cli import main

if __name__ == '__main__':
    if sys.argv[1:2] == ['_serve_windows']:
        from core.runtime import serve_windows

        receipt_directory, expected_python, *web_args = sys.argv[2:]
        raise SystemExit(serve_windows(receipt_directory, expected_python, web_args))
    if sys.argv[1:2] == ['_windows_web']:
        from core.runtime import run_windows_web

        receipt_directory, *web_args = sys.argv[2:]
        raise SystemExit(run_windows_web(receipt_directory, web_args))
    raise SystemExit(main())
