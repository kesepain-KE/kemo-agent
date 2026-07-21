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
from zoneinfo import ZoneInfo

from run.memory import contains_sensitive_credential


_ACTIONS = frozenset({"list", "create", "validate"})
_SENSE_JSON_FIELDS = {"name", "data_md", "recent_update", "health", "start_update"}
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SENSITIVE_NAME_RE = re.compile(
    r"(?i)(?:api_?key|token|password|passwd|secret|cookie|private_?key|验证码|密码|密钥|令牌)"
)
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_BEIJING = ZoneInfo("Asia/Shanghai")
_MAX_EXPLAIN_CHARS = 2_000
_MAX_MARKDOWN_CHARS = 100_000
_MAX_CODE_CHARS = 500_000
_CREATE_FILES = ("sense.json", "sense.md", "data_update.py")


_DATA_UPDATE_TEMPLATE = '''#!/usr/bin/env python3
"""数据更新入口：运行后刷新 sense.md 和 sense.json 健康状态。

在 collect_data() 中接入系统接口、传感器或外部 API。不要在源码中硬编码凭据，
需要认证时应从环境变量读取。
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")


def collect_data() -> dict[str, Any]:
    """采集感知数据；请替换为真实、只读的数据采集逻辑。"""

    # TODO: 对接实际数据源并返回结构化数据。
    return {}


def render_markdown(data: dict[str, Any], status: str = "正常") -> str:
    """把采集结果渲染为将要注入 system prompt 的 Markdown。"""

    now = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    lines = ["# 感知数据", "", f"> 最后更新: {now}", f"> 状态: {status}", ""]
    if not data:
        lines.append("暂无数据")
        return "\\n".join(lines) + "\\n"
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"## {key}")
            lines.extend(f"- {child_key}: {child_value}" for child_key, child_value in value.items())
        else:
            lines.append(f"- **{key}**: {value}")
        lines.append("")
    return "\\n".join(lines).rstrip() + "\\n"


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_data()
    atomic_write(base / "sense.md", render_markdown(data))

    manifest_path = base / "sense.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    manifest["recent_update"] = datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    manifest["health"] = "正常"
    atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\\n")
    print(json.dumps({"status": "ok", "updated": "sense.md"}, ensure_ascii=False))


if __name__ == "__main__":
    update()
'''


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
        raise ValueError("感知模块路径越出项目根目录") from None
    current = resolved_root
    for part in relative.parts:
        current = current / part
        if current.exists() and _is_link(current):
            raise ValueError("感知模块路径不允许包含符号链接或目录联接")


def _validate_name(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("感知模块名必须是字符串")
    name = value.strip()
    if not _NAME_RE.fullmatch(name):
        raise ValueError(
            f"感知模块名无效：{name!r}。必须以字母开头，仅含字母、数字、下划线或连字符，最长 64 字符"
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


def _sense_base(root: Path) -> Path:
    base = root.resolve() / "global_sense"
    if not _is_within(base.resolve(strict=False), root.resolve()):
        raise ValueError("全局感知目录越出项目根目录")
    _reject_link_components(root, base)
    return base


def _module_dir(root: Path, name: Any) -> tuple[str, Path, Path]:
    module_name = _validate_name(name)
    base = _sense_base(root)
    module = base / module_name
    if not _is_within(module.resolve(strict=False), base.resolve(strict=False)):
        raise ValueError("感知模块路径越出 global_sense")
    _reject_link_components(root, module)
    return module_name, base, module


def _read_manifest(module: Path) -> tuple[dict[str, Any], list[str]]:
    path = module / "sense.json"
    if _is_link(path):
        return {}, ["sense.json 不允许是符号链接或目录联接"]
    if not path.is_file():
        return {}, ["sense.json 缺失"]
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {}, [f"sense.json 不可读或 JSON 无效：{exc}"]
    if not isinstance(value, dict):
        return {}, ["sense.json 根节点必须是对象"]
    return value, []


def _run_validate(root: Path, name: Any) -> dict[str, Any]:
    module_name, _, module = _module_dir(root, name)
    errors: list[str] = []
    manifest: dict[str, Any] = {}
    if _is_link(module):
        errors.append("模块目录不允许是符号链接或目录联接")
    elif not module.is_dir():
        errors.append("模块目录不存在")
    else:
        manifest, errors = _read_manifest(module)
        if manifest:
            missing = sorted(_SENSE_JSON_FIELDS - set(manifest))
            unknown = sorted(set(manifest) - _SENSE_JSON_FIELDS)
            if missing:
                errors.append("sense.json 缺少字段：" + ", ".join(missing))
            if unknown:
                errors.append("sense.json 包含未知字段：" + ", ".join(unknown))
            for field in sorted(_SENSE_JSON_FIELDS):
                if not isinstance(manifest.get(field), str) or not manifest[field].strip():
                    errors.append(f"{field} 必须是非空字符串")
            health = manifest.get("health")
            if isinstance(health, str) and health.strip() not in {"正常", "异常"}:
                errors.append("health 必须是“正常”或“异常”")
            recent_update = manifest.get("recent_update")
            if isinstance(recent_update, str) and recent_update.strip():
                try:
                    datetime.strptime(recent_update.strip(), _TIME_FORMAT)
                except ValueError:
                    errors.append(f"recent_update 必须符合 {_TIME_FORMAT}")

            file_fields = {"data_md": ".md", "start_update": ".py"}
            for field, suffix in file_fields.items():
                raw_name = manifest.get(field)
                if not isinstance(raw_name, str) or not raw_name.strip():
                    continue
                file_name = raw_name.strip()
                if Path(file_name).name != file_name or Path(file_name).suffix.casefold() != suffix:
                    errors.append(f"{field} 必须是模块目录内的 {suffix} 文件名")
                    continue
                target = module / file_name
                if _is_link(target):
                    errors.append(f"{field} 指向的文件不允许是符号链接或目录联接：{file_name}")
                elif not target.is_file():
                    errors.append(f"{field} 指向的文件不存在：{file_name}")
                elif suffix == ".py":
                    try:
                        source = target.read_text("utf-8-sig")
                        compile(source, target.name, "exec")
                    except (OSError, UnicodeError, SyntaxError) as exc:
                        errors.append(f"start_update Python 代码无效：{exc}")
    return {
        "action": "validate",
        "name": module_name,
        "valid": not errors,
        "errors": errors,
        "health": str(manifest.get("health") or "异常") if manifest else "异常",
        "recent_update": str(manifest.get("recent_update") or "") if manifest else "",
        "data_md": str(manifest.get("data_md") or "") if manifest else "",
    }


def _run_list(root: Path) -> dict[str, Any]:
    base = _sense_base(root)
    if not base.is_dir():
        return {"action": "list", "modules": []}
    modules: list[dict[str, Any]] = []
    for path in sorted(base.iterdir(), key=lambda item: (item.name.casefold(), item.name)):
        if path.name.startswith(".") or path.name == "__pycache__" or not path.is_dir() or _is_link(path):
            continue
        try:
            result = _run_validate(root, path.name)
        except (ValueError, OSError) as exc:
            result = {
                "name": path.name,
                "valid": False,
                "errors": [str(exc)],
                "health": "异常",
                "recent_update": "",
                "data_md": "",
            }
        item = {
            "name": result["name"],
            "health": result["health"],
            "recent_update": result["recent_update"],
            "data_md": result["data_md"],
            "valid": result["valid"],
            "errors": result["errors"],
        }
        modules.append(item)
    return {"action": "list", "modules": modules}


def _write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content.rstrip())
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _run_create(
    root: Path,
    name: Any,
    explain: Any,
    sense_content: Any,
    data_update: Any,
) -> dict[str, Any]:
    module_name, base, module = _module_dir(root, name)
    explain_text = _required_text(explain, "explain", _MAX_EXPLAIN_CHARS)
    sense_body = _required_text(sense_content, "sense_content", _MAX_MARKDOWN_CHARS)
    if data_update is not None and not isinstance(data_update, str):
        raise ValueError("data_update 必须是字符串")
    update_source = (data_update or "").strip() or _DATA_UPDATE_TEMPLATE
    if len(update_source) > _MAX_CODE_CHARS:
        raise ValueError(f"data_update 超过最大长度 {_MAX_CODE_CHARS}")
    try:
        tree = ast.parse(update_source, filename="data_update.py", mode="exec")
        compile(tree, "data_update.py", "exec")
    except SyntaxError as exc:
        raise ValueError(f"data_update Python 代码无效：{exc}") from exc
    if _python_contains_sensitive_credential(update_source):
        raise ValueError("data_update 包含疑似硬编码敏感凭据，请改用环境变量读取")

    base.mkdir(parents=True, exist_ok=True)
    _reject_link_components(root, base)
    if module.exists() or _is_link(module):
        raise FileExistsError(f"感知模块已存在：{module_name}")
    manifest = {
        "name": module_name,
        "data_md": "sense.md",
        "recent_update": datetime.now(_BEIJING).strftime(_TIME_FORMAT),
        "health": "正常",
        "start_update": "data_update.py",
    }
    temporary = base / f".{module_name}.{uuid.uuid4().hex}.tmp"
    published = False
    try:
        temporary.mkdir()
        _write_text(temporary / "sense.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        _write_text(temporary / "sense.md", sense_body)
        _write_text(temporary / "data_update.py", update_source)
        if module.exists() or _is_link(module):
            raise FileExistsError(f"感知模块已存在：{module_name}")
        os.rename(temporary, module)
        published = True
        validation = _run_validate(root, module_name)
        if not validation["valid"]:
            raise ValueError("创建后的感知模块未通过运行时校验：" + "; ".join(validation["errors"]))
    except Exception:
        if published and module.is_dir() and not _is_link(module):
            shutil.rmtree(module, ignore_errors=True)
        raise
    finally:
        if temporary.is_dir() and not _is_link(temporary):
            shutil.rmtree(temporary, ignore_errors=True)
    return {
        "action": "create",
        "name": module_name,
        "explain": explain_text,
        "path": module.relative_to(root.resolve()).as_posix(),
        "files": list(_CREATE_FILES),
        "valid": True,
        "next_steps": [
            "在 data_update.py 的 collect_data() 中填充实际数据采集逻辑",
            "运行 data_update.py 初始化 sense.md 数据",
        ],
    }


def run(
    action: str,
    name: str = "",
    explain: str = "",
    sense_content: str = "",
    data_update: str = "",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in _ACTIONS:
        raise ValueError(f"未知 sense_creater action：{action}，可选: list / create / validate")
    if not isinstance(context, dict) or not context.get("root"):
        raise ValueError("工具上下文缺少 root")
    root = Path(str(context["root"])).resolve()
    if action == "list":
        return _run_list(root)
    if action == "validate":
        return _run_validate(root, name)
    return _run_create(root, name, explain, sense_content, data_update)
