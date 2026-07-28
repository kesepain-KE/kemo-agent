"""拓展、知识、技能与感知模块路由。"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import Response

from web.schemas import SkillToggleBody, TextBody
from web.service import SKILL_ARCHIVE_MAX_BYTES, WebRunService


def register_module_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/expand")
    async def expands(user: str) -> dict[str, Any]:
        return backend.expands(user)

    @app.post("/api/users/{user}/expand/{scope}/{module_name}/refresh")
    async def refresh_expand_module(
        user: str,
        scope: str,
        module_name: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.refresh_expand_module,
            user,
            scope,
            module_name,
        )

    @app.patch("/api/users/{user}/expand/{scope}/{module_name}/enabled")
    async def set_expand_module_enabled(
        user: str,
        scope: str,
        module_name: str,
        body: SkillToggleBody,
    ) -> dict[str, Any]:
        return backend.set_expand_module_enabled(
            user,
            scope,
            module_name,
            body.enabled,
        )

    @app.delete("/api/users/{user}/expand/{scope}/{module_name}")
    async def delete_expand_module(
        user: str,
        scope: str,
        module_name: str,
    ) -> dict[str, Any]:
        return backend.delete_expand_module(user, scope, module_name)

    @app.get("/api/users/{user}/knowledge")
    async def knowledge(user: str) -> dict[str, Any]:
        return backend.knowledge(user)

    @app.get("/api/users/{user}/knowledge/{scope}/document")
    async def knowledge_document(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.knowledge_document(user, scope, path)

    @app.put("/api/users/{user}/knowledge/{scope}/document")
    async def update_knowledge_document(
        user: str,
        scope: str,
        body: TextBody,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.put_knowledge_document(user, scope, path, body.content)

    @app.delete("/api/users/{user}/knowledge/{scope}/document")
    async def delete_knowledge_document(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.delete_knowledge_document(user, scope, path)

    @app.patch("/api/users/{user}/knowledge/{scope}/document")
    async def move_knowledge_document(
        user: str,
        scope: str,
        path: str = Query(...),
        new_path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.move_knowledge_document(user, scope, path, new_path)

    @app.get("/api/users/{user}/skills")
    async def skills(user: str) -> dict[str, Any]:
        return backend.skills(user)

    @app.post("/api/users/{user}/skills/user-created/upload")
    async def upload_user_skills(
        user: str,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        filename = file.filename or ""
        try:
            data = await file.read(SKILL_ARCHIVE_MAX_BYTES + 1)
        finally:
            await file.close()
        return await asyncio.to_thread(
            backend.upload_user_skills,
            user,
            filename,
            data,
        )

    @app.get("/api/users/{user}/skills/{category}/document")
    async def skill_document(
        user: str,
        category: str,
        name: str = Query(...),
    ) -> dict[str, Any]:
        return backend.skill_document(user, category, name)

    @app.put("/api/users/{user}/skills/{category}/document")
    async def update_skill_document(
        user: str,
        category: str,
        body: TextBody,
        name: str = Query(...),
    ) -> dict[str, Any]:
        return backend.put_skill_document(user, category, name, body.content)

    @app.delete("/api/users/{user}/skills/{category}")
    async def delete_skill(
        user: str,
        category: str,
        name: str = Query(...),
    ) -> dict[str, Any]:
        return backend.delete_skill(user, category, name)

    @app.patch("/api/users/{user}/skills/{category}/enabled")
    async def set_skill_enabled(
        user: str,
        category: str,
        body: SkillToggleBody,
        name: str = Query(...),
    ) -> dict[str, Any]:
        return backend.set_skill_enabled(user, category, name, body.enabled)

    @app.get("/api/users/{user}/skills/{category}/download")
    async def download_skill(
        user: str,
        category: str,
        name: str = Query(...),
    ) -> Response:
        filename, data = backend.skill_archive(user, category, name)
        return Response(
            content=data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            },
        )

    @app.get("/api/users/{user}/sense")
    async def sense(user: str) -> dict[str, Any]:
        return backend.sense(user)

    @app.post("/api/users/{user}/sense/{module_name}/refresh")
    async def refresh_sense_module(user: str, module_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(backend.refresh_sense_module, user, module_name)

    @app.patch("/api/users/{user}/sense/{module_name}/enabled")
    async def set_sense_module_enabled(
        user: str,
        module_name: str,
        body: SkillToggleBody,
    ) -> dict[str, Any]:
        return backend.set_sense_module_enabled(user, module_name, body.enabled)

    @app.delete("/api/users/{user}/sense/{module_name}")
    async def delete_sense_module(user: str, module_name: str) -> dict[str, Any]:
        return backend.delete_sense_module(user, module_name)

