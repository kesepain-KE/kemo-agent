"""交互式更新设备令牌哈希，不把明文写入磁盘。"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
from pathlib import Path

from credential_registry import refresh_registry

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def main() -> None:
    token = getpass.getpass("New device token (at least 32 characters): ").strip()
    confirmation = getpass.getpass("Confirm device token: ").strip()
    if token != confirmation:
        raise SystemExit("tokens do not match")
    if len(token) < 32:
        raise SystemExit("token must contain at least 32 characters")
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config["token_sha256"] = hashlib.sha256(token.encode("utf-8")).hexdigest()
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    refresh_registry()
    print("device token hash updated; restart the bridge")


if __name__ == "__main__":
    main()
