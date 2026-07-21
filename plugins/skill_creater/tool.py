from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from run.memory import contains_sensitive_credential
from run.users import validate_user_name


_INVALID_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
_SECONDARY_HEADING_RE = re.compile(r"^##\s+")
_TOOL_HEADING_RE = re.compile(r"^##\s+Tool\s*$", re.IGNORECASE)
_JSON_FENCE_RE = re.compile(r"```json\s*\n(.*?)\n```", re.IGNORECASE | re.DOTALL)
_SCOPES = frozenset({"agent_create", "user_create", "shared"})
_ACTIONS = frozenset({"list", "get", "validate", "create", "update", "delete"})
_MAX_CONTENT_CHARS = 500_000


def _validate_name(value: Any) -> str:
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


def _is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _reject_link_components(root: Path, target: Path) -> None:
    resolved_root = root.resolve()
    candidate = target if target.is_absolute() else resolved_root / target
    try:
        relative = candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError("技能路径越出项目根目录") from None
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError("技能路径不允许包含符号链接或目录联接")


def _base_path(root: Path, user: str, scope: str) -> Path:
    if scope == "shared":
        base = root / "shared_skills"
    else:
        base = root / "users" / user / "user_skills" / scope
    resolved = base.resolve(strict=False)
    if not _is_within(resolved, root):
        raise ValueError("技能作用域路径越界")
    _reject_link_components(root, base)
    return base


def _skill_paths(root: Path, user: str, scope: str, name: Any) -> tuple[str, Path, Path, Path]:
    skill_name = _validate_name(name)
    base = _base_path(root, user, scope)
    skill_dir = base / skill_name
    if not _is_within(skill_dir.resolve(strict=False), base.resolve(strict=False)):
        raise ValueError("技能路径越界")
    _reject_link_components(root, skill_dir)
    return skill_name, base, skill_dir, skill_dir / "SKILL.md"


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


def _read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise OSError(f"无法读取技能文件：{exc}") from exc


def _validate_content(content: str) -> list[str]:
    errors: list[str] = []
    lines = content.splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    title_lines = [line.strip() for line in lines if _TITLE_RE.fullmatch(line.strip())]
    if not non_empty or not _TITLE_RE.fullmatch(non_empty[0]):
        errors.append("第一个非空行必须是一级标题（# 技能名）")
    if len(title_lines) > 1:
        errors.append("SKILL.md 只能包含一个一级标题")

    tool_headings = [index for index, line in enumerate(lines) if _TOOL_HEADING_RE.fullmatch(line.strip())]
    if len(tool_headings) > 1:
        errors.append("SKILL.md 只能包含一个 ## Tool")
    elif tool_headings:
        start = tool_headings[0] + 1
        end = len(lines)
        for index in range(start, len(lines)):
            if _SECONDARY_HEADING_RE.match(lines[index].strip()):
                end = index
                break
        matches = list(_JSON_FENCE_RE.finditer("\n".join(lines[start:end])))
        if not matches:
            errors.append("## Tool 下缺少 JSON 代码块")
        elif len(matches) > 1:
            errors.append("## Tool 下只能包含一个 JSON 代码块")
        else:
            try:
                tool_schema = json.loads(matches[0].group(1))
            except json.JSONDecodeError as exc:
                errors.append(f"Tool JSON 无效：{exc}")
            else:
                if not isinstance(tool_schema, dict):
                    errors.append("Tool JSON 必须是对象")
    return errors


def _run_list(root: Path, user: str, scope: str) -> dict[str, Any]:
    base = _base_path(root, user, scope)
    if not base.is_dir():
        return {"action": "list", "scope": scope, "skills": []}
    skills: list[dict[str, str]] = []
    for path in sorted(base.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if path.name.startswith(".") or path.name == "__pycache__" or not path.is_dir() or _is_link(path):
            continue
        skill_file = path / "SKILL.md"
        if _is_link(skill_file) or not skill_file.is_file():
            continue
        title = path.name
        try:
            for line in _read_text(skill_file).splitlines():
                matched = _TITLE_RE.fullmatch(line.strip())
                if matched:
                    title = matched.group(1).strip()
                    break
        except OSError:
            pass
        skills.append({"name": path.name, "title": title})
    return {"action": "list", "scope": scope, "skills": skills}


def _run_get(root: Path, user: str, scope: str, name: Any) -> dict[str, Any]:
    skill_name, _, skill_dir, skill_file = _skill_paths(root, user, scope, name)
    if _is_link(skill_dir) or _is_link(skill_file):
        raise ValueError("拒绝读取符号链接或目录联接技能")
    if not skill_dir.is_dir() or not skill_file.is_file():
        raise FileNotFoundError(f"技能不存在：{scope}/{skill_name}")
    return {
        "action": "get",
        "scope": scope,
        "name": skill_name,
        "content": _read_text(skill_file),
    }


def _run_validate(root: Path, user: str, scope: str, name: Any) -> dict[str, Any]:
    skill_name, _, skill_dir, skill_file = _skill_paths(root, user, scope, name)
    errors: list[str] = []
    if _is_link(skill_dir) or _is_link(skill_file):
        errors.append("技能目录和 SKILL.md 不允许是符号链接或目录联接")
    elif not skill_dir.is_dir():
        errors.append("技能目录不存在")
    elif not skill_file.is_file():
        errors.append("SKILL.md 不存在")
    else:
        try:
            content = _read_text(skill_file)
        except OSError as exc:
            errors.append(str(exc))
        else:
            errors.extend(_validate_content(content))
    return {
        "action": "validate",
        "scope": scope,
        "name": skill_name,
        "valid": not errors,
        "errors": errors,
    }


def _assemble_skill_content(
    title: Any,
    description: Any,
    instruction: Any,
    tool_schema: Any,
) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("结构化模式需要非空 title")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("结构化模式需要非空 description")
    instruction_value = instruction.strip() if isinstance(instruction, str) and instruction.strip() else None
    tool_provided = tool_schema is not None
    if instruction_value is not None and tool_provided:
        raise ValueError("instruction 与 tool_schema 只能提供一个")
    if instruction is not None and instruction_value is None:
        raise ValueError("instruction 必须是非空字符串")
    if tool_provided and not isinstance(tool_schema, dict):
        raise ValueError("tool_schema 必须是对象")
    if instruction_value is None and not tool_provided:
        raise ValueError("结构化模式需要 instruction 或 tool_schema")

    lines = [f"# {title.strip()}", "", description.strip()]
    if tool_provided:
        lines.extend(("", "## Tool", "", "```json", json.dumps(tool_schema, ensure_ascii=False, indent=2), "```"))
    else:
        lines.extend(("", instruction_value or ""))
    return "\n".join(lines) + "\n"


def _prepare_content(
    action: str,
    content: Any,
    title: Any,
    description: Any,
    instruction: Any,
    tool_schema: Any,
) -> str:
    if content is not None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"{action} 需要非空 content")
        body = content.strip()
    else:
        body = _assemble_skill_content(title, description, instruction, tool_schema).strip()
    if len(body) > _MAX_CONTENT_CHARS:
        raise ValueError(f"技能内容超过最大长度 {_MAX_CONTENT_CHARS}")
    if contains_sensitive_credential(body):
        raise ValueError("技能内容包含疑似敏感凭据")
    return body


def run(
    action: str,
    scope: str,
    name: str = "",
    content: str | None = None,
    title: str = "",
    description: str = "",
    instruction: str | None = None,
    tool_schema: dict[str, Any] | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in _ACTIONS:
        raise ValueError(f"未知 skill_creater action：{action}")
    if scope not in _SCOPES:
        raise ValueError(f"未知 skill_creater scope：{scope}")
    if not isinstance(context, dict) or not context.get("root") or not context.get("user"):
        raise ValueError("工具上下文缺少 root 或 user")
    if context.get("agent") == "self_improve" and scope != "agent_create":
        raise PermissionError("self_improve 只能写入 agent_create 技能目录")

    root = Path(str(context["root"])).resolve()
    try:
        user = validate_user_name(str(context["user"]))
    except Exception as exc:
        raise ValueError(str(exc)) from exc

    if action == "list":
        return _run_list(root, user, scope)
    if action == "get":
        return _run_get(root, user, scope, name)
    if action == "validate":
        return _run_validate(root, user, scope, name)

    skill_name, base, skill_dir, skill_file = _skill_paths(root, user, scope, name)
    if action == "delete":
        if _is_link(skill_dir):
            raise ValueError("拒绝删除符号链接或目录联接技能目录")
        if not skill_dir.exists():
            return {"action": action, "scope": scope, "name": skill_name, "deleted": False}
        if not skill_dir.is_dir():
            raise ValueError("技能路径不是目录")
        shutil.rmtree(skill_dir)
        return {"action": action, "scope": scope, "name": skill_name, "deleted": True}

    body = _prepare_content(action, content, title, description, instruction, tool_schema)
    if action == "create":
        if skill_dir.exists() or _is_link(skill_dir):
            raise FileExistsError(f"技能已存在：{skill_name}")
        base.mkdir(parents=True, exist_ok=True)
        _reject_link_components(root, base)
        skill_dir.mkdir()
        try:
            _atomic_text(skill_file, body)
            validation = _run_validate(root, user, scope, skill_name)
            if not validation["valid"]:
                raise ValueError("创建后的技能未通过校验：" + "; ".join(validation["errors"]))
        except Exception:
            if skill_dir.is_dir() and not _is_link(skill_dir):
                shutil.rmtree(skill_dir, ignore_errors=True)
            raise
    else:
        if _is_link(skill_dir) or _is_link(skill_file):
            raise ValueError("拒绝更新符号链接或目录联接技能")
        if not skill_dir.is_dir() or not skill_file.is_file():
            raise FileNotFoundError(f"技能不存在：{skill_name}")
        previous = _read_text(skill_file)
        try:
            _atomic_text(skill_file, body)
            validation = _run_validate(root, user, scope, skill_name)
            if not validation["valid"]:
                raise ValueError("更新后的技能未通过校验：" + "; ".join(validation["errors"]))
        except Exception:
            _atomic_text(skill_file, previous)
            raise

    return {
        "action": action,
        "scope": scope,
        "name": skill_name,
        "path": str(skill_dir),
        "valid": True,
    }
