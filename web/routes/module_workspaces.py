"""Module workbench APIs and isolated, revocable static browser namespaces."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import Response

from web.service import WebRunService
from web.services.module_import import ARCHIVE_MAX_BYTES, install_import, preview_import
from web.services.module_ui_instances import invoke_bridge, open_instance, static_asset
from web.services.module_workspace import ModuleWorkspaceService


def register_module_workspace_routes(app: FastAPI, backend: WebRunService) -> None:
    app.state.module_workspaces = None

    def service() -> ModuleWorkspaceService:
        if app.state.module_workspaces is None:
            app.state.module_workspaces = ModuleWorkspaceService(backend)
        return app.state.module_workspaces

    @app.get("/api/users/{user}/module-workspaces/{kind}")
    async def inventory(user: str, kind: str) -> dict[str, Any]:
        return await asyncio.to_thread(service().inventory, user, kind)

    @app.get("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/panel")
    async def panel(user: str, kind: str, scope: str, module: str) -> dict[str, Any]:
        return await asyncio.to_thread(service().panel, user, kind, scope, module)

    @app.put("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/panel")
    async def save_panel(user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(service().save_config, user, kind, scope, module, body)

    @app.get("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/document/{page}")
    async def document(user: str, kind: str, scope: str, module: str, page: str) -> dict[str, str]:
        return await asyncio.to_thread(service().document, user, kind, scope, module, page)

    @app.put("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/permissions")
    async def permissions(user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(service().set_permissions, user, kind, scope, module, body)

    @app.post("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/instances")
    async def create_instance(user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(open_instance, service(), user, kind, scope, module, body.get("page"))

    @app.post("/api/module-ui/instances/{token}/rpc")
    async def bridge(token: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(invoke_bridge, service(), token, body)

    @app.delete("/api/module-ui/instances/{token}")
    async def close_instance(token: str) -> dict[str, bool]:
        with service().lock:
            service().instances.pop(token, None)
        return {"closed": True}

    @app.post("/api/users/{user}/module-workspaces/{kind}/import/preview")
    async def preview(user: str, kind: str, scope: str, file: UploadFile = File(...)) -> dict[str, Any]:
        try:
            data = await file.read(ARCHIVE_MAX_BYTES + 1)
        finally:
            await file.close()
        return await asyncio.to_thread(preview_import, service(), user, kind, scope, file.filename or "", data)

    @app.post("/api/users/{user}/module-workspaces/install/{import_id}")
    async def install(user: str, import_id: str) -> dict[str, Any]:
        return await asyncio.to_thread(install_import, service(), user, import_id)

    @app.post("/api/users/{user}/module-workspaces/{kind}/{scope}/{module}/action")
    async def action(user: str, kind: str, scope: str, module: str, body: dict[str, Any]) -> dict[str, Any]:
        return {"result": await asyncio.to_thread(service().action, user, kind, scope, module, body.get("action"), body.get("params", {}))}

    @app.get("/module-ui/{token}/{path:path}")
    async def asset(request: Request, token: str, path: str) -> Response:
        data, media_type = await asyncio.to_thread(static_asset, service(), token, path)
        prefix = f"{str(request.base_url).rstrip('/')}/module-ui/{token}/"
        return Response(content=data, media_type=media_type, headers={
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"sandbox allow-scripts; default-src 'none'; script-src {prefix} 'unsafe-inline'; "
                f"style-src {prefix} 'unsafe-inline'; img-src {prefix} data: blob:; "
                f"font-src {prefix} data:; connect-src 'none'; frame-src 'none'; "
                "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
            ),
        })
