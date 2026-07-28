"""配置、版本、Prompt 与记忆管理路由。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query
from fastapi.responses import Response

from web.auth import WebAuthConfig
from web.schemas import MemoryWriteBody, PreferencesBody, TextBody
from web.service import WebRunService


def register_setting_routes(
    app: FastAPI,
    backend: WebRunService,
    configured_auth: WebAuthConfig,
) -> None:
    @app.get("/api/users/{user}/settings")
    async def settings(user: str) -> dict[str, Any]:
        value = backend.settings(user)
        return {**value, "authentication": configured_auth.public_summary()}

    @app.get("/api/version")
    async def version_info() -> dict[str, Any]:
        return backend.version_info()

    @app.get("/api/version/check")
    async def version_check(refresh: bool = Query(False)) -> dict[str, Any]:
        return await asyncio.to_thread(backend.version_check, refresh=refresh)

    @app.get("/api/users/{user}/prompt/sections")
    async def prompt_sections(user: str) -> dict[str, Any]:
        return backend.prompt_sections(user)

    @app.get("/api/users/{user}/memory/summary")
    async def memory_summary(user: str) -> dict[str, Any]:
        return backend.memory_summary(user)

    @app.get("/api/users/{user}/memory/item")
    async def memory_item(
        user: str,
        tier: str = Query(...),
        filename: str = Query(...),
    ) -> dict[str, Any]:
        return backend.memory_item(user, tier, filename)

    @app.put("/api/users/{user}/memory/item")
    async def update_memory(
        user: str,
        body: MemoryWriteBody,
        filename: str = Query(...),
    ) -> dict[str, Any]:
        return backend.put_memory(user, filename, body.content, body.tier)

    @app.delete("/api/users/{user}/memory/item")
    async def delete_memory(
        user: str,
        tier: str = Query(...),
        filename: str = Query(...),
    ) -> dict[str, Any]:
        return backend.delete_memory(user, tier, filename)

    @app.get("/api/users/{user}/memory/important")
    async def important_memory(user: str) -> dict[str, Any]:
        return backend.important_memory(user)

    @app.put("/api/users/{user}/memory/important")
    async def update_important_memory(user: str, body: TextBody) -> dict[str, Any]:
        return backend.update_important_memory(user, body.content)

    @app.get("/api/users/{user}/config/full")
    async def full_config(user: str) -> dict[str, Any]:
        return backend.user_config(user)

    @app.patch("/api/users/{user}/config")
    async def patch_user_config(user: str, body: dict[str, Any]) -> dict[str, Any]:
        return backend.patch_user_config(user, body.get("changes", body))

    @app.get("/api/users/{user}/provider/models")
    async def kemo_provider_models(
        user: str,
        response: Response,
        refresh: bool = Query(False),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(
            backend.kemo_provider_models,
            user,
            refresh=refresh,
        )

    @app.get("/api/users/{user}/provider/model-capabilities")
    async def kemo_provider_model_capabilities(
        user: str,
        response: Response,
        model: str = Query(...),
        refresh: bool = Query(False),
    ) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(
            backend.kemo_provider_model_capabilities,
            user,
            model,
            refresh=refresh,
        )

    @app.get("/api/global-config")
    async def global_config() -> dict[str, Any]:
        return backend.global_config()

    @app.patch("/api/global-config")
    async def patch_global_config(body: dict[str, Any]) -> dict[str, Any]:
        return backend.patch_global_config(body.get("changes", body))

    @app.get("/api/users/{user}/preferences")
    async def preferences(user: str) -> dict[str, Any]:
        return backend.preferences(user)

    @app.patch("/api/users/{user}/preferences")
    async def patch_preferences(
        user: str,
        body: PreferencesBody,
    ) -> dict[str, Any]:
        return backend.patch_preferences(user, body.model_dump(exclude_none=True))
