"""生成并核对桥接服务凭据配置摘要；绝不保存明文凭据。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
REGISTRY_PATH = BASE_DIR / "credential_registry.json"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def build_snapshot() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    users_path = BASE_DIR / str(config.get("users_path", "users.json"))
    try:
        users = json.loads(users_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        users = {}
    token_hash = str(config.get("token_sha256") or "").strip().lower()
    user_items: list[dict[str, Any]] = []
    for username, record in sorted(users.items() if isinstance(users, dict) else []):
        if not isinstance(record, dict):
            continue
        verification_record = {
            "salt": str(record.get("salt") or ""),
            "hash": str(record.get("hash") or ""),
            "iterations": int(record.get("iterations") or config.get("pbkdf2_iterations") or 310000),
            "enabled": bool(record.get("enabled", True)),
        }
        user_items.append(
            {
                "username": str(username),
                "configured": bool(verification_record["salt"] and verification_record["hash"]),
                "enabled": verification_record["enabled"],
                "iterations": verification_record["iterations"],
                "record_fingerprint": _fingerprint(verification_record),
            }
        )
    return {
        "device_token": {
            "configured": len(token_hash) == 64,
            "sha256_fingerprint": f"{token_hash[:10]}...{token_hash[-10:]}" if len(token_hash) == 64 else "",
            "source": CONFIG_PATH.name,
        },
        "users": user_items,
        "security": {
            "plaintext_stored": False,
            "note": "本文件只保存配置状态和不可逆核对指纹，不保存设备 Token、用户密码、盐或完整密码哈希。",
        },
    }


def refresh_registry() -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        **build_snapshot(),
    }
    temporary = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, REGISTRY_PATH)
    try:
        os.chmod(REGISTRY_PATH, 0o600)
    except OSError:
        pass
    return payload


def registry_matches() -> bool:
    try:
        current = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    expected = build_snapshot()
    return all(current.get(key) == value for key, value in expected.items())


def main() -> None:
    parser = argparse.ArgumentParser(description="维护 kemo app 桥接服务凭据核对摘要")
    parser.add_argument("--check", action="store_true", help="只核对摘要是否与当前配置一致")
    args = parser.parse_args()
    if args.check:
        matched = registry_matches()
        print(json.dumps({"ok": matched, "registry": str(REGISTRY_PATH)}, ensure_ascii=False))
        raise SystemExit(0 if matched else 1)
    payload = refresh_registry()
    print(json.dumps({"ok": True, "registry": str(REGISTRY_PATH), "users": len(payload["users"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
