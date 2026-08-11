"""uvicorn 守护服务入口。"""

from __future__ import annotations

import json
from pathlib import Path

import uvicorn

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    config = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
    uvicorn.run(
        "app:app",
        host=str(config.get("host", "127.0.0.1")),
        port=int(config.get("port", 8742)),
        app_dir=str(BASE_DIR),
        access_log=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
