from __future__ import annotations

import ast
import json
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, cast

from run.memory import contains_sensitive_credential
from run.modules import ModuleSpecError, read_manifest, write_manifest
from run.config import validate_user_name
from web.services.module_panels import load_module_panel
from plugins._creator_security import (
    is_link as _is_link,
    is_within as _is_within,
    reject_link_components,
)


_ACTIONS = frozenset({"list", "create", "validate"})
_SCOPES = frozenset({"user", "shared"})
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_RESERVED_HEADING_RE = re.compile(r"^##\s+(?:注入层|操作层)\s*$", re.MULTILINE)
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:api_?key|token|password|passwd|secret|cookie|private_?key|验证码|密码|密钥|令牌)"
)
_CREATE_FILES = (
    "module.yaml",
    "expand_control.md",
    "start_expand.py",
    "data_update.py",
    "input_data.md",
    "module/panel.json",
    "module/panel.values.json",
    "module/status.json",
    "module/README.md",
)
_PANEL_FILES = ("panel.json", "panel.values.json", "status.json", "README.md")
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


def _reject_link_components(root: Path, target: Path) -> None:
    reject_link_components(root, target, label="拓展")


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
        base = resolved_root / "expand" / "share"
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


def _copy_panel_template(
    target: Path,
    module_name: str,
    *,
    include_default_action: bool,
) -> None:
    source = _BUNDLED_EXPAND_TEMPLATE / "module"
    if not source.is_dir() or _is_link(source):
        raise RuntimeError(f"根目录拓展组件模板不可用：{source}")
    target.mkdir()
    for filename in _PANEL_FILES:
        source_file = source / filename
        if not source_file.is_file() or _is_link(source_file):
            raise RuntimeError(f"根目录拓展组件模板文件不可用：{source_file}")
        content = source_file.read_text("utf-8")
        if filename == "panel.json":
            panel = json.loads(content)
            if not isinstance(panel, dict):
                raise RuntimeError("根目录拓展组件 panel.json 顶层必须是对象")
            panel["title"] = f"{module_name} · 用户配置"
            if not include_default_action:
                panel["containers"] = [
                    container
                    for container in panel.get("containers", [])
                    if isinstance(container, dict) and container.get("kind") != "action"
                ]
            content = json.dumps(panel, ensure_ascii=False, indent=2)
        _write_text(target / filename, content)


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
        return {"ok": False, "action": "validate", "scope": scope, "module": name, "path": relative, "valid": False, "errors": ["模块目录不允许是符号链接或目录联接"]}
    if not module_dir.is_dir():
        return {"ok": False, "action": "validate", "scope": scope, "module": name, "path": relative, "valid": False, "errors": ["模块目录不存在"]}

    errors = []
    spec = None
    try:
        spec = read_manifest(module_dir, kind_hint="expand", scope=scope)
    except ModuleSpecError as exc:
        errors.append(str(exc))
    if spec:
        _validate_python(spec.update_script, "update_script", errors)
        if spec.control_open:
            _validate_python(cast(Path, spec.control_script), "control_script", errors)
            try:
                control = cast(Path, spec.control_entry).read_text("utf-8-sig")
            except (OSError, UnicodeError) as exc:
                errors.append(f"control_entry 不可读：{exc}")
            else:
                injection = re.search(r"^##\s+注入层\s*$", control, re.MULTILINE)
                operation = re.search(r"^##\s+操作层\s*$", control, re.MULTILINE)
                if injection is None or operation is None:
                    errors.append("control_entry 缺少 ## 注入层 或 ## 操作层")
                elif operation.start() <= injection.end():
                    errors.append("control_entry 必须先写注入层，再写操作层")
                elif not control[injection.end():operation.start()].strip() or not control[operation.end():].strip():
                    errors.append("control_entry 注入层和操作层内容不能为空")
    panel_enabled = False
    panel_path = module_dir / "module" / "panel.json"
    if panel_path.exists() or _is_link(panel_path):
        panel, panel_error = load_module_panel(module_dir, "expand")
        if panel is None:
            errors.append(f"组件面板无效：{panel_error or 'panel.json 无法解析'}")
        else:
            panel_enabled = True
    return {
        "ok": not errors,
        "action": "validate",
        "scope": scope,
        "module": name,
        "path": relative,
        "valid": not errors,
        "errors": errors,
        "name": spec.name if spec else name,
        "explain": spec.explain if spec else "",
        "input_health": spec.health if spec else "未知",
        "panel_enabled": panel_enabled,
    }


def _run_list(root: Path, scope: str, user: str) -> dict[str, Any]:
    base = _resolve_scope_dir(root, scope, user)
    if not base.is_dir():
        return {"ok": True, "action": "list", "scope": scope, "count": 0, "modules": []}
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
            "panel_enabled": bool(result.get("panel_enabled")),
            "valid": bool(result.get("valid")),
            "errors": result.get("errors", []),
        })
    return {"ok": True, "action": "list", "scope": scope, "count": len(modules), "modules": modules}


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
    custom_start = bool((start_expand or "").strip())
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
        "kind": "expand", "name": name, "explain": explain_text,
        "input_open": open_input, "input_file": "input_data.md", "health": "异常",
        "update_script": "data_update.py", "control_open": True,
        "control_script": "start_expand.py", "control_entry": "expand_control.md",
    }
    control = f"## 注入层\n\n{injection_text}\n\n## 操作层\n\n{operations_text}\n"
    temporary = base / f".{name}.{uuid.uuid4().hex}.tmp"
    published = False
    try:
        temporary.mkdir()
        write_manifest(temporary / "module.yaml", manifest)
        _write_text(temporary / "expand_control.md", control)
        _write_text(temporary / "start_expand.py", start_code)
        _write_text(temporary / "data_update.py", update_code)
        _write_text(
            temporary / "input_data.md",
            _bundled_template("input_data.md", _INPUT_DATA_TEMPLATE),
        )
        _copy_panel_template(
            temporary / "module",
            name,
            include_default_action=not custom_start,
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
        "ok": True,
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
            "按真实配置项和操控能力调整 module/panel.json；保存后无需重启即可由 Web 热加载",
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
