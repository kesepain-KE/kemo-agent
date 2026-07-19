from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from run.memory import contains_sensitive_credential


_INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SCOPES = frozenset({"agent_create", "user_create", "shared"})
_ACTIONS = frozenset({"create", "update", "delete"})


def _validate_name(value: str) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 64
        or name in {".", ".."}
        or name.startswith(".")
        or name.endswith((".", " "))
        or _INVALID_NAME_RE.search(name)
    ):
        raise ValueError("技能名称无效")
    return name


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _base_path(root: Path, user: str, scope: str) -> Path:
    if scope == "shared":
        return root / "shared_skills"
    return root / "users" / user / "user_skills" / scope


def _atomic_text(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content.rstrip())
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(
    action: str,
    scope: str,
    name: str,
    content: str | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in _ACTIONS:
        raise ValueError(f"未知 skill_creater action：{action}")
    if scope not in _SCOPES:
        raise ValueError(f"未知 skill_creater scope：{scope}")
    if context.get("agent") == "self_improve" and scope != "agent_create":
        raise PermissionError("self_improve 只能写入 agent_create 技能目录")

    root = Path(context["root"]).resolve()
    user = str(context["user"])
    skill_name = _validate_name(name)
    base = _base_path(root, user, scope)
    resolved_base = base.resolve(strict=False)
    if not _is_within(resolved_base, root):
        raise ValueError("技能作用域路径越界")
    skill_dir = base / skill_name
    resolved_skill = skill_dir.resolve(strict=False)
    if not _is_within(resolved_skill, resolved_base):
        raise ValueError("技能路径越界")
    skill_file = skill_dir / "SKILL.md"

    if action == "delete":
        if skill_dir.is_symlink():
            raise ValueError("拒绝删除符号链接技能目录")
        if not skill_dir.exists():
            return {"action": action, "scope": scope, "name": skill_name, "deleted": False}
        if not skill_dir.is_dir():
            raise ValueError("技能路径不是目录")
        shutil.rmtree(skill_dir)
        return {"action": action, "scope": scope, "name": skill_name, "deleted": True}

    body = str(content or "").strip()
    if not body:
        raise ValueError(f"{action} 需要非空 content")
    if contains_sensitive_credential(body):
        raise ValueError("技能内容包含疑似敏感凭据")

    if action == "create":
        if skill_dir.exists() or skill_dir.is_symlink():
            raise FileExistsError(f"技能已存在：{skill_name}")
        base.mkdir(parents=True, exist_ok=True)
        skill_dir.mkdir()
        try:
            _atomic_text(skill_file, body)
        except Exception:
            shutil.rmtree(skill_dir, ignore_errors=True)
            raise
    else:
        if skill_dir.is_symlink() or skill_file.is_symlink():
            raise ValueError("拒绝更新符号链接技能")
        if not skill_dir.is_dir() or not skill_file.is_file():
            raise FileNotFoundError(f"技能不存在：{skill_name}")
        _atomic_text(skill_file, body)

    return {
        "action": action,
        "scope": scope,
        "name": skill_name,
        "path": str(skill_dir),
    }
