"""Create local runtime configuration without activating or starting the bridge."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from lifecycle import inspect_configuration


BASE_DIR = Path(__file__).resolve().parent


def _atomic_json(path: Path, payload: Any, *, sensitive: bool = False) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    if sensitive:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def initialize(base_dir: Path | None = None) -> dict[str, Any]:
    root = (base_dir or BASE_DIR).resolve()
    example_path = root / "config.example.json"
    config_path = root / "config.json"
    users_path = root / "users.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    if not isinstance(example, dict):
        raise ValueError("config.example.json 顶层必须是 JSON 对象")

    if config_path.is_file():
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("config.json 顶层必须是 JSON 对象")
        created = False
    else:
        config = dict(example)
        created = True
    if len(str(config.get("session_secret") or "")) < 32:
        config["session_secret"] = secrets.token_urlsafe(48)
    config.pop("note", None)
    _atomic_json(config_path, config, sensitive=True)
    if not users_path.exists():
        _atomic_json(users_path, {}, sensitive=True)

    state = inspect_configuration(root)
    return {
        "ok": True,
        "created": created,
        **state,
        "message": "初始化完成；尚未激活。请继续配置设备 Token 和至少一个用户。",
    }


def main() -> None:
    result = initialize()
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
