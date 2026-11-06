"""Read-only subprocess for native file-handle inspection; never imports runtime."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import psutil


def find_writer(payload: dict) -> int | None:
    root = Path(payload["root"]).resolve()
    for pid, started_at in payload["processes"]:
        try:
            process = psutil.Process(pid)
            if process.create_time() != started_at:
                raise ValueError("process identity changed")
            for item in process.open_files():
                path = Path(item.path).resolve()
                if (
                    path.is_relative_to(root)
                    and str(path).removesuffix("-wal").removesuffix("-shm").lower().endswith(
                        (".sqlite3", ".sqlite", ".db")
                    )
                    and process.is_running()
                ):
                    return pid
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return None


if __name__ == "__main__":
    try:
        result = find_writer(json.load(sys.stdin))
        print(json.dumps({"writer_pid": result}))
    except Exception:
        # Never emit arguments, credentials, paths or native diagnostics.
        sys.exit(2)
