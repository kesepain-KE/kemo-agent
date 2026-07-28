"""用户、人格、子代理与外部消息路由。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, Response

from web.schemas import SoulBody
from web.service import WebRunService


def register_identity_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users")
    async def users() -> dict[str, Any]:
        return {"users": backend.users()}

    @app.get("/api/users/{user}/agents")
    async def agents(user: str) -> dict[str, Any]:
        return backend.agents(user)

    @app.delete("/api/users/{user}/agents/{agent}")
    async def delete_user_agent(user: str, agent: str) -> dict[str, Any]:
        return backend.delete_user_agent(user, agent)

    @app.get("/api/users/{user}/message/status")
    async def message_status(user: str) -> dict[str, Any]:
        return backend.message_status(user)

    @app.post("/api/users/{user}/message/modules/{module_name}/check")
    async def check_message_module(user: str, module_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(backend.check_message_module, user, module_name)

    @app.delete("/api/users/{user}/message/modules/{module_name}")
    async def delete_message_module(user: str, module_name: str) -> dict[str, Any]:
        return backend.delete_message_module(user, module_name)

    @app.get("/api/users/{user}/soul")
    async def user_soul(user: str) -> dict[str, Any]:
        return backend.user_soul(user)

    @app.put("/api/users/{user}/soul")
    async def update_user_soul(user: str, body: SoulBody) -> dict[str, Any]:
        return backend.update_user_soul(user, body.content)

    @app.get("/api/global-soul")
    async def global_soul() -> dict[str, Any]:
        return backend.global_soul()

    @app.put("/api/global-soul")
    async def update_global_soul(body: SoulBody) -> dict[str, Any]:
        return backend.update_global_soul(body.content)

    @app.get("/api/logo")
    async def logo() -> Response:
        target = backend.logo()
        if target is None:
            return Response(status_code=204)
        return FileResponse(target, media_type="image/jpeg")

