"""Workspaces reuse the module inventory, panel configuration and execution chain."""

from __future__ import annotations

import json
import secrets
import sys
import threading
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jsonschema import Draft202012Validator, ValidationError

from run.config import load_config
from run.extensions import (
    EXPAND_CALL_RESULT_PREFIX, EXPAND_CALL_RUNNER, invoke_expand,
    module_execution_lock, module_update_timeout, read_expand_runtime, run_protocol_process,
)
from run.modules import locate_modules
from run.modules.paths import module_path
from run.modules.spec import ModuleKind, ModuleSpec
from web.errors import ConflictError, InvalidRequestError, NotFoundError, WebServiceError
from web.services._io import atomic_write
from web.services.module_panels import module_panel_response, save_module_panel_values
from web.services.module_workspace_schema import fingerprint, workspace_declaration

if TYPE_CHECKING:
    from web.service import WebRunService


class WorkspaceForbidden(WebServiceError):
    code = "forbidden"
    status = 403


class ModuleWorkspaceService:
    def __init__(self, backend: WebRunService) -> None:
        self.backend = backend
        self.root = backend.root
        self.lock = threading.RLock()
        self.instances: dict[str, dict[str, Any]] = {}
        self.imports: dict[str, dict[str, Any]] = {}
        self.grant_path = self.root / "config" / "module_ui_grants.json"

    def _kind(self, kind: str) -> ModuleKind:
        if kind not in {"expand", "sense"}:
            raise InvalidRequestError("模块类型只支持 expand 或 sense")
        return kind  # type: ignore[return-value]

    def _identity(self, user: str, kind: str, scope: str, module: str) -> tuple[str, Path]:
        self._kind(kind)
        if kind == "sense":
            if scope != "global":
                raise InvalidRequestError("Sense 仅使用现有全局作用域")
            name, _, path = self.backend._sense_module_directory(user, module)
        else:
            name, _, _, path = self.backend._expand_module_directory(user, scope, module)
        return name, path

    def grant_key(self, user: str, kind: str, scope: str, module: str) -> str:
        return f"{kind}:{scope}:{module}" + (f":{user}" if scope == "user" else "")

    def _grants(self) -> dict[str, Any]:
        if not self.grant_path.exists():
            return {}
        module_path(self.root, "config/module_ui_grants.json")
        try:
            data = json.loads(self.grant_path.read_text("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise InvalidRequestError("插件 UI 授权文件无效，需要管理员修复") from exc
        if not isinstance(data, dict):
            raise InvalidRequestError("插件 UI 授权文件必须是对象")
        return data

    def inventory(self, user: str, kind: str) -> dict[str, Any]:
        with self.lock:
            return self._inventory(user, kind)

    def _inventory(self, user: str, kind: str) -> dict[str, Any]:
        name = self.backend.require_user(user)
        typed_kind = self._kind(kind)
        original = self.backend.expands(name) if kind == "expand" else self.backend.sense(name)
        specs = {spec.key: spec for spec in locate_modules(self.root, name, kind=typed_kind).specs}
        if kind == "expand":
            rows = [dict(item, scope=group["scope"]) for group in original["expands"] for item in group["items"]]
        else:
            rows = [dict(item, scope="global") for item in original["sources"]]
        items = []
        grants = self._grants()
        for row in rows:
            module = row["name"]
            scope = row["scope"]
            key = f"{scope}:{module}" if kind == "expand" else f"sense:{module}"
            item = {
                **row, "key": key, "module": module, "kind": kind,
                "title": row["display_name"],
                "health": row.get("health", row.get("input_health", "异常")),
                "enabled": bool(row.get("whitelisted")),
                "ui_error": "", "pages": [], "actions": [],
                "ui_revision": "", "authorization_revision": "",
                "requested_permissions": [], "granted_permissions": [],
                "permission_revision": "",
            }
            spec = specs.get(key)
            if spec is not None:
                item["description"] = spec.explain
                if kind == "sense":
                    item["runtime"] = read_expand_runtime(spec.module_dir)
                try:
                    declaration = workspace_declaration(spec)
                    item.update(declaration)
                    grant = grants.get(self.grant_key(name, kind, scope, module), {})
                    if grant.get("authorization_revision") == item["authorization_revision"]:
                        item["granted_permissions"] = grant.get("permissions", [])
                        item["permission_revision"] = grant.get("generation", "")
                except (ValueError, OSError, InvalidRequestError) as exc:
                    item["ui_error"] = str(exc)
                    item["pages"] = [
                        {"id": "overview", "label": "总览", "type": "builtin", "view": "overview"},
                        *([{"id": "panel", "label": "通用配置", "type": "panel"}] if row.get("panel") or row.get("panel_error") else []),
                    ]
                    item["default_page"] = "overview"
            if not item["pages"]:
                item["pages"] = [{"id": "overview", "label": "诊断", "type": "builtin", "view": "overview"}]
                item["default_page"] = "overview"
            items.append(item)
        return {
            "user": name, "kind": kind, "items": items,
            "injection": original.get("injection", {}),
            "summary": {
                "total": len(items),
                "enabled": sum(item["enabled"] for item in items),
                "invalid": sum(not item["valid"] or bool(item["ui_error"]) for item in items),
                "injected_tokens": sum(item.get("injected_tokens", 0) for item in items),
            },
            "registry_revision": fingerprint([
                [item["key"], item["ui_revision"], item["enabled"], item["granted_permissions"], item["ui_error"]]
                for item in items
            ]),
        }

    def describe(self, user: str, kind: str, scope: str, module: str) -> tuple[dict[str, Any], ModuleSpec]:
        name, _ = self._identity(user, kind, scope, module)
        item = next((row for row in self.inventory(name, kind)["items"] if row["module"] == module and row["scope"] == scope), None)
        if item is None:
            raise NotFoundError("模块未注册")
        specs = locate_modules(self.root, name, kind=self._kind(kind)).specs
        spec = next((s for s in specs if s.module_dir.name == module and s.scope == scope), None)
        if spec is None:
            raise InvalidRequestError(item["error"] or "模块清单无效")
        return item, spec

    def panel(self, user: str, kind: str, scope: str, module: str) -> dict[str, Any]:
        name, path = self._identity(user, kind, scope, module)
        return {"user": name, "module": module, "scope": scope, **module_panel_response(path, self._kind(kind))}

    def save_config(self, user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        name, path = self._identity(user, kind, scope, module)
        revision = body.get("config_revision")
        if not isinstance(revision, str) or not revision:
            raise InvalidRequestError("保存配置必须提供 config_revision")
        saved = save_module_panel_values(
            path, self._kind(kind), body.get("values"), body.get("clear_secrets"), revision,
        )
        try:
            self.refresh(name, kind, scope, module)
        except WebServiceError as exc:
            raise WebServiceError(f"配置已保存，但采集刷新失败：{exc}") from exc
        return {"user": name, "scope": scope, "module": module, "saved": True, **saved}

    def refresh(self, user: str, kind: str, scope: str, module: str) -> dict[str, Any]:
        if kind == "expand":
            return self.backend.refresh_expand_module(user, scope, module)
        return self.backend.refresh_sense_module(user, module)

    def set_permissions(self, user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            item, _ = self.describe(user, kind, scope, module)
            if body.get("authorization_revision") != item["authorization_revision"]:
                raise ConflictError("插件声明已更新，请重新审查权限")
            permissions = body.get("permissions")
            if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
                raise InvalidRequestError("permissions 必须是字符串数组")
            if set(permissions) - set(item["requested_permissions"]):
                raise InvalidRequestError("不能授予插件未声明的权限")
            grants = self._grants()
            grants[self.grant_key(user, kind, scope, module)] = {
                "authorization_revision": item["authorization_revision"],
                "permissions": sorted(set(permissions)), "generation": secrets.token_urlsafe(16),
            }
            module_path(self.root, "config/module_ui_grants.json", file=False)
            atomic_write(self.grant_path, (json.dumps(grants, ensure_ascii=False, indent=2) + "\n").encode())
            self.invalidate(user, kind, scope, module)
            return {"granted_permissions": sorted(set(permissions))}

    def invalidate(self, user: str, kind: str, scope: str, module: str, *, all_users: bool = True) -> None:
        with self.lock:
            for token, instance in list(self.instances.items()):
                if instance["kind"] == kind and instance["scope"] == scope and instance["module"] == module and ((all_users and scope != "user") or instance["user"] == user):
                    del self.instances[token]

    def document(self, user: str, kind: str, scope: str, module: str, page_id: str) -> dict[str, str]:
        item, spec = self.describe(user, kind, scope, module)
        page = next((p for p in item["pages"] if p["id"] == page_id), None)
        if not page:
            raise NotFoundError("页面不存在")
        if page["type"] == "markdown":
            path = module_path(spec.module_dir, page["source"])
        elif page.get("view") == "docs":
            path = spec.control_entry or module_path(spec.module_dir, "README.md")
        else:
            raise InvalidRequestError("当前页面不是文档")
        if path.stat().st_size > 512 * 1024:
            raise InvalidRequestError("文档超过 512 KiB")
        return {"content": path.read_text("utf-8-sig")}

    def action(self, user: str, kind: str, scope: str, module: str, action_id: Any, params: Any) -> Any:
        item, spec = self.describe(user, kind, scope, module)
        if not item["enabled"]:
            raise WorkspaceForbidden("当前模块已停用，不能执行动作")
        action = next((a for a in item["actions"] if a["id"] == action_id), None)
        if action is None:
            raise InvalidRequestError("动作未在 workspace.json 中声明")
        try:
            Draft202012Validator(action["input_schema"]).validate(params)
        except ValidationError as exc:
            raise InvalidRequestError("动作参数不符合 input_schema") from exc
        if action["handler"] == "refresh":
            return self.refresh(user, kind, scope, module)
        timeout = module_update_timeout(load_config(user, self.root))
        if kind == "expand":
            return invoke_expand(
                root=self.root, user=user, scope=scope, module=module,
                command=action["handler"], params=params, timeout=timeout,
            )
        if spec.ui_action_entry is None:
            raise InvalidRequestError("Sense 未声明 UI 动作入口")
        request = json.dumps({"command": action["handler"], "params": params, "context": {
            "user": user, "scope": scope, "module": module, "source": "module_ui",
        }}, ensure_ascii=False)
        with module_execution_lock(spec.module_dir, timeout=timeout):
            code, payload, _, _ = run_protocol_process(
                [sys.executable, "-I", "-c", EXPAND_CALL_RUNNER, str(spec.ui_action_entry), str(spec.module_dir)],
                cwd=spec.module_dir, timeout=timeout, result_prefix=EXPAND_CALL_RESULT_PREFIX,
                stdin_payload=request,
            )
        if code or not payload or payload.get("ok") is not True:
            raise WebServiceError("Sense UI 动作执行失败：" + str((payload or {}).get("reason", "无有效回执")))
        result = payload.get("result")
        if isinstance(result, dict) and (result.get("ok") is False or result.get("status") in {"error", "failed", "failure"}):
            raise WebServiceError("Sense UI 动作返回失败：" + str(result.get("error") or result.get("message") or "无错误详情"))
        return result
