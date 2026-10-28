"""Validated HTTP contracts; extra response fields remain forward-compatible."""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class ResponseObject(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)


class TaskSummaryResponse(ResponseObject):
    active_plans: int
    waiting_plans: int
    enabled_crons: int
    completed_plans: int


class ProgressResponse(ResponseObject):
    completed: int
    total: int
    percent: float


class StepResponse(ResponseObject):
    step_id: str
    title: str
    status: str
    depends_on: list[str]


class PlanResponse(ResponseObject):
    plan_id: str
    title: str
    status: str
    revision: int
    source: str
    session_id: str
    steps: list[StepResponse]
    progress: ProgressResponse


class TasksResponse(ResponseObject):
    user: str
    summary: TaskSummaryResponse
    plans: list[PlanResponse]
    cron_tasks: list[dict[str, Any]]
    executions: list[dict[str, Any]]


class ApiStatusResponse(ResponseObject):
    type: str
    model: str
    configured: bool


class ContextResponse(ResponseObject):
    percent: float
    used_tokens: int
    max_tokens: int


class TokenResponse(ResponseObject):
    date: str
    timezone: str
    sent_tokens: int
    received_tokens: int
    total_tokens: int
    cached_tokens: int
    cache_rate: float
    request_count: int
    estimated: bool
    trend: list[int]


class PromptComponentResponse(ResponseObject):
    id: str
    name: str
    state: str
    chars: int
    tokens: int
    source_files: list[str]
    injected_items: int
    original_items: int


class PromptResponse(ResponseObject):
    content: str
    total_chars: int
    estimated_tokens: int
    components: list[PromptComponentResponse]


class RouteSummaryResponse(ResponseObject):
    connected_transports: int
    total_transports: int


class MessageRoutesResponse(ResponseObject):
    summary: RouteSummaryResponse
    routes: list[dict[str, Any]]


class SystemCronResponse(ResponseObject):
    tracking: str
    tasks: list[dict[str, Any]]
    executions: list[dict[str, Any]]


class ProviderCongestionResponse(ResponseObject):
    active_requests: int
    max_requests: int
    available_requests: int
    waiting_estimate: int


class WebCongestionResponse(ResponseObject):
    active_chats: int
    max_chats: int
    pending_chats: int
    max_pending: int


class RouterCongestionResponse(ResponseObject):
    active_workers: int
    max_workers: int
    queued_messages: int
    max_queued: int


class CongestionResponse(ResponseObject):
    provider: ProviderCongestionResponse
    web: WebCongestionResponse
    message_router: RouterCongestionResponse


class RuntimeResponse(ResponseObject):
    user: str
    api: ApiStatusResponse
    context: ContextResponse
    tokens: TokenResponse
    prompt: PromptResponse
    message_routes: MessageRoutesResponse
    system_cron: SystemCronResponse
    congestion: CongestionResponse


class KnowledgeSummaryResponse(ResponseObject):
    documents: int
    user_documents: int
    shared_documents: int
    global_documents: int


class KnowledgeDocumentResponse(ResponseObject):
    scope: str
    relative_path: str
    title: str
    size: int
    updated_at: float
    active_for_main_agent: bool


class KnowledgeResponse(ResponseObject):
    user: str
    summary: KnowledgeSummaryResponse
    documents: list[KnowledgeDocumentResponse]


class MemoryResponse(ResponseObject):
    user: str
    summary: dict[str, int]
    items: list[dict[str, Any]]


class WriteBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PlanBody(WriteBody):
    # Legacy clients may send scope, but only URL/query identity is authoritative.
    user: str | None = None
    source: str | None = None
    session_id: str | None = None
    plan_id: str | None = None
    title: str | None = None
    description: str | None = None
    steps: list[dict[str, Any]] | None = None
    status: str | None = None
    auto_accept: bool | None = None
    reminder: str | None = None
    current_step: str | None = None
    revision: int | None = Field(default=None, ge=1)


class PlanEditBody(WriteBody):
    title: str | None = None
    description: str | None = None
    steps: list[dict[str, Any]] | None = None
    revision: int | None = Field(default=None, ge=1)


class RetryBody(WriteBody):
    revision: int | None = Field(default=None, ge=1)


class RollbackBody(WriteBody):
    revision: int = Field(ge=1)
    current_revision: int = Field(ge=1)


class CleanupItem(WriteBody):
    id: str
    version: str


class CleanupBody(WriteBody):
    category: Literal["plans", "crons", "executions"]
    source: str = "web"
    session_id: str = ""
    items: list[CleanupItem] = Field(max_length=10000)


class CronBody(WriteBody):
    title: str | None = None
    prompt: str | None = None
    type: Literal["once", "daily", "weekly", "monthly", "recurring"] | None = None
    status: str | None = None
    interval_seconds: int | None = Field(default=None, ge=1)
    time: str | None = None
    times: list[str] | None = None
    weekdays: list[int] | None = None
    month_days: list[int] | None = None
    start_date: str | None = None
    end_date: str | None = None
    next_run_at: str | None = None
    max_runs: int | None = Field(default=None, ge=1)
