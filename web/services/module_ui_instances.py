"""Revocable browser instances and capability-limited static UI assets."""

from __future__ import annotations

import json
import mimetypes
import secrets
import time
from pathlib import Path
from typing import Any

from run.modules.paths import module_path
from web.errors import ConflictError, InvalidRequestError, NotFoundError
from web.services.module_panels import module_panel_response
from web.services.module_workspace import ModuleWorkspaceService, WorkspaceForbidden
from web.services.module_workspace_schema import ASSET_SUFFIXES

_TTL = 24 * 60 * 60
_MAX_INSTANCES = 256
_SDK = Path(__file__).resolve().parents[1] / "module_ui_bridge.js"


def open_instance(service: ModuleWorkspaceService, user: str, kind: str, scope: str, module: str, page_id: Any) -> dict[str, Any]:
    with service.lock:
        item, _ = service.describe(user, kind, scope, module)
        if item["ui_error"]:
            raise InvalidRequestError(item["ui_error"])
        page = next((p for p in item["pages"] if p["id"] == page_id and p["type"] == "web"), None)
        if not page:
            raise NotFoundError("独立网页不存在")
        grant = service._grants().get(service.grant_key(user, kind, scope, module), {})
        if page["permissions"] and grant.get("authorization_revision") != item["authorization_revision"]:
            raise WorkspaceForbidden("请先由实例管理员审查并授予插件 UI 权限")
        now = time.monotonic()
        for token, instance in list(service.instances.items()):
            if instance["expires"] <= now:
                del service.instances[token]
        if len(service.instances) >= _MAX_INSTANCES:
            raise ConflictError("打开的插件页面过多，请关闭不用的工作台")
        token = secrets.token_urlsafe(32)
        instance = {
            "user": user, "kind": kind, "scope": scope, "module": module, "page": page_id,
            "ui_revision": item["ui_revision"], "generation": grant.get("generation"),
            "enabled": item["enabled"], "expires": now + _TTL,
            "permissions": sorted(set(page["permissions"]) & set(item["granted_permissions"])),
            "entry": page["entry"][len("ui/dist/"):], "calls": [],
        }
        service.instances[token] = instance
        return {
            "nonce": token, "asset_url": f"/module-ui/{token}/{instance['entry']}",
            "ui_revision": item["ui_revision"], "permissions": instance["permissions"],
            "expires_in": _TTL,
        }


def current_instance(service: ModuleWorkspaceService, token: str) -> tuple[dict[str, Any], dict[str, Any], Any]:
    with service.lock:
        instance = service.instances.get(token)
        if instance is None or instance["expires"] <= time.monotonic():
            service.instances.pop(token, None)
            raise NotFoundError("插件页面已关闭或过期")
        try:
            item, spec = service.describe(instance["user"], instance["kind"], instance["scope"], instance["module"])
            grant = service._grants().get(service.grant_key(instance["user"], instance["kind"], instance["scope"], instance["module"]), {})
            valid = (
                not item["ui_error"] and instance["ui_revision"] == item["ui_revision"]
                and instance["generation"] == grant.get("generation")
                and instance["enabled"] == item["enabled"]
                and (not instance["permissions"] or grant.get("authorization_revision") == item["authorization_revision"])
            )
        except (NotFoundError, InvalidRequestError):
            service.instances.pop(token, None)
            raise
        if not valid:
            service.instances.pop(token, None)
            raise ConflictError("模块、UI 或授权已更新，当前页面实例已失效")
        return instance, item, spec


def static_asset(service: ModuleWorkspaceService, token: str, relative: str) -> tuple[bytes, str]:
    _, _, spec = current_instance(service, token)
    if relative == "__kemo__/bridge.js":
        return _SDK.read_bytes(), "text/javascript"
    try:
        path = module_path(spec.module_dir, "ui/dist/" + relative)
    except ValueError as exc:
        raise WorkspaceForbidden(str(exc)) from exc
    if path.suffix.lower() not in ASSET_SUFFIXES or any(part.startswith(".") for part in relative.split("/")):
        raise WorkspaceForbidden("此文件不是可公开的 UI 静态资源")
    media_type = {
        ".js": "text/javascript", ".mjs": "text/javascript",
        ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml",
    }.get(path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return path.read_bytes(), media_type


def invoke_bridge(service: ModuleWorkspaceService, token: str, body: dict[str, Any]) -> dict[str, Any]:
    instance, item, spec = current_instance(service, token)
    if len(json.dumps(body, ensure_ascii=False)) > 128 * 1024:
        raise InvalidRequestError("Bridge 请求超过 128 KiB")
    method = body.get("method")
    permissions = {
        "module.status.get": "module.status.read",
        "module.config.get": "module.config.read",
        "module.config.patch": "module.config.write",
        "module.action.invoke": "module.action.invoke",
        "module.events.subscribe": "module.status.read",
    }
    if method not in permissions:
        raise InvalidRequestError("未知 Bridge 方法")
    if permissions[method] not in instance["permissions"]:
        raise WorkspaceForbidden("当前插件页面未获此操作权限")
    params = body.get("params", {})
    if not isinstance(params, dict):
        raise InvalidRequestError("Bridge params 必须是对象")
    with service.lock:
        now = time.monotonic()
        calls = [t for t in instance["calls"] if now - t < 60]
        if len(calls) >= 120:
            raise WorkspaceForbidden("插件页面调用过于频繁")
        instance["calls"] = [*calls, now]
    panel = module_panel_response(spec.module_dir, service._kind(instance["kind"]))
    if method == "module.status.get":
        result = {
            "module": {key: item.get(key) for key in (
                "module", "kind", "scope", "title", "description", "health", "enabled",
                "valid", "recent_update", "injected_tokens", "active_for_main_agent",
            )},
            "status": [c for c in (panel.get("panel") or {}).get("containers", []) if c["kind"] == "status"],
            "collected_markdown": item.get("collected_markdown", ""),
            "injected_markdown": item.get("injected_markdown", ""),
            "runtime": item.get("runtime", {}),
        }
    elif method == "module.config.get":
        result = {
            **panel,
            "panel": {**panel["panel"], "containers": [c for c in panel["panel"]["containers"] if c["kind"] == "config"]} if panel["panel"] else None,
        }
    elif method == "module.config.patch":
        result = service.save_config(instance["user"], instance["kind"], instance["scope"], instance["module"], params)
    elif method == "module.action.invoke":
        result = service.action(instance["user"], instance["kind"], instance["scope"], instance["module"], params.get("action"), params.get("params", {}))
    else:
        result = {"subscribed": True, "interval_ms": 5000}
    # A response to an already-dispatched operation never revalidates/retries it:
    # invalidation can revoke future calls, not undo a completed side effect.
    return {"result": result}
