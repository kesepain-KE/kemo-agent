from __future__ import annotations

import ast
import json
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from run.memory import contains_sensitive_credential
from run.config import read_expand_meta
from run.config import validate_user_name


_ACTIONS = frozenset({"list", "create", "validate"})
_SCOPES = frozenset({"user", "shared"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RESERVED_HEADING_RE = re.compile(r"^##\s+(?:注入层|操作层)\s*$", re.MULTILINE)
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:api_?key|token|password|passwd|secret|cookie|private_?key|验证码|密码|密钥|令牌)"
)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_REQUIRED_EXPAND_FIELDS = {
    "name",
    "explain",
    "open_input",
    "input_data",
    "input_health",
    "start_update",
    "open_control",
    "start_expand",
    "start_control",
}
_OPTIONAL_EXPAND_FIELDS = {"recent_update"}
_FILE_FIELDS = {
    "input_data": ".md",
    "start_update": ".py",
    "start_expand": ".py",
    "start_control": ".md",
}
_CREATE_FILES = (
    "expand.json",
    "expand_control.md",
    "start_expand.py",
    "data_update.py",
    "input_data.md",
)
_MAX_EXPLAIN_CHARS = 2_000
_MAX_MARKDOWN_CHARS = 100_000
_MAX_CODE_CHARS = 500_000
_BUNDLED_EXPAND_TEMPLATE = Path(__file__).resolve().parents[2] / "template" / "expand"


_INPUT_DATA_TEMPLATE = "# 数据采集\n\n> 运行 data_update.py 后自动刷新\n\n暂无数据\n"


def _bundled_template(name: str, fallback: str = "") -> str:
    """Use the root Expand skeleton as the single production template source."""

    path = _BUNDLED_EXPAND_TEMPLATE / name
    try:
        content = path.read_text("utf-8")
    except (OSError, UnicodeError) as exc:
        if fallback:
            return fallback
        raise RuntimeError(f"根目录拓展模板不可读：{path}") from exc
    content = content.strip()
    if content:
        return content
    if fallback:
        return fallback
    raise RuntimeError(f"根目录拓展模板为空：{path}")


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
        raise ValueError("拓展路径越出项目根目录") from None
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError("拓展路径不允许包含符号链接或目录联接")


def _validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("拓展名必须是字符串")
    name = value.strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"拓展名无效：{name!r}。必须以字母开头，仅含字母、数字、下划线或连字符，最长 64 字符"
        )
    return name


def _required_text(value: Any, field: str, max_chars: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"create 需要非空 {field}")
    text = value.strip()
    if len(text) > max_chars:
        raise ValueError(f"{field} 超过最大长度 {max_chars}")
    if contains_sensitive_credential(text):
        raise ValueError(f"{field} 包含疑似敏感凭据，请改用环境变量读取")
    return text


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _literal_secret(value: ast.AST | None) -> bool:
    return isinstance(value, ast.Constant) and isinstance(value.value, str) and len(value.value.strip()) >= 4


def _python_contains_sensitive_credential(source: str) -> bool:
    """Detect embedded literals while allowing credentials loaded from the environment."""

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if contains_sensitive_credential(node.value):
                return True
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(_SENSITIVE_NAME_RE.search(_target_name(target)) for target in targets):
                if _literal_secret(node.value):
                    return True
        elif isinstance(node, ast.keyword) and node.arg and _SENSITIVE_NAME_RE.search(node.arg):
            if _literal_secret(node.value):
                return True
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    if _SENSITIVE_NAME_RE.search(key.value) and _literal_secret(value):
                        return True
    return False


def _resolve_scope_dir(root: Path, scope: Any, user: str) -> Path:
    if not isinstance(scope, str) or scope not in _SCOPES:
        raise ValueError(f"scope 必须是 user 或 shared，而不是 {scope!r}")
    resolved_root = root.resolve()
    if scope == "shared":
        base = resolved_root / "shared_expand"
    else:
        try:
            user_name = validate_user_name(user)
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        base = resolved_root / "users" / user_name / "expand"
    unresolved = base
    resolved = base.resolve(strict=False)
    if not _is_within(resolved, resolved_root):
        raise ValueError("拓展作用域路径越出项目根目录")
    _reject_link_components(resolved_root, unresolved)
    return unresolved


def _module_dir(root: Path, scope: str, user: str, name: str) -> Path:
    base = _resolve_scope_dir(root, scope, user)
    target = base / name
    resolved_base = base.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if not _is_within(resolved_target, resolved_base):
        raise ValueError("拓展模块路径越出目标作用域")
    _reject_link_components(root.resolve(), target)
    return target


def _write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content.rstrip())
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_manifest(module_dir: Path) -> tuple[dict[str, Any], list[str]]:
    path = module_dir / "expand.json"
    if _is_link(path):
        return {}, ["expand.json 不允许是符号链接或目录联接"]
    if not path.is_file():
        return {}, ["expand.json 缺失"]
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"expand.json 不可读或 JSON 无效：{exc}"]
    if not isinstance(value, dict):
        return {}, ["expand.json 根节点必须是对象"]
    return value, []


def _validate_python(path: Path, field: str, errors: list[str]) -> None:
    try:
        source = path.read_text("utf-8-sig")
        compile(source, path.name, "exec")
    except (OSError, UnicodeError, SyntaxError) as exc:
        errors.append(f"{field} Python 代码无效：{exc}")


def _run_validate(root: Path, scope: str, user: str, name: str) -> dict[str, Any]:
    name = _validate_name(name)
    module_dir = _module_dir(root, scope, user, name)
    relative = module_dir.relative_to(root.resolve()).as_posix()
    if _is_link(module_dir):
        return {"action": "validate", "scope": scope, "module": name, "path": relative, "valid": False, "errors": ["模块目录不允许是符号链接或目录联接"]}
    if not module_dir.is_dir():
        return {"action": "validate", "scope": scope, "module": name, "path": relative, "valid": False, "errors": ["模块目录不存在"]}

    manifest, errors = _read_manifest(module_dir)
    if manifest:
        missing = sorted(_REQUIRED_EXPAND_FIELDS - set(manifest))
        unknown = sorted(set(manifest) - _REQUIRED_EXPAND_FIELDS - _OPTIONAL_EXPAND_FIELDS)
        if missing:
            errors.append("expand.json 缺少字段：" + ", ".join(missing))
        if unknown:
            errors.append("expand.json 包含未知字段：" + ", ".join(unknown))
        for field in ("name", "explain"):
            if not isinstance(manifest.get(field), str) or not manifest[field].strip():
                errors.append(f"{field} 必须是非空字符串")
        for field in ("open_input", "open_control"):
            if not isinstance(manifest.get(field), bool):
                errors.append(f"{field} 必须是布尔值")
        if manifest.get("input_health") not in {"正常", "异常"}:
            errors.append("input_health 必须是“正常”或“异常”")
        recent_update = manifest.get("recent_update")
        if recent_update is not None:
            if not isinstance(recent_update, str):
                errors.append("recent_update 必须是字符串")
            else:
                try:
                    datetime.strptime(recent_update.strip(), _TIME_FORMAT)
                except ValueError:
                    errors.append(f"recent_update 必须符合 {_TIME_FORMAT}")

        for field, suffix in _FILE_FIELDS.items():
            file_name = manifest.get(field)
            if not isinstance(file_name, str) or not file_name.strip():
                errors.append(f"{field} 必须是非空字符串")
                continue
            file_name = file_name.strip()
            if Path(file_name).name != file_name or Path(file_name).suffix.casefold() != suffix:
                errors.append(f"{field} 必须是模块目录内的 {suffix} 文件名")
                continue
            target = module_dir / file_name
            if _is_link(target):
                errors.append(f"{field} 指向的文件不允许是符号链接或目录联接：{file_name}")
            elif not target.is_file():
                errors.append(f"{field} 指向的文件不存在：{file_name}")
            elif suffix == ".py":
                _validate_python(target, field, errors)

        control_name = manifest.get("start_control")
        if isinstance(control_name, str) and Path(control_name).name == control_name:
            control_path = module_dir / control_name
            if control_path.is_file() and not _is_link(control_path):
                try:
                    control = control_path.read_text("utf-8-sig")
                except (OSError, UnicodeError) as exc:
                    errors.append(f"start_control 不可读：{exc}")
                else:
                    injection_match = re.search(r"^##\s+注入层\s*$", control, re.MULTILINE)
                    operation_match = re.search(r"^##\s+操作层\s*$", control, re.MULTILINE)
                    if injection_match is None:
                        errors.append("start_control 缺少 ## 注入层")
                    if operation_match is None:
                        errors.append("start_control 缺少 ## 操作层")
                    if injection_match and operation_match:
                        if operation_match.start() <= injection_match.end():
                            errors.append("start_control 必须先写注入层，再写操作层")
                        else:
                            if not control[injection_match.end():operation_match.start()].strip():
                                errors.append("start_control 注入层内容不能为空")
                            if not control[operation_match.end():].strip():
                                errors.append("start_control 操作层内容不能为空")

    runtime_meta = read_expand_meta(module_dir)
    if not runtime_meta.valid and runtime_meta.error and runtime_meta.error not in errors:
        errors.append(f"运行时校验失败：{runtime_meta.error}")
    return {
        "action": "validate",
        "scope": scope,
        "module": name,
        "path": relative,
        "valid": not errors,
        "errors": errors,
        "name": str(manifest.get("name") or name) if manifest else name,
        "explain": str(manifest.get("explain") or "") if manifest else "",
        "input_health": str(manifest.get("input_health") or "未知") if manifest else "未知",
    }


def _run_list(root: Path, scope: str, user: str) -> dict[str, Any]:
    base = _resolve_scope_dir(root, scope, user)
    if not base.is_dir():
        return {"action": "list", "scope": scope, "count": 0, "modules": []}
    modules: list[dict[str, Any]] = []
    for path in sorted(base.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if path.name.startswith(".") or path.name == "__pycache__" or not path.is_dir() or _is_link(path):
            continue
        try:
            result = _run_validate(root, scope, user, path.name)
        except (ValueError, OSError) as exc:
            result = {
                "module": path.name,
                "valid": False,
                "errors": [str(exc)],
                "name": path.name,
                "explain": "",
                "input_health": "异常",
            }
        modules.append({
            "name": result.get("module", path.name),
            "display_name": result.get("name", path.name),
            "explain": result.get("explain", ""),
            "input_health": result.get("input_health", "异常"),
            "valid": bool(result.get("valid")),
            "errors": result.get("errors", []),
        })
    return {"action": "list", "scope": scope, "count": len(modules), "modules": modules}


def _run_create(
    root: Path,
    scope: str,
    user: str,
    name: str,
    explain: Any,
    injection: Any,
    operations: Any,
    open_input: Any,
    start_expand: Any,
    data_update: Any,
) -> dict[str, Any]:
    name = _validate_name(name)
    explain_text = _required_text(explain, "explain", _MAX_EXPLAIN_CHARS)
    injection_text = _required_text(injection, "injection", _MAX_MARKDOWN_CHARS)
    operations_text = _required_text(operations, "operations", _MAX_MARKDOWN_CHARS)
    if _RESERVED_HEADING_RE.search(injection_text) or _RESERVED_HEADING_RE.search(operations_text):
        raise ValueError("injection 和 operations 不得重复包含 ## 注入层 或 ## 操作层标题")
    if not isinstance(open_input, bool):
        raise ValueError("open_input 必须是布尔值")
    if start_expand is not None and not isinstance(start_expand, str):
        raise ValueError("start_expand 必须是字符串")
    if data_update is not None and not isinstance(data_update, str):
        raise ValueError("data_update 必须是字符串")
    start_code = (start_expand or "").strip() or _bundled_template("start_expand.py")
    update_code = (data_update or "").strip() or _bundled_template("data_update.py")
    for field, source in (("start_expand", start_code), ("data_update", update_code)):
        if len(source) > _MAX_CODE_CHARS:
            raise ValueError(f"{field} 超过最大长度 {_MAX_CODE_CHARS}")
        try:
            tree = ast.parse(source, filename=f"{field}.py", mode="exec")
            compile(tree, f"{field}.py", "exec")
        except SyntaxError as exc:
            raise ValueError(f"{field} Python 代码无效：{exc}") from exc
        if _python_contains_sensitive_credential(source):
            raise ValueError(f"{field} 包含疑似硬编码敏感凭据，请改用环境变量读取")

    base = _resolve_scope_dir(root, scope, user)
    base.mkdir(parents=True, exist_ok=True)
    _reject_link_components(root.resolve(), base)
    module_dir = _module_dir(root, scope, user, name)
    if module_dir.exists() or _is_link(module_dir):
        raise FileExistsError(f"拓展模块已存在：{scope}:{name}")

    manifest = {
        "name": name,
        "explain": explain_text,
        "open_input": open_input,
        "input_data": "input_data.md",
        "input_health": "异常",
        "start_update": "data_update.py",
        "open_control": True,
        "start_expand": "start_expand.py",
        "start_control": "expand_control.md",
    }
    control = f"## 注入层\n\n{injection_text}\n\n## 操作层\n\n{operations_text}\n"
    temporary = base / f".{name}.{uuid.uuid4().hex}.tmp"
    published = False
    try:
        temporary.mkdir()
        _write_text(temporary / "expand.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _write_text(temporary / "expand_control.md", control)
        _write_text(temporary / "start_expand.py", start_code)
        _write_text(temporary / "data_update.py", update_code)
        _write_text(
            temporary / "input_data.md",
            _bundled_template("input_data.md", _INPUT_DATA_TEMPLATE),
        )
        if module_dir.exists() or _is_link(module_dir):
            raise FileExistsError(f"拓展模块已存在：{scope}:{name}")
        os.rename(temporary, module_dir)
        published = True
        validation = _run_validate(root, scope, user, name)
        if not validation["valid"]:
            raise ValueError("创建后的拓展未通过运行时校验：" + "; ".join(validation["errors"]))
    except Exception:
        if published and module_dir.is_dir() and not _is_link(module_dir):
            shutil.rmtree(module_dir, ignore_errors=True)
        raise
    finally:
        if temporary.is_dir() and not _is_link(temporary):
            shutil.rmtree(temporary, ignore_errors=True)

    return {
        "action": "create",
        "scope": scope,
        "name": name,
        "path": module_dir.relative_to(root.resolve()).as_posix(),
        "files": list(_CREATE_FILES),
        "valid": True,
        "next_steps": [
            "按实际复杂度在模块目录内自由实现；声明入口可以直接处理，也可以适配完整内部工程",
            "保持 Prompt 数据出口有界，操控入口返回结构化结果",
            "运行清单声明的更新入口初始化 input_data.md",
        ],
    }


def run(
    action: str,
    scope: str,
    name: str = "",
    explain: str = "",
    injection: str = "",
    operations: str = "",
    open_input: bool = True,
    start_expand: str = "",
    data_update: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in _ACTIONS:
        raise ValueError(f"未知 expand_creater action：{action}")
    if scope not in _SCOPES:
        raise ValueError(f"未知 expand_creater scope：{scope}")
    if not isinstance(context, dict) or not context.get("root") or not context.get("user"):
        raise ValueError("工具上下文缺少 root 或 user")
    root = Path(str(context["root"])).resolve()
    user = str(context["user"])
    if action == "list":
        return _run_list(root, scope, user)
    if action == "validate":
        return _run_validate(root, scope, user, name)
    return _run_create(
        root,
        scope,
        user,
        name,
        explain,
        injection,
        operations,
        open_input,
        start_expand,
        data_update,
    )
