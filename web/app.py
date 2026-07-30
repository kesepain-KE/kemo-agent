"""用于 kemo-agent Web 后端的 FastAPI 应用程序工厂。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from events import RunEvent, TERMINAL_EVENTS
from run.config import project_root
from web.auth import (
    AuthFailureLimiter,
    WEB_SESSION_MAX_AGE_SECONDS,
    WebAuthConfig,
    WebAuthError,
    WebAuthenticator,
    resolve_client_ip,
)
from web.routes.files import register_file_routes
from web.routes.identity import register_identity_routes
from web.routes.modules import register_module_routes
from web.routes.sessions import register_session_routes
from web.routes.settings import register_setting_routes
from web.routes.tasks import register_task_routes
from web.schemas import (
    ChatBody,
    DeleteManyBody,
    GuidanceBody,
    LoginBody,
    MemoryWriteBody,
    PreferencesBody,
    RestartBody,
    RunCancelBody,
    SessionClientBody,
    SessionRenameBody,
    SessionUndoLastRoundBody,
    SkillToggleBody,
    SoulBody,
    TextBody,
    TokenLoginBody,
)
from web.service import ConflictError, InvalidRequestError, WebRunService, WebServiceError


__all__ = [
    "ChatBody",
    "DeleteManyBody",
    "GuidanceBody",
    "LoginBody",
    "MemoryWriteBody",
    "PreferencesBody",
    "RestartBody",
    "RunCancelBody",
    "SessionClientBody",
    "SessionRenameBody",
    "SessionUndoLastRoundBody",
    "SkillToggleBody",
    "SoulBody",
    "TextBody",
    "TokenLoginBody",
    "create_app",
]


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


def _spawn_restart_helper(base: Path, port: int) -> int:
    command = [
        sys.executable,
        str(base / "restart.py"),
        f"--port={port}",
        f"--parent-pid={os.getpid()}",
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(base),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    return process.pid


def create_app(
    *,
    root: Path | None = None,
    service: WebRunService | None = None,
    auth_config: WebAuthConfig | None = None,
) -> FastAPI:
    """Create the FastAPI application and compose domain route registrars."""

    base = (root or project_root()).resolve()
    backend = service or WebRunService(base)
    frontend_dist = (base / "web" / "frontend" / "dist").resolve()
    app = FastAPI(title="kemo-agent Web API", version="2")
    app.state.web_service = backend
    configured_auth = auth_config or WebAuthConfig()
    authenticator = WebAuthenticator(configured_auth)
    auth_limiter = AuthFailureLimiter(configured_auth)
    app.state.web_auth = configured_auth
    app.state.web_auth_limiter = auth_limiter
    restart_lock = threading.Lock()
    app.state.restart_requested = False

    @app.middleware("http")
    async def require_web_auth(request: Request, call_next):
        path = request.url.path
        public = path in {"/api/health", "/api/logo"} or path.startswith(
            "/api/auth/"
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
            headers=exc.headers,
        )

    @app.exception_handler(WebAuthError)
    async def web_auth_error(_: Request, exc: WebAuthError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_error_body(exc.code, str(exc), exc.status),
            headers=exc.headers,
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

    @app.post("/api/system/restart")
    async def restart_system(body: RestartBody) -> dict[str, Any]:
        active_run_checker = getattr(backend, "has_active_runs", None)
        if not body.force and callable(active_run_checker) and active_run_checker():
            raise ConflictError("存在正在运行的对话，请结束当前响应后再重启智能体")
        with restart_lock:
            if app.state.restart_requested:
                return {"ok": True, "port": body.port, "already_requested": True}
            helper_pid = _spawn_restart_helper(base, body.port)
            app.state.restart_requested = True
        return {"ok": True, "port": body.port, "helper_pid": helper_pid}

    @app.get("/api/auth/status")
    async def auth_status(request: Request) -> dict[str, Any]:
        session = request.session if configured_auth.enabled else None
        return authenticator.status(session)

    def auth_client_ip(request: Request) -> str:
        peer = request.client.host if request.client is not None else "unknown"
        return resolve_client_ip(
            peer,
            request.headers.get("x-forwarded-for", ""),
            configured_auth.trusted_proxies,
        )

    @app.post("/api/auth/token")
    async def auth_token(body: TokenLoginBody, request: Request) -> dict[str, Any]:
        client_ip = auth_client_ip(request)
        auth_limiter.check(client_ip, "token")
        try:
            authenticator.authenticate_token(body.token)
        except WebAuthError as exc:
            if exc.code == "invalid_credentials":
                auth_limiter.failure(client_ip, "token")
            raise
        auth_limiter.success(client_ip, "token")
        if configured_auth.requires_both:
            authenticator.establish_token_stage(request.session)
        else:
            authenticator.establish(request.session, "token")
            auth_limiter.clear_ip(client_ip)
        return authenticator.status(request.session)

    @app.post("/api/auth/login")
    async def auth_login(body: LoginBody, request: Request) -> dict[str, Any]:
        if configured_auth.requires_both and not authenticator.token_stage_verified(
            request.session
        ):
            raise WebAuthError(
                "auth_stage_required",
                "请先完成访问令牌验证",
                409,
            )
        client_ip = auth_client_ip(request)
        auth_limiter.check(client_ip, "password")
        try:
            authenticator.authenticate_password(body.username, body.password)
        except WebAuthError as exc:
            if exc.code == "invalid_credentials":
                auth_limiter.failure(client_ip, "password")
            raise
        auth_limiter.success(client_ip, "password")
        method = "token+password" if configured_auth.requires_both else "password"
        authenticator.establish(request.session, method)
        auth_limiter.clear_ip(client_ip)
        return authenticator.status(request.session)

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request) -> dict[str, bool]:
        if configured_auth.enabled:
            authenticator.logout(request.session)
        return {"authenticated": False}

    # Domain routers keep HTTP declarations aligned with service boundaries.
    register_identity_routes(app, backend)
    register_file_routes(app, backend)
    register_module_routes(app, backend)
    register_session_routes(app, backend)
    register_task_routes(app, backend)
    register_setting_routes(app, backend, configured_auth)

    @app.post("/api/runs/{run_id}/guidance")
    async def submit_guidance(run_id: str, body: GuidanceBody) -> dict[str, Any]:
        return backend.submit_guidance(
            body.user,
            run_id,
            body.guidance,
            guidance_id=body.guidance_id,
            uploaded_files=body.uploaded_files,
        )

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, body: RunCancelBody) -> dict[str, Any]:
        return backend.cancel_run(body.user, run_id)

    @app.post("/api/chat")
    async def chat(body: ChatBody, request: Request) -> StreamingResponse:
        cancel_event = threading.Event()
        events: Iterator[RunEvent] | None = None
        try:
            content_options = {"content": body.content} if body.content else {}
            if body.uploaded_files:
                content_options["uploaded_files"] = body.uploaded_files
            # stream_chat 可能在用户级并发闸前有界等待；放入工作线程，避免
            # 一个用户的排队请求阻塞 FastAPI 事件循环和其他用户的 API。
            if body.plan_id:
                chat_task = asyncio.create_task(
                    asyncio.to_thread(
                        backend.stream_plan,
                        body.user,
                        body.session_id,
                        body.plan_id,
                        cancel_event=cancel_event,
                        run_id=body.run_id,
                        client_id=body.client_id,
                    )
                )
            else:
                chat_task = asyncio.create_task(
                    asyncio.to_thread(
                        backend.stream_chat,
                        body.user,
                        body.session_id,
                        body.prompt,
                        cancel_event=cancel_event,
                        run_id=body.run_id,
                        client_id=body.client_id,
                        **content_options,
                    )
                )
            try:
                events = await asyncio.shield(chat_task)
            except asyncio.CancelledError:
                cancel_event.set()
                # shield 保留工作线程结果；若闸门刚好放行并返回生成器，必须
                # 主动关闭它，避免客户端断开后泄漏 Web 并发槽位。
                try:
                    abandoned = await chat_task
                except BaseException:
                    pass
                else:
                    close = getattr(abandoned, "close", None)
                    if callable(close):
                        await asyncio.to_thread(close)
                raise
        except WebServiceError:
            raise
        except Exception as exc:
            raise InvalidRequestError("无法创建聊天请求") from exc

        assert events is not None

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
                    await asyncio.to_thread(close)

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
