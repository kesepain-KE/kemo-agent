"""Authenticated workspace projections and explicit controls for owned agent work."""
from typing import Any

from fastapi import FastAPI, Query, Response

from web.services.workspace_jobs import job_detail, list_jobs
from web.services.workspace_agents import cancel, guide, task, tasks
from web.operation_contracts import (
    WorkspaceAgentCancelResponse, WorkspaceAgentGuideResponse, WorkspaceAgentsResponse,
    WorkspaceAgentResponse, WorkspaceJobsResponse, WorkspaceJobDetailResponse,
)
from web.schemas import WorkspaceAgentGuideBody

_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def register_workspace_routes(app: FastAPI, backend: Any) -> None:
    @app.get("/api/users/{user}/workspace/agents", response_model=WorkspaceAgentsResponse,
             response_model_exclude_unset=True)
    def workspace_agents(user: str, response: Response, source: str = "", session_id: str = "",
                         page: int = Query(default=1, ge=1),
                         page_size: int = Query(default=20, ge=1, le=50)):
        response.headers.update(_HEADERS)
        return tasks(backend, user, source=source, session_id=session_id, page=page, page_size=page_size)

    @app.get("/api/users/{user}/workspace/agents/{task_id}", response_model=WorkspaceAgentResponse,
             response_model_exclude_unset=True)
    def workspace_agent(user: str, task_id: str, response: Response, source: str = "", session_id: str = ""):
        response.headers.update(_HEADERS)
        return task(backend, user, task_id, source=source, session_id=session_id)

    @app.post(
        "/api/users/{user}/workspace/agents/{task_id}/guide",
        response_model=WorkspaceAgentGuideResponse,
    )
    def guide_workspace_agent(
        user: str,
        task_id: str,
        body: WorkspaceAgentGuideBody,
        response: Response,
        source: str = Query(..., min_length=1, max_length=32),
        session_id: str = Query(..., min_length=1, max_length=128),
    ):
        response.headers.update(_HEADERS)
        return guide(
            backend, user, task_id, source=source, session_id=session_id,
            guidance=body.guidance,
        )

    @app.post(
        "/api/users/{user}/workspace/agents/{task_id}/cancel",
        response_model=WorkspaceAgentCancelResponse,
    )
    def cancel_workspace_agent(
        user: str,
        task_id: str,
        response: Response,
        source: str = Query(..., min_length=1, max_length=32),
        session_id: str = Query(..., min_length=1, max_length=128),
    ):
        response.headers.update(_HEADERS)
        return cancel(backend, user, task_id, source=source, session_id=session_id)

    @app.get("/api/users/{user}/workspace/jobs", response_model=WorkspaceJobsResponse,
             response_model_exclude_unset=True)
    def workspace_jobs(user: str, response: Response, session_id: str = "", source: str = "",
                       page: int = Query(default=1, ge=1),
                       page_size: int = Query(default=20, ge=1, le=50)):
        response.headers.update(_HEADERS)
        return list_jobs(backend, user, session_id=session_id, source=source, page=page, page_size=page_size)

    @app.get("/api/users/{user}/workspace/jobs/{job_id}", response_model=WorkspaceJobDetailResponse,
             response_model_exclude_unset=True)
    def workspace_job(user: str, job_id: str, response: Response, session_id: str = "", source: str = ""):
        response.headers.update(_HEADERS)
        return job_detail(backend, user, job_id, session_id=session_id, source=source)
