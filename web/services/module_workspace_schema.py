"""Validated UI declarations attached to existing Sense/Expand/message modules."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, SchemaError

from run.modules.paths import module_path
from run.modules.spec import ModuleSpec
from web.errors import InvalidRequestError
from web.services.module_panels import load_module_panel
from web.services.message_workspace import MessageWorkspaceSpec

PERMISSIONS = frozenset({
    "module.status.read", "module.config.read", "module.config.write",
    "module.action.invoke",
})
ASSET_SUFFIXES = frozenset({
    ".html", ".js", ".mjs", ".css", ".json", ".svg", ".png", ".jpg",
    ".jpeg", ".webp", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".map",
})
_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_ASSET_FILES = 2048


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read_object(path: Path, maximum: int = 128 * 1024) -> dict[str, Any]:
    if path.stat().st_size > maximum:
        raise InvalidRequestError(f"{path.name} 文件过大")
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise InvalidRequestError(f"{path.name} 不是有效 JSON") from exc
    if not isinstance(value, dict):
        raise InvalidRequestError(f"{path.name} 必须是对象")
    return value


def asset_files(root: Path) -> dict[str, Path]:
    directory = module_path(root, "ui/dist", file=False)
    result: dict[str, Path] = {}
    total = 0
    for path in directory.rglob("*"):
        relative = path.relative_to(root).as_posix()
        safe = module_path(root, relative, file=False)
        if safe.is_dir():
            continue
        if any(part.startswith(".") for part in path.relative_to(directory).parts):
            raise InvalidRequestError("UI 静态包不能包含隐藏文件")
        if safe.suffix.lower() not in ASSET_SUFFIXES:
            raise InvalidRequestError(f"UI 静态包包含不支持的文件：{path.name}")
        total += safe.stat().st_size
        if total > MAX_ASSET_BYTES or len(result) >= MAX_ASSET_FILES:
            raise InvalidRequestError("UI 静态包超过 32 MiB 或 2048 文件")
        result[path.relative_to(directory).as_posix()] = safe
    return result


def _id(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise InvalidRequestError(f"{field} 必须是 1～80 位字母、数字、下划线或短横线")
    return value


def _label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise InvalidRequestError("页面和动作 label 必须是 1～160 字符")
    return value.strip()


def _no_references(value: Any) -> None:
    if isinstance(value, dict):
        if {"$ref", "$dynamicRef"} & value.keys():
            raise InvalidRequestError("动作 schema 不支持外部或动态引用")
        for item in value.values():
            _no_references(item)
    elif isinstance(value, list):
        for item in value:
            _no_references(item)


def workspace_declaration(spec: ModuleSpec | MessageWorkspaceSpec) -> dict[str, Any]:
    root = spec.module_dir
    panel, panel_error = load_module_panel(root, spec.kind)
    pages: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    assets: dict[str, Path] = {}
    raw: dict[str, Any] = {}
    if spec.ui_workspace is not None:
        path = module_path(root, spec.ui_workspace.relative_to(root).as_posix())
        raw = read_object(path)
        if raw.get("schema_version") != 1 or set(raw) - {"schema_version", "default_page", "pages", "actions"}:
            raise InvalidRequestError("workspace.json schema_version 必须为 1，且不能包含未知字段")
        if not isinstance(raw.get("pages"), list) or not 1 <= len(raw["pages"]) <= 32:
            raise InvalidRequestError("workspace.pages 必须包含 1～32 个页面")
        for page in raw["pages"]:
            if not isinstance(page, dict):
                raise InvalidRequestError("workspace.pages 每项必须是对象")
            page_id = _id(page.get("id"), "page.id")
            if any(item["id"] == page_id for item in pages):
                raise InvalidRequestError("页面 ID 重复")
            mode = page.get("type")
            allowed = {"id", "label", "type"}
            current = {"id": page_id, "label": _label(page.get("label")), "type": mode}
            if mode == "builtin":
                allowed.add("view")
                if page.get("view") not in {"overview", "collection", "injection", "docs", "logs"}:
                    raise InvalidRequestError("未知 builtin view")
                current["view"] = page["view"]
            elif mode == "panel":
                if panel is None and not panel_error:
                    raise InvalidRequestError("panel 页面需要 module/panel.json")
            elif mode == "markdown":
                allowed.add("source")
                source = page.get("source")
                path = module_path(root, source)
                if path.suffix.lower() != ".md" or path.stat().st_size > 512 * 1024:
                    raise InvalidRequestError("markdown 页面必须是模块内不超过 512 KiB 的 .md 文件")
                current["source"] = source
            elif mode == "web":
                allowed |= {"entry", "permissions"}
                entry = page.get("entry")
                path = module_path(root, entry)
                if not isinstance(entry, str) or not entry.startswith("ui/dist/") or path.suffix.lower() != ".html":
                    raise InvalidRequestError("web.entry 必须是 ui/dist 内的 HTML 文件")
                permissions = page.get("permissions", [])
                if not isinstance(permissions, list) or any(not isinstance(p, str) or p not in PERMISSIONS for p in permissions):
                    raise InvalidRequestError("页面请求了未知权限")
                current.update(entry=entry, permissions=sorted(set(permissions)))
            else:
                raise InvalidRequestError("页面 type 只支持 builtin、panel、markdown、web")
            if set(page) - allowed:
                raise InvalidRequestError(f"页面 {page_id} 包含未知字段")
            pages.append(current)
        declared_actions = raw.get("actions", [])
        if not isinstance(declared_actions, list) or len(declared_actions) > 64:
            raise InvalidRequestError("workspace.actions 必须是最多 64 项的数组")
        for action in declared_actions:
            if not isinstance(action, dict) or set(action) - {"id", "label", "input_schema", "permission", "handler"}:
                raise InvalidRequestError("动作声明包含未知字段")
            action_id = _id(action.get("id"), "action.id")
            if any(item["id"] == action_id for item in actions):
                raise InvalidRequestError("动作 ID 重复")
            handler = _id(action.get("handler"), "action.handler")
            if action.get("permission", "module.action.invoke") != "module.action.invoke":
                raise InvalidRequestError("动作 permission 必须是 module.action.invoke")
            schema = action.get("input_schema", {"type": "object", "additionalProperties": False})
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise InvalidRequestError("动作输入 schema 必须是 object")
            _no_references(schema)
            schema = {**schema, "additionalProperties": False}
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as exc:
                raise InvalidRequestError(f"动作 {action_id} schema 无效") from exc
            if spec.kind in {"sense", "message"} and handler != "refresh" and spec.ui_action_entry is None:
                raise InvalidRequestError("自定义 UI 动作需要 ui.action_entry")
            actions.append({
                "id": action_id, "label": _label(action.get("label")),
                "input_schema": schema, "handler": handler,
                "permission": "module.action.invoke",
            })
        if any(page["type"] == "web" for page in pages):
            assets = asset_files(root)
    defaults = [{"id": "overview", "label": "总览", "type": "builtin", "view": "overview"}]
    if spec.input_open:
        defaults.extend([
            {"id": "collection", "label": "采集数据", "type": "builtin", "view": "collection"},
            {"id": "injection", "label": "注入数据", "type": "builtin", "view": "injection"},
        ])
    if panel is not None or panel_error:
        defaults.append({"id": "panel", "label": "通用配置", "type": "panel"})
    if spec.control_entry is not None or (root / "README.md").is_file():
        defaults.append({"id": "docs", "label": "文档", "type": "builtin", "view": "docs"})
    if (root / "_runtime.json").is_file():
        defaults.append({"id": "logs", "label": "运行记录", "type": "builtin", "view": "logs"})
    for page in defaults:
        if not any(item["id"] == page["id"] for item in pages):
            pages.append(page)
    default = raw.get("default_page", pages[0]["id"])
    if not any(page["id"] == default for page in pages):
        raise InvalidRequestError("default_page 指向不存在的页面")
    asset_hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in assets.items()}
    authorization = {
        "pages": [{k: p[k] for k in ("id", "permissions") if k in p} for p in pages if p["type"] == "web"],
        "actions": actions,
        "entry": str(spec.ui_action_entry or spec.control_script or ""),
    }
    return {
        "pages": pages, "actions": actions, "default_page": default,
        "ui_revision": fingerprint({"declaration": raw, "assets": asset_hashes, "panel": panel}),
        "authorization_revision": fingerprint(authorization),
        "requested_permissions": sorted({p for page in pages for p in page.get("permissions", [])}),
    }
