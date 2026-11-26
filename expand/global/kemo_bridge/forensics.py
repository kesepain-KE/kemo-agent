"""Local, bounded diagnostics for bridge chat failures."""
from __future__ import annotations

import json
import logging
import os
import traceback
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast


MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


class _ForensicsHandler(RotatingFileHandler):
    def _open(self) -> TextIOWrapper:
        return cast(TextIOWrapper, open(
            self.baseFilename, self.mode, encoding=self.encoding,
            opener=lambda path, flags: os.open(path, flags, 0o600),
        ))

    def handleError(self, record: logging.LogRecord) -> None:
        # The default handler dumps failed writes to stderr, which is bridge.log.
        raise


class ForensicsLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handler = _ForensicsHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
                                          encoding="utf-8", delay=True)

    def write(
        self, run_id: str, phase: str, code: str, *, attempt: int, status: int | None,
        body_bytes: int | None, body_sha256: str | None, unexpected: Exception | None,
    ) -> None:
        data: dict[str, Any] = {"run_id": run_id, "phase": phase, "code": code, "attempt": attempt}
        if status is not None:
            data["status"] = status
        if body_bytes is not None and body_sha256 is not None:
            data.update(body_bytes=body_bytes, body_sha256=body_sha256)
        if unexpected is not None:
            data["traceback"] = [
                {"file": Path(frame.f_code.co_filename).name, "line": line, "function": frame.f_code.co_name}
                for frame, line in traceback.walk_tb(unexpected.__traceback__)
            ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        message = json.dumps(data, separators=(",", ":"))
        self._handler.handle(logging.LogRecord("kemo_bridge.forensics", logging.ERROR, "", 0, message, (), None))

    def close(self) -> None:
        self._handler.close()
