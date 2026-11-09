"""The single parser and validator for Sense and Expand module manifests."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, NoReturn, overload, Literal

import yaml

from run.infra import cached_read_text, invalidate_source_cache
from run.modules.errors import ModuleError, ModuleSpecError, Reason
from run.modules.spec import ModuleKind, ModuleSpec
from run.modules.paths import module_path


_MANIFEST = "module.yaml"
_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_FIELDS = {
    "kind",
    "name",
    "explain",
    "recent_update",
    "health",
    "update_script",
    "input_open",
    "input_file",
    "control_open",
    "control_script",
    "control_entry",
    "ui",
}
_REQUIRED = {
    "kind",
    "name",
    "explain",
    "health",
    "update_script",
    "input_open",
    "control_open",
}


class _UniqueKeyLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in result:
                raise yaml.constructor.ConstructorError(
                    "while constructing a module manifest",
                    node.start_mark,
                    "mapping keys must be unique strings",
                    key_node.start_mark,
                )
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _fail(reason: Reason, detail: str, field: str | None = None) -> NoReturn:
    raise ModuleSpecError(ModuleError(reason, field, detail))


@overload
def _module_file(module_dir: Path, field: str, value: Any, suffix: str, *, required: Literal[True]) -> Path: ...


@overload
def _module_file(module_dir: Path, field: str, value: Any, suffix: str, *, required: bool) -> Path | None: ...


def _module_file(
    module_dir: Path,
    field: str,
    value: Any,
    suffix: str,
    *,
    required: bool,
) -> Path | None:
    if value is None and not required:
        return None
    if value is None:
        _fail("missing_field", f"module.yaml 缺少字段：{field}", field)
    if not isinstance(value, str) or not value.strip():
        _fail("bad_type", f"{field} 必须是模块目录内的 {suffix} 文件名", field)
    name = value.strip()
    if (
        PurePosixPath(name).name != name
        or PureWindowsPath(name).name != name
        or "\x00" in name or ":" in name
    ):
        _fail("path_escape", f"{field} 不得包含目录或绝对路径", field)
    if Path(name).suffix.casefold() != suffix:
        _fail("bad_suffix", f"{field} 必须是 {suffix} 文件名", field)
    path = module_dir / name
    try:
        resolved = path.resolve()
        resolved.relative_to(module_dir.resolve())
    except (OSError, ValueError):
        _fail("path_escape", f"{field} 不得跳出模块目录", field)
    current = module_dir
    for part in Path(name).parts:
        current /= part
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            _fail("path_escape", f"{field} 不能经过符号链接或目录联接", field)
    if required and not resolved.is_file():
        _fail("file_missing", f"{field} 指向的文件不存在：{name}", field)
    return resolved


def read_manifest(
    module_dir: Path,
    *,
    kind_hint: ModuleKind,
    scope: str,
) -> ModuleSpec:
    path = module_dir / _MANIFEST
    if not path.is_file():
        _fail("missing_manifest", "module.yaml 缺失")
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        _fail("path_escape", "module.yaml 不能是符号链接或目录联接")
    try:
        raw = yaml.load(cached_read_text(path, "utf-8-sig"), Loader=_UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError):
        _fail("unreadable", "module.yaml 不可读或 YAML 无效")
    if not isinstance(raw, dict):
        _fail("not_object", "module.yaml 根节点必须是映射")

    if "kind" not in raw:
        _fail("missing_field", "module.yaml 缺少字段：kind", "kind")
    if not isinstance(raw["kind"], str):
        _fail("bad_type", "kind 必须是字符串", "kind")
    if raw["kind"] not in {"sense", "expand"}:
        _fail("bad_enum", "kind 必须是 sense 或 expand", "kind")
    if raw["kind"] != kind_hint:
        _fail("kind_mismatch", f"kind 必须与模块类型一致：{kind_hint}", "kind")
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        _fail("unknown_field", "module.yaml 包含未知字段：" + ", ".join(unknown))
    missing = sorted(_REQUIRED - set(raw))
    if missing:
        _fail("missing_field", "module.yaml 缺少字段：" + ", ".join(missing), missing[0])

    for field in ("name", "explain", "update_script"):
        if not isinstance(raw[field], str) or not raw[field].strip():
            _fail("bad_type", f"{field} 必须是非空字符串", field)
    for field in ("input_open", "control_open"):
        if type(raw[field]) is not bool:
            _fail("bad_type", f"{field} 必须是布尔值", field)
    health = raw["health"]
    if not isinstance(health, str):
        _fail("bad_type", "health 必须是字符串", "health")
    if health not in {"正常", "异常"}:
        _fail("bad_enum", "health 必须是“正常”或“异常”", "health")
    recent_update = raw.get("recent_update")
    if recent_update is not None:
        if not isinstance(recent_update, str):
            _fail("bad_type", "recent_update 必须是字符串或 null", "recent_update")
        try:
            datetime.strptime(recent_update, _TIME_FORMAT)
        except ValueError:
            _fail(
                "bad_type",
                f"recent_update 必须符合 {_TIME_FORMAT}",
                "recent_update",
            )

    input_open = raw["input_open"]
    control_open = raw["control_open"]
    if kind_hint == "sense" and control_open:
        _fail("bad_enum", "sense 模块的 control_open 必须为 false", "control_open")
    if kind_hint == "expand" and not (input_open or control_open):
        _fail("bad_enum", "expand 模块必须开启 input_open 或 control_open")

    update_script = _module_file(
        module_dir, "update_script", raw["update_script"], ".py", required=True
    )
    input_file = _module_file(
        module_dir,
        "input_file",
        raw.get("input_file"),
        ".md",
        required=input_open,
    )
    control_script = _module_file(
        module_dir,
        "control_script",
        raw.get("control_script"),
        ".py",
        required=control_open,
    )
    control_entry = _module_file(
        module_dir,
        "control_entry",
        raw.get("control_entry"),
        ".md",
        required=control_open,
    )
    ui = raw.get("ui")
    workspace = action_entry = None
    if ui is not None:
        if not isinstance(ui, dict) or set(ui) - {"workspace", "action_entry"} or "workspace" not in ui:
            _fail("bad_type", "ui 必须声明 workspace，可选 action_entry", "ui")
        try:
            workspace = module_path(module_dir, ui["workspace"], file=False)
            if workspace.suffix != ".json":
                raise ValueError("ui.workspace 必须是 JSON 文件")
            if "action_entry" in ui:
                if kind_hint != "sense":
                    raise ValueError("Expand UI 动作复用 control_script")
                action_entry = module_path(module_dir, ui["action_entry"])
                if action_entry.suffix != ".py":
                    raise ValueError("ui.action_entry 必须是 Python 文件")
        except (ValueError, OSError) as exc:
            _fail("path_escape", str(exc), "ui")
    return ModuleSpec(
        kind=kind_hint,
        key=f"sense:{module_dir.name}" if kind_hint == "sense" else f"{scope}:{module_dir.name}",
        scope=scope,
        name=raw["name"].strip(),
        explain=raw["explain"].strip(),
        health=health,
        recent_update=recent_update,
        module_dir=module_dir,
        update_script=update_script,
        input_open=input_open,
        input_file=input_file,
        control_open=control_open,
        control_script=control_script,
        control_entry=control_entry,
        ui_workspace=workspace,
        ui_action_entry=action_entry,
    )


def write_manifest(path: Path, payload: dict[str, Any]) -> bool:
    rendered = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=1_000_000,
    )
    try:
        if path.read_text("utf-8") == rendered:
            return False
    except (OSError, UnicodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        invalidate_source_cache(path)
        return True
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
