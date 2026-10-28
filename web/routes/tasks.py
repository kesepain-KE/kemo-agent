"""任务计划与定时任务路由。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query

from web.service import WebRunService
from web.api_contracts import (TasksResponse, PlanBody, PlanEditBody, RetryBody, RollbackBody, CleanupBody, CronBody)
from web.operation_contracts import (
    CleanupPreviewResponse, CleanupResultResponse, PlanMutationResponse, PlanDeleteResponse,
    CronReadResponse, CronMutationResponse, CronDeleteResponse,
)


def register_task_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/tasks", response_model=TasksResponse)
    async def tasks(
        user: str,
        source: str = Query("web", max_length=32),
        session_id: str = Query("", max_length=256),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(backend.tasks, user, source=source, session_id=session_id)

    @app.get("/api/users/{user}/tasks/cleanup", response_model=CleanupPreviewResponse)
    async def preview_cleanup(user: str, category: str = Query(...),
                              source: str = Query("web", max_length=32),
                              session_id: str = Query("", max_length=256)) -> dict[str, Any]:
        return backend.preview_task_cleanup(user, category, source=source, session_id=session_id)

    @app.post("/api/users/{user}/tasks/cleanup", response_model=CleanupResultResponse)
    async def cleanup(user: str, body: CleanupBody) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.cleanup_completed_tasks(user, payload)

    @app.post("/api/users/{user}/tasks/plans", response_model=PlanMutationResponse)
    async def create_plan(
        user: str,
        body: PlanBody,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.create_plan(user, payload, session_id, source)

    @app.put("/api/users/{user}/tasks/plans/{plan_id}", response_model=PlanMutationResponse)
    async def update_plan(
        user: str,
        plan_id: str,
        body: PlanBody,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.update_plan(user, plan_id, payload, session_id, source)

    @app.patch("/api/users/{user}/tasks/plans/{plan_id}/edit", response_model=PlanMutationResponse)
    async def edit_plan(
        user: str,
        plan_id: str,
        body: PlanEditBody,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.edit_plan(user, plan_id, payload, session_id, source)

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/steps/{step_id}/retry", response_model=PlanMutationResponse)
    async def retry_plan_step(
        user: str,
        plan_id: str,
        step_id: str,
        body: RetryBody,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.retry_plan_step(user, plan_id, step_id, payload, session_id, source)

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

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/rollback", response_model=PlanMutationResponse)
    async def rollback_plan(
        user: str,
        plan_id: str,
        body: RollbackBody,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.rollback_plan(
            user,
            plan_id,
            payload.get("revision"),
            payload.get("current_revision"),
            session_id,
            source,
        )

    @app.post("/api/users/{user}/tasks/plans/{plan_id}/actions/{action}", response_model=PlanMutationResponse)
    async def command_plan(
        user: str,
        plan_id: str,
        action: str,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.command_plan(user, plan_id, action, session_id, source)

    @app.delete("/api/users/{user}/tasks/plans/{plan_id}", response_model=PlanDeleteResponse)
    async def delete_plan(
        user: str,
        plan_id: str,
        session_id: str = Query("", max_length=256),
        source: str = Query("web", max_length=32),
    ) -> dict[str, Any]:
        return backend.delete_plan(user, plan_id, session_id, source)

    @app.post("/api/users/{user}/tasks/crons", response_model=CronMutationResponse)
    async def create_cron(user: str, body: CronBody) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.create_cron(user, payload)

    @app.get("/api/users/{user}/tasks/crons/{task_id}", response_model=CronReadResponse)
    async def get_cron(user: str, task_id: str) -> dict[str, Any]:
        return backend.get_cron(user, task_id)

    @app.put("/api/users/{user}/tasks/crons/{task_id}", response_model=CronMutationResponse)
    async def update_cron(
        user: str,
        task_id: str,
        body: CronBody,
    ) -> dict[str, Any]:
        payload = body.model_dump(exclude_unset=True)
        return backend.update_cron(user, task_id, payload)

    @app.delete("/api/users/{user}/tasks/crons/{task_id}", response_model=CronDeleteResponse)
    async def delete_cron(user: str, task_id: str) -> dict[str, Any]:
        return backend.delete_cron(user, task_id)
