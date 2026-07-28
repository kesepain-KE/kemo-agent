"""任务计划与定时任务路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from web.service import WebRunService


def register_task_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/tasks")
    async def tasks(user: str) -> dict[str, Any]:
        return backend.tasks(user)

    @app.post("/api/users/{user}/tasks/plans")
    async def create_plan(user: str, body: dict[str, Any]) -> dict[str, Any]:
        return backend.create_plan(user, body)

    @app.put("/api/users/{user}/tasks/plans/{plan_id}")
    async def update_plan(
        user: str,
        plan_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        return backend.update_plan(user, plan_id, body)

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/actions/{action}")
    async def command_plan(user: str, plan_id: str, action: str) -> dict[str, Any]:
        return backend.command_plan(user, plan_id, action)

    @app.delete("/api/users/{user}/tasks/plans/{plan_id}")
    async def delete_plan(user: str, plan_id: str) -> dict[str, Any]:
        return backend.delete_plan(user, plan_id)

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
