"""Field-level contracts for critical JSON operations, not SSE/file responses."""
from typing import Any, Literal

from pydantic import Field

from web.api_contracts import PlanResponse, ResponseObject


class AuthMethodsResponse(ResponseObject):
    token: bool
    password: bool


class AuthStatusResponse(ResponseObject):
    enabled: bool
    authenticated: bool
    methods: AuthMethodsResponse
    stage: Literal["none", "token", "password", "authenticated"]
    requires_both: bool
    session_cookie_configured: bool


class AuthLogoutResponse(ResponseObject):
    authenticated: bool


class DeleteSessionResponse(ResponseObject):
    user: str
    source: str
    session_id: str
    deleted: bool


class DeleteSessionsResponse(ResponseObject):
    user: str
    source: str
    deleted: bool
    deleted_sessions: int = Field(ge=0)
    deleted_windows: int = Field(ge=0)


class PlanMutationResponse(ResponseObject):
    user: str
    plan: PlanResponse
    updated: bool


class PlanDeleteResponse(ResponseObject):
    user: str
    plan_id: str
    deleted: bool


class CronSummaryResponse(ResponseObject):
    task_id: str
    title: str
    user_defined: bool
    status: str
    type: str


class CronReadResponse(ResponseObject):
    user: str
    cron_task: CronSummaryResponse


class CronMutationResponse(CronReadResponse):
    updated: bool


class CronDeleteResponse(ResponseObject):
    user: str
    task_id: str
    deleted: bool


class CleanupPreviewItemResponse(ResponseObject):
    id: str
    title: str
    version: str


class CleanupPreviewResponse(ResponseObject):
    user: str
    category: Literal["plans", "crons", "executions"]
    count: int = Field(ge=0)
    items: list[CleanupPreviewItemResponse]


class CleanupErrorResponse(ResponseObject):
    id: str
    message: str


class CleanupResultResponse(ResponseObject):
    user: str
    category: Literal["plans", "crons", "executions"]
    removed_count: int = Field(ge=0)
    removed: list[str]
    skipped: list[str]
    errors: list[CleanupErrorResponse]


class WorkspaceAgentSummary(ResponseObject):
    id: str
    agent: str
    source: str
    session_id: str
    status: str
    description: str
    created_at: str
    updated_at: str
    submission_order: int | None = Field(default=None, ge=1)
    children: list["WorkspaceAgentSummary"] | None = None
    children_total: int | None = Field(default=None, ge=0)


class WorkspaceAgentsResponse(ResponseObject):
    tasks: list[WorkspaceAgentSummary]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    total_pages: int = Field(ge=1)


class AgentEventResponse(ResponseObject):
    at: str
    status: str


class AgentArtifactResponse(ResponseObject):
    scope: Literal["file_upload", "download"]
    relative_path: str
    name: str
    size: int = Field(ge=0)


class WorkspaceAgentDetail(WorkspaceAgentSummary):
    events: list[AgentEventResponse] | None = None
    artifacts: list[AgentArtifactResponse]
    result: Any = None


class WorkspaceAgentResponse(ResponseObject):
    task: WorkspaceAgentDetail


class WorkspaceJobResponse(ResponseObject):
    job_id: str
    status: str
    source: str
    session_id: str
    command_digest: str
    exit_code: int | None = None


class WorkspaceJobsResponse(ResponseObject):
    user: str
    jobs: list[WorkspaceJobResponse]
    active_count: int = Field(ge=0)
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=50)
    total_pages: int = Field(ge=1)
    read_only: bool


class JobStreamResponse(ResponseObject):
    text: str
    size: int = Field(ge=0)
    truncated: bool
    error: str


class JobStreamsResponse(ResponseObject):
    stdout: JobStreamResponse
    stderr: JobStreamResponse


class WorkspaceJobDetailResponse(ResponseObject):
    user: str
    job: WorkspaceJobResponse
    streams: JobStreamsResponse
    generated_at: str
    read_only: bool
    tail_bytes: int = Field(gt=0)
