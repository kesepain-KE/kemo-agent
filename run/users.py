"""User-directory discovery, validation and bootstrap helpers."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path


class UserDataError(RuntimeError):
    """User data cannot be located or prepared."""


_USER_RE = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f.][^\\/:*?\"<>|\x00-\x1f]{0,63}$")


def validate_user_name(user: str) -> str:
    name = user.strip()
    if not name or not _USER_RE.fullmatch(name) or name in {"_template", ".", ".."}:
        raise UserDataError(f"非法用户名称：{user!r}")
    return name


def user_dir(user: str, root: Path) -> Path:
    name = validate_user_name(user)
    path = root / "users" / name
    if not path.is_dir():
        raise UserDataError(f"用户不存在：{name}")
    return path


def list_users(root: Path) -> list[str]:
    directory = root / "users"
    if not directory.is_dir():
        return []
    return sorted(
        item.name
        for item in directory.iterdir()
        if item.is_dir() and not item.name.startswith("_")
    )


def ensure_user(user: str, root: Path) -> Path:
    name = validate_user_name(user)
    destination = root / "users" / name
    if destination.exists():
        if not destination.is_dir():
            raise UserDataError(f"用户路径不是目录：{destination}")
        return destination
    template = root / "users" / "_template"
    if not template.is_dir():
        raise UserDataError(f"用户模板不存在：{template}")
    shutil.copytree(template, destination)
    return destination


def write_json_if_empty(path: Path, default: object) -> None:
    if path.exists() and path.stat().st_size > 0:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(default, ensure_ascii=False, indent=2) + "\n", "utf-8")
