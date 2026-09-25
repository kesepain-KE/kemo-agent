"""Declarative user-configuration panels for Expand and Sense modules."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Literal

from web.errors import InvalidRequestError
from web.services._io import atomic_write


PanelKind = Literal["expand", "sense"]

_PANEL_MAX_BYTES = 256 * 1024
_VALUES_MAX_BYTES = 128 * 1024
_MAX_CONTAINERS = 24
_MAX_FIELDS = 64
_MAX_CONTROLS = 24
_MAX_TEXT = 20_000
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_COMMAND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")
_INPUT_TYPES = {"string", "number", "boolean", "enum", "text"}
_STATUS_TYPES = {"text", "badge", "keyvalue", "markdown"}
_WIDTHS = {"quarter", "third", "half", "full"}
_HEIGHTS = {"h1", "h2"}


def _text(value: Any, field: str, *, required: bool = True, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise InvalidRequestError(f"{field} 必须是字符串")
    normalized = value.strip()
    if required and not normalized:
        raise InvalidRequestError(f"{field} 不能为空")
    if len(normalized) > maximum:
        raise InvalidRequestError(f"{field} 超过最大长度 {maximum}")
    return normalized


def _safe_panel_path(module_root: Path, value: Any, field: str) -> tuple[str, Path]:
    normalized = _text(value, field, maximum=500).replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or Path(normalized).is_absolute()
        or ".." in pure.parts
        or "\x00" in normalized
        or not pure.parts
        or ":" in pure.parts[0]
    ):
        raise InvalidRequestError(f"{field} 必须是模块目录内的相对路径")
    root = module_root.resolve()
    candidate = root.joinpath(*pure.parts)
    current = root
    for part in pure.parts:
        current = current / part
        if current.exists() and (
            current.is_symlink() or getattr(current, "is_junction", lambda: False)()
        ):
            raise InvalidRequestError(f"{field} 不允许经过符号链接或目录联接")
    try:
        candidate.resolve().relative_to(root)
    except ValueError:
        raise InvalidRequestError(f"{field} 越出模块目录") from None
    return pure.as_posix(), candidate


def _read_json_object(path: Path, *, maximum: int, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(missing or {})
    if not path.is_file() or path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise InvalidRequestError(f"面板文件不是安全的普通文件：{path.name}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InvalidRequestError(f"面板文件读取失败：{path.name}") from exc
    if len(data) > maximum:
        raise InvalidRequestError(f"面板文件超过 {maximum // 1024} KB：{path.name}")
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidRequestError(f"面板 JSON 无法解析：{path.name}") from exc
    if not isinstance(value, dict):
        raise InvalidRequestError(f"面板 JSON 顶层必须是对象：{path.name}")
    return value


def _normalize_options(value: Any, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > 100:
        raise InvalidRequestError(f"{field} 必须是 1～100 项数组")
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if isinstance(item, str):
            option_value = _text(item, f"{field}.{index}", maximum=240)
            label = option_value
        elif isinstance(item, dict):
            option_value = _text(item.get("value"), f"{field}.{index}.value", maximum=240)
            label = _text(item.get("label", option_value), f"{field}.{index}.label", maximum=240)
        else:
            raise InvalidRequestError(f"{field}.{index} 必须是字符串或选项对象")
        if option_value in seen:
            raise InvalidRequestError(f"{field} 不能包含重复值：{option_value}")
        seen.add(option_value)
        options.append({"value": option_value, "label": label})
    return options


def _validate_value(field: dict[str, Any], value: Any, path: str) -> Any:
    field_type = field["type"]
    if field_type in {"string", "text"}:
        if not isinstance(value, str):
            raise InvalidRequestError(f"{path} 必须是字符串")
        maximum = int(field.get("max_length") or _MAX_TEXT)
        if len(value) > maximum:
            raise InvalidRequestError(f"{path} 超过最大长度 {maximum}")
        if field.get("required") and not value.strip():
            raise InvalidRequestError(f"{path} 不能为空")
        return value
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise InvalidRequestError(f"{path} 必须是数字")
        if field.get("min") is not None and value < field["min"]:
            raise InvalidRequestError(f"{path} 不能小于 {field['min']}")
        if field.get("max") is not None and value > field["max"]:
            raise InvalidRequestError(f"{path} 不能大于 {field['max']}")
        return value
    if field_type == "boolean":
        if not isinstance(value, bool):
            raise InvalidRequestError(f"{path} 必须是布尔值")
        return value
    if field_type == "enum":
        if not isinstance(value, str):
            raise InvalidRequestError(f"{path} 必须是字符串枚举")
        allowed = {item["value"] for item in field["options"]}
        if value not in allowed:
            raise InvalidRequestError(f"{path} 不在允许的枚举选项内")
        return value
    raise InvalidRequestError(f"{path} 使用了不支持的字段类型")


def _normalize_field(raw: Any, path: str, *, status: bool = False) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise InvalidRequestError(f"{path} 必须是对象")
    key = _text(raw.get("key"), f"{path}.key", maximum=64)
    if not _KEY_RE.fullmatch(key):
        raise InvalidRequestError(f"{path}.key 格式无效")
    field_type = _text(raw.get("type"), f"{path}.type", maximum=40)
    allowed = _STATUS_TYPES if status else _INPUT_TYPES
    if field_type not in allowed:
        raise InvalidRequestError(f"{path}.type 不受支持：{field_type}")
    field: dict[str, Any] = {
        "key": key,
        "label": _text(raw.get("label", key), f"{path}.label", maximum=160),
        "type": field_type,
    }
    for name in ("placeholder", "description"):
        if raw.get(name) is not None:
            field[name] = _text(raw[name], f"{path}.{name}", required=False, maximum=500)
    if bool(raw.get("masked")):
        if not status and field_type not in {"string", "text"}:
            raise InvalidRequestError(f"{path}.masked 只允许用于 string 或 text 字段")
        field["masked"] = True
    if bool(raw.get("required")):
        field["required"] = True
    if raw.get("max_length") is not None:
        maximum = raw["max_length"]
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= _MAX_TEXT:
            raise InvalidRequestError(f"{path}.max_length 必须是 1～{_MAX_TEXT} 的整数")
        field["max_length"] = maximum
    if field_type == "number":
        for name in ("min", "max"):
            if raw.get(name) is not None:
                number = raw[name]
                if isinstance(number, bool) or not isinstance(number, (int, float)):
                    raise InvalidRequestError(f"{path}.{name} 必须是数字")
                field[name] = number
        if field.get("min") is not None and field.get("max") is not None and field["min"] > field["max"]:
            raise InvalidRequestError(f"{path}.min 不能大于 max")
    if field_type == "enum":
        field["options"] = _normalize_options(raw.get("options"), f"{path}.options")
    if raw.get("default") is not None and not status:
        field["default"] = _validate_value(field, raw["default"], f"{path}.default")
    return field


def _normalize_container(raw: Any, index: int, panel_kind: PanelKind) -> dict[str, Any]:
    path = f"containers.{index}"
    if not isinstance(raw, dict):
        raise InvalidRequestError(f"{path} 必须是对象")
    kind = _text(raw.get("kind"), f"{path}.kind", maximum=40)
    if kind not in {"status", "config", "action"}:
        raise InvalidRequestError(f"{path}.kind 只允许 status、config 或 action")
    width = str(raw.get("width") or "full")
    height = str(raw.get("height") or "h1")
    if width not in _WIDTHS or height not in _HEIGHTS:
        raise InvalidRequestError(f"{path} 使用了无效宽高档位")
    container: dict[str, Any] = {
        "kind": kind,
        "title": _text(raw.get("title", "用户配置"), f"{path}.title", maximum=200),
        "width": width,
        "height": height,
    }
    if kind in {"status", "config"}:
        location_key = "source" if kind == "status" else "values"
        container[location_key] = _text(raw.get(location_key), f"{path}.{location_key}", maximum=500).replace("\\", "/")
        fields = raw.get("fields", [])
        if not isinstance(fields, list) or len(fields) > _MAX_FIELDS:
            raise InvalidRequestError(f"{path}.fields 最多 {_MAX_FIELDS} 项")
        container["fields"] = [
            _normalize_field(field, f"{path}.fields.{field_index}", status=kind == "status")
            for field_index, field in enumerate(fields)
        ]
        keys = [field["key"] for field in container["fields"]]
        if len(set(keys)) != len(keys):
            raise InvalidRequestError(f"{path}.fields 不能包含重复 key")
        if kind == "config":
            presets = raw.get("presets", [])
            if not isinstance(presets, list) or len(presets) > 20:
                raise InvalidRequestError(f"{path}.presets 最多 20 项")
            field_map = {field["key"]: field for field in container["fields"]}
            normalized_presets = []
            for preset_index, preset in enumerate(presets):
                preset_path = f"{path}.presets.{preset_index}"
                if not isinstance(preset, dict) or not isinstance(preset.get("values"), dict):
                    raise InvalidRequestError(f"{preset_path} 必须包含 values 对象")
                unknown = set(preset["values"]) - set(field_map)
                if unknown:
                    raise InvalidRequestError(f"{preset_path} 包含未声明字段：{sorted(unknown)[0]}")
                normalized_presets.append({
                    "name": _text(preset.get("name"), f"{preset_path}.name", maximum=120),
                    "values": {
                        key: _validate_value(field_map[key], value, f"{preset_path}.values.{key}")
                        for key, value in preset["values"].items()
                    },
                })
            container["presets"] = normalized_presets
        return container

    controls = raw.get("controls", [])
    if not isinstance(controls, list) or len(controls) > _MAX_CONTROLS:
        raise InvalidRequestError(f"{path}.controls 最多 {_MAX_CONTROLS} 项")
    normalized_controls = []
    for control_index, control in enumerate(controls):
        control_path = f"{path}.controls.{control_index}"
        if not isinstance(control, dict):
            raise InvalidRequestError(f"{control_path} 必须是对象")
        control_type = _text(control.get("type"), f"{control_path}.type", maximum=40)
        if control_type not in ({"button", "send"} if panel_kind == "expand" else {"button"}):
            raise InvalidRequestError(f"{control_path}.type 不受支持")
        command = _text(control.get("command"), f"{control_path}.command", maximum=128)
        if not _COMMAND_RE.fullmatch(command):
            raise InvalidRequestError(f"{control_path}.command 格式无效")
        if panel_kind == "sense" and command != "refresh":
            raise InvalidRequestError("感知面板 action 只允许 refresh")
        normalized = {
            "type": control_type,
            "label": _text(control.get("label", command), f"{control_path}.label", maximum=120),
            "command": command,
        }
        inputs = control.get("inputs", [])
        if control_type == "send":
            if not isinstance(inputs, list) or not inputs or len(inputs) > 20:
                raise InvalidRequestError(f"{control_path}.inputs 必须是 1～20 项数组")
            normalized["inputs"] = [
                _normalize_field(item, f"{control_path}.inputs.{input_index}")
                for input_index, item in enumerate(inputs)
            ]
            input_keys = [field["key"] for field in normalized["inputs"]]
            if len(set(input_keys)) != len(input_keys):
                raise InvalidRequestError(f"{control_path}.inputs 不能包含重复 key")
        normalized_controls.append(normalized)
    container["controls"] = normalized_controls
    return container


def load_module_panel(module_root: Path, panel_kind: PanelKind) -> tuple[dict[str, Any] | None, str]:
    """Return a normalized declaration; parse errors stay local to the module."""

    panel_path = module_root / "module" / "panel.json"
    if not panel_path.exists():
        return None, ""
    try:
        _, safe_path = _safe_panel_path(module_root, "module/panel.json", "panel.json")
        raw = _read_json_object(safe_path, maximum=_PANEL_MAX_BYTES)
        if raw.get("schema_version") not in {1, "1"}:
            raise InvalidRequestError("panel.json.schema_version 只支持 1")
        containers = raw.get("containers")
        if not isinstance(containers, list) or len(containers) > _MAX_CONTAINERS:
            raise InvalidRequestError(f"panel.json.containers 必须是 0～{_MAX_CONTAINERS} 项数组")
        panel = {
            "schema_version": 1,
            "title": _text(raw.get("title", "用户配置"), "panel.json.title", maximum=240),
            "containers": [
                _normalize_container(container, index, panel_kind)
                for index, container in enumerate(containers)
            ],
        }
        config_keys: set[str] = set()
        action_commands: set[str] = set()
        for container in panel["containers"]:
            if container["kind"] == "config":
                _safe_panel_path(module_root, container["values"], "config.values")
                for field in container["fields"]:
                    if field["key"] in config_keys:
                        raise InvalidRequestError(f"配置字段 key 跨容器重复：{field['key']}")
                    config_keys.add(field["key"])
            elif container["kind"] == "status":
                _safe_panel_path(module_root, container["source"], "status.source")
            else:
                for control in container["controls"]:
                    if control["command"] in action_commands:
                        raise InvalidRequestError(f"action command 重复：{control['command']}")
                    action_commands.add(control["command"])
        return panel, ""
    except InvalidRequestError as exc:
        return None, str(exc)


def _public_config_values(container: dict[str, Any], module_root: Path) -> tuple[dict[str, Any], dict[str, bool], str]:
    _, path = _safe_panel_path(module_root, container["values"], "config.values")
    try:
        stored = _read_json_object(path, maximum=_VALUES_MAX_BYTES, missing={})
    except InvalidRequestError as exc:
        return {}, {}, str(exc)
    values: dict[str, Any] = {}
    secret_set: dict[str, bool] = {}
    for field in container["fields"]:
        key = field["key"]
        value = stored.get(key, _field_default(field))
        if field.get("masked"):
            secret_set[key] = key in stored and stored[key] is not None and stored[key] != ""
        else:
            try:
                values[key] = _validate_value(field, value, f"values.{key}")
            except InvalidRequestError:
                values[key] = _field_default(field)
    return values, secret_set, ""


def _field_default(field: dict[str, Any]) -> Any:
    if "default" in field:
        return field["default"]
    if field["type"] == "boolean":
        return False
    if field["type"] == "number":
        return field.get("min", 0)
    if field["type"] == "enum":
        options = field.get("options") or []
        return options[0]["value"] if options else ""
    return ""


def module_panel_response(module_root: Path, panel_kind: PanelKind) -> dict[str, Any]:
    panel, panel_error = load_module_panel(module_root, panel_kind)
    if panel is None:
        return {"panel": None, "panel_error": panel_error}
    rendered = {**panel, "containers": []}
    for container in panel["containers"]:
        current = dict(container)
        if container["kind"] == "status":
            _, path = _safe_panel_path(module_root, container["source"], "status.source")
            try:
                stored = _read_json_object(path, maximum=_VALUES_MAX_BYTES, missing={})
                current["data"] = {
                    field["key"]: stored.get(field["key"])
                    for field in container["fields"]
                    if not field.get("masked")
                }
                current["secret_set"] = {
                    field["key"]: (
                        field["key"] in stored
                        and stored[field["key"]] is not None
                        and stored[field["key"]] != ""
                    )
                    for field in container["fields"]
                    if field.get("masked")
                }
                current["data_error"] = ""
            except InvalidRequestError as exc:
                current["data"] = {}
                current["data_error"] = str(exc)
        elif container["kind"] == "config":
            values, secret_set, data_error = _public_config_values(container, module_root)
            current["current_values"] = values
            current["secret_set"] = secret_set
            current["data_error"] = data_error
        rendered["containers"].append(current)
    return {"panel": rendered, "panel_error": ""}


def save_module_panel_values(
    module_root: Path,
    panel_kind: PanelKind,
    values: Any,
    clear_secrets: Any = None,
) -> dict[str, Any]:
    panel, panel_error = load_module_panel(module_root, panel_kind)
    if panel is None:
        raise InvalidRequestError(panel_error or "当前模块没有用户配置面板")
    if not isinstance(values, dict):
        raise InvalidRequestError("values 必须是对象")
    clear = [] if clear_secrets is None else clear_secrets
    if not isinstance(clear, list) or any(not isinstance(item, str) for item in clear):
        raise InvalidRequestError("clear_secrets 必须是字符串数组")
    fields: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for container in panel["containers"]:
        if container["kind"] == "config":
            for field in container["fields"]:
                fields[field["key"]] = (field, container)
    unknown = set(values) - set(fields)
    if unknown:
        raise InvalidRequestError(f"values 包含未声明字段：{sorted(unknown)[0]}")
    clear_unknown = set(clear) - {key for key, (field, _) in fields.items() if field.get("masked")}
    if clear_unknown:
        raise InvalidRequestError(f"clear_secrets 包含非密钥字段：{sorted(clear_unknown)[0]}")
    grouped: dict[str, dict[str, Any]] = {}
    containers_by_path: dict[str, dict[str, Any]] = {}
    for key, raw_value in values.items():
        field, container = fields[key]
        grouped.setdefault(container["values"], {})[key] = _validate_value(field, raw_value, f"values.{key}")
        containers_by_path[container["values"]] = container
    for key in clear:
        _, container = fields[key]
        grouped.setdefault(container["values"], {})[key] = ""
        containers_by_path[container["values"]] = container
    for relative_path, updates in grouped.items():
        _, path = _safe_panel_path(module_root, relative_path, "config.values")
        stored = _read_json_object(path, maximum=_VALUES_MAX_BYTES, missing={})
        stored.update(updates)
        data = (json.dumps(stored, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if len(data) > _VALUES_MAX_BYTES:
            raise InvalidRequestError("panel.values.json 超过 128 KB")
        atomic_write(path, data)
    return module_panel_response(module_root, panel_kind)


def validate_expand_panel_action(panel: dict[str, Any], command: Any, params: Any) -> tuple[str, dict[str, Any]]:
    normalized_command = _text(command, "command", maximum=128)
    if not isinstance(params, dict):
        raise InvalidRequestError("params 必须是对象")
    control: dict[str, Any] | None = None
    for container in panel["containers"]:
        if container["kind"] != "action":
            continue
        control = next((item for item in container["controls"] if item["command"] == normalized_command), control)
    if control is None:
        raise InvalidRequestError("command 未在面板 action 容器中声明")
    inputs = {field["key"]: field for field in control.get("inputs", [])}
    unknown = set(params) - set(inputs)
    if unknown:
        raise InvalidRequestError(f"params 包含未声明字段：{sorted(unknown)[0]}")
    validated = {
        key: _validate_value(field, params[key], f"params.{key}")
        for key, field in inputs.items()
        if key in params
    }
    missing = [key for key, field in inputs.items() if field.get("required") and key not in validated]
    if missing:
        raise InvalidRequestError(f"params 缺少必填字段：{missing[0]}")
    return normalized_command, validated
