"""任务计划与定时任务路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query

from web.service import WebRunService


def register_task_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/tasks")
    async def tasks(
        user: str,
        source: str = Query("web", max_length=32),
        session_id: str = Query("", max_length=256),
    ) -> dict[str, Any]:
        return backend.tasks(user, source=source, session_id=session_id)

    @app.post("/api/users/{user}/tasks/plans")
    async def create_plan(
        user: str,
        body: dict[str, Any],
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.create_plan(user, body, session_id, source)

    @app.put("/api/users/{user}/tasks/plans/{plan_id}")
    async def update_plan(
        user: str,
        plan_id: str,
        body: dict[str, Any],
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.update_plan(user, plan_id, body, session_id, source)

    @app.patch("/api/users/{user}/tasks/plans/{plan_id}/edit")
    async def edit_plan(
        user: str,
        plan_id: str,
        body: dict[str, Any],
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.edit_plan(user, plan_id, body, session_id, source)

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/steps/{step_id}/retry")
    async def retry_plan_step(
        user: str,
        plan_id: str,
        step_id: str,
        body: dict[str, Any],
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.retry_plan_step(user, plan_id, step_id, body, session_id, source)

    @app.get("/api/users/{user}/tasks/plans/{plan_id}/revisions")
    async def list_plan_revisions(
        user: str,
        plan_id: str,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.list_plan_revisions(user, plan_id, session_id, source)

    @app.get("/api/users/{user}/tasks/plans/{plan_id}/revisions/{revision}")
    async def get_plan_revision(
        user: str,
        plan_id: str,
        revision: int,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.get_plan_revision(user, plan_id, revision, session_id, source)

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/rollback")
    async def rollback_plan(
        user: str,
        plan_id: str,
        body: dict[str, Any],
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.rollback_plan(
            user,
            plan_id,
            body.get("revision"),
            body.get("current_revision"),
            session_id,
            source,
        )

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/actions/{action}")
    async def command_plan(
        user: str,
        plan_id: str,
        action: str,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.command_plan(user, plan_id, action, session_id, source)

    @app.delete("/api/users/{user}/tasks/plans/{plan_id}")
    async def delete_plan(
        user: str,
        plan_id: str,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.delete_plan(user, plan_id, session_id, source)

    @app.post("/api/users/{user}/tasks/crons")
    async def create_cron(user: str, body: dict[str, Any]) -> dict[str, Any]:
        return backend.create_cron(user, body)

    @app.put("/api/users/{user}/tasks/crons/{task_id}")
    async def update_cron(
        user: str,
        task_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return backend.update_cron(user, task_id, body)

    @app.delete("/api/users/{user}/tasks/crons/{task_id}")
    async def delete_cron(user: str, task_id: str) -> dict[str, Any]:
        return backend.delete_cron(user, task_id)
