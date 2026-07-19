"""用于 kemo-agent Web 后端的 FastAPI 应用程序工厂。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from events import RunEvent, TERMINAL_EVENTS
from run.config import project_root
from web.auth import (
    WEB_SESSION_MAX_AGE_SECONDS,
    WebAuthConfig,
    WebAuthError,
    WebAuthenticator,
)
from web.service import (
    InvalidRequestError,
    WebRunService,
    WebServiceError,
)


class ChatBody(BaseModel):
    user: str
    session_id: str
    prompt: str
    run_id: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class GuidanceBody(BaseModel):
    user: str
    guidance: str


class SessionRenameBody(BaseModel):
    title: str


def _error_body(code: str, message: str, status: int) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "status": status}}


def _sse(event: RunEvent) -> bytes:
    payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.type}\ndata: {payload}\n\n".encode("utf-8")


def _safe_internal_message(_: BaseException) -> str:
    return "Web 服务处理请求失败"


def _frontend_media_type(path: Path) -> str | None:
    return {
        ".css": "text/css",
        ".js": "text/javascript",
        ".json": "application/json",
        ".mjs": "text/javascript",
        ".svg": "image/svg+xml",
        ".wasm": "application/wasm",
    }.get(path.suffix.lower())


def create_app(
    *,
    root: Path | None = None,
    service: WebRunService | None = None,
    auth_config: WebAuthConfig | None = None,
) -> FastAPI:
    base = (root or project_root()).resolve()
    backend = service or WebRunService(base)
    frontend_dist = (base / "web" / "frontend" / "dist").resolve()
    app = FastAPI(title="kemo-agent Web API", version="2")
    app.state.web_service = backend
    configured_auth = auth_config or WebAuthConfig()
    authenticator = WebAuthenticator(configured_auth)
    app.state.web_auth = configured_auth

    @app.middleware("http")
    async def require_web_auth(request: Request, call_next):
        path = request.url.path
        public = path == "/api/health" or path.startswith("/api/auth/")
        query_token = request.query_params.get("token", "")
        if query_token:
            try:
                authenticator.authenticate_token(query_token)
                authenticator.establish(request.session, "token")
            except WebAuthError as exc:
                return JSONResponse(
                    status_code=exc.status,
                    content=_error_body(exc.code, str(exc), exc.status),
                )
        if (
            configured_auth.enabled
            and (path == "/api" or path.startswith("/api/"))
            and not public
            and not authenticator.is_authenticated(request.session)
        ):
            return JSONResponse(
                status_code=401,
                content=_error_body(
                    "authentication_required",
                    "需要先完成 Web 认证",
                    401,
                ),
            )
        return await call_next(request)

    # SessionMiddleware must wrap the auth guard so request.session is available.
    if configured_auth.enabled:
        app.add_middleware(
            SessionMiddleware,
            secret_key=configured_auth.session_secret,
            session_cookie=configured_auth.cookie_name,
            max_age=WEB_SESSION_MAX_AGE_SECONDS,
            same_site="lax",
            https_only=False,
        )

    @app.exception_handler(WebServiceError)
    async def web_service_error(_: Request, exc: WebServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_error_body(exc.code, str(exc), exc.status),
        )

    @app.exception_handler(WebAuthError)
    async def web_auth_error(_: Request, exc: WebAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_error_body(exc.code, str(exc), exc.status),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=_error_body("invalid_request", "请求字段无效", 400),
        )

    @app.exception_handler(Exception)
    async def internal_error(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=_error_body("internal_error", _safe_internal_message(exc), 500),
        )

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return backend.health()

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        session = request.session if configured_auth.enabled else None
        return authenticator.status(session)

    @app.post("/api/auth/login")
    async def auth_login(body: LoginBody, request: Request) -> dict[str, Any]:
        authenticator.authenticate_password(body.username, body.password)
        authenticator.establish(request.session, "password")
        return authenticator.status(request.session)

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> dict[str, bool]:
        if configured_auth.enabled:
            authenticator.logout(request.session)
        return {"authenticated": False}

    @app.get("/api/users")
    async def users() -> dict[str, Any]:
        return {"users": backend.users()}

    @app.get("/api/users/{user}/sessions")
    async def sessions(
        user: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.sessions(user, source=source)

    @app.delete("/api/users/{user}/sessions")
    async def delete_all_sessions(
        user: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.delete_all_sessions(user, source=source)

    @app.patch("/api/users/{user}/sessions/{session_id}")
    async def rename_session(
        user: str,
        session_id: str,
        body: SessionRenameBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.rename_session(user, session_id, body.title, source=source)

    @app.delete("/api/users/{user}/sessions/{session_id}")
    async def delete_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.delete_session(user, session_id, source=source)

    @app.get("/api/users/{user}/sessions/{session_id}/history")
    async def history(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.history(user, session_id, source=source)

    @app.get("/api/users/{user}/overview")
    async def overview(
        user: str,
        session_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.overview(user, session_id=session_id)

    @app.get("/api/users/{user}/tasks")
    async def tasks(user: str) -> dict[str, Any]:
        return backend.tasks(user)

    @app.get("/api/users/{user}/knowledge")
    async def knowledge(user: str) -> dict[str, Any]:
        return backend.knowledge(user)

    @app.get("/api/users/{user}/skills")
    async def skills(user: str) -> dict[str, Any]:
        return backend.skills(user)

    @app.get("/api/users/{user}/sense")
    async def sense(user: str) -> dict[str, Any]:
        return backend.sense(user)

    @app.get("/api/users/{user}/settings")
    async def settings(user: str) -> dict[str, Any]:
        value = backend.settings(user)
        return {**value, "authentication": configured_auth.public_summary()}

    @app.get("/api/users/{user}/prompt/sections")
    async def prompt_sections(user: str) -> dict[str, Any]:
        return backend.prompt_sections(user)

    @app.get("/api/users/{user}/memory/summary")
    async def memory_summary(user: str) -> dict[str, Any]:
        return backend.memory_summary(user)

    @app.get("/api/users/{user}/config/full")
    async def full_config(user: str) -> dict[str, Any]:
        return backend.user_config(user)

    @app.post("/api/runs/{run_id}/guidance")
    async def submit_guidance(run_id: str, body: GuidanceBody) -> dict[str, Any]:
        return backend.submit_guidance(body.user, run_id, body.guidance)

    @app.post("/api/chat")
    async def chat(body: ChatBody, request: Request) -> StreamingResponse:
        cancel_event = threading.Event()
        try:
            events = backend.stream_chat(
                body.user,
                body.session_id,
                body.prompt,
                cancel_event=cancel_event,
                run_id=body.run_id,
            )
        except WebServiceError:
            raise
        except Exception as exc:
            raise InvalidRequestError("无法创建聊天请求") from exc

        async def generate():
            terminal = False
            iterator = iter(events)
            try:
                while True:
                    if await request.is_disconnected():
                        cancel_event.set()
                        break
                    event = await asyncio.to_thread(next, iterator, None)
                    if event is None:
                        break
                    if not isinstance(event, RunEvent):
                        event = RunEvent(
                            type="error",
                            error={
                                "message": "Run 服务返回了无效事件",
                                "exception_type": "InvalidRunEvent",
                                "phase": "web_stream",
                            },
                        )
                    yield _sse(event)
                    if event.type in TERMINAL_EVENTS:
                        terminal = True
                        break
                if not terminal and not cancel_event.is_set():
                    yield _sse(
                        RunEvent(
                            type="error",
                            error={
                                "message": "Run 流在终态事件前结束",
                                "exception_type": "MissingTerminalEvent",
                                "phase": "web_stream",
                            },
                        )
                    )
            except asyncio.CancelledError:
                cancel_event.set()
                raise
            except Exception:
                if not cancel_event.is_set():
                    yield _sse(
                        RunEvent(
                            type="error",
                            error={
                                "message": "Web 流式桥接失败",
                                "exception_type": "WebStreamError",
                                "phase": "web_stream",
                            },
                        )
                    )
            finally:
                cancel_event.set()
                close = getattr(iterator, "close", None)
                if callable(close):
                    close()

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Kemo-Run-Id": body.run_id,
            },
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content=_error_body("not_found", "接口不存在", 404),
            )

        index = frontend_dist / "index.html"
        if not index.is_file():
            return JSONResponse(
                status_code=503,
                content=_error_body(
                    "ui_not_built",
                    "前端尚未构建，请在 web/frontend 执行 npm.cmd run build",
                    503,
                ),
            )

        candidate = (frontend_dist / full_path).resolve()
        try:
            candidate.relative_to(frontend_dist)
        except ValueError:
            candidate = index
        if full_path and candidate.is_file():
            return FileResponse(candidate, media_type=_frontend_media_type(candidate))
        return FileResponse(index)

    return app
