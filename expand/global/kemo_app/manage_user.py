"""本地用户密码管理工具；密码只经 getpass 读取。"""

from __future__ import annotations

import argparse
import getpass
import json
from pathlib import Path

from auth import UserStore
from credential_registry import refresh_registry

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("username")
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    cfg = json.loads((BASE_DIR / "config.json").read_text(encoding="utf-8"))
    path = BASE_DIR / str(cfg.get("users_path", "users.json"))
    password = getpass.getpass("App user password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("passwords do not match")
    UserStore(path, int(cfg.get("pbkdf2_iterations", 310000))).set_password(args.username, password, not args.disable)
    refresh_registry()
    print(f"updated user: {args.username}")


if __name__ == "__main__":
    main()
