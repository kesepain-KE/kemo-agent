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
from urllib.parse import quote

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator
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
from web.service import (
    AVATAR_MAX_BYTES,
    ConflictError,
    FILE_UPLOAD_MAX_BYTES,
    InvalidRequestError,
    SKILL_ARCHIVE_MAX_BYTES,
    WebRunService,
    WebServiceError,
)


class ChatBody(BaseModel):
    user: str
    session_id: str
    prompt: str = ""
    content: list[dict[str, Any]] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list, max_length=20)
    run_id: str = ""
    plan_id: str = ""
    client_id: str = ""

    @model_validator(mode="after")
    def require_input(self) -> "ChatBody":
        if (
            not self.prompt.strip()
            and not self.content
            and not self.uploaded_files
            and not self.plan_id.strip()
        ):
            raise ValueError("prompt、content、uploaded_files 和 plan_id 不能同时为空")
        return self


class LoginBody(BaseModel):
    username: str
    password: str


class TokenLoginBody(BaseModel):
    token: str


class GuidanceBody(BaseModel):
    user: str
    guidance: str


class RunCancelBody(BaseModel):
    user: str


class SessionRenameBody(BaseModel):
    title: str


class SessionClientBody(BaseModel):
    client_id: str = ""


class SessionUndoLastRoundBody(BaseModel):
    expected_round: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=1_000_000)


class SoulBody(BaseModel):
    content: str


class TextBody(BaseModel):
    content: str


class MemoryWriteBody(BaseModel):
    content: str
    tier: str | None = None


class PreferencesBody(BaseModel):
    theme: str | None = None
    font_size: str | None = None


class SkillToggleBody(BaseModel):
    enabled: bool


class DeleteManyBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=10_000)


class RestartBody(BaseModel):
    port: int = Field(ge=1, le=65535)
    force: bool = False


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
        public = (
            path in {"/api/health", "/api/logo"}
            or path.startswith("/api/auth/")
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

    @app.get("/api/users")
    async def users() -> dict[str, Any]:
        return {"users": backend.users()}

    @app.get("/api/users/{user}/files/{scope}")
    async def files(
        user: str,
        scope: str,
        path: str = Query(default="", max_length=500),
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=6, ge=1, le=100),
    ) -> dict[str, Any]:
        return backend.files(
            user,
            scope,
            path=path,
            search=search,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/users/{user}/files/{scope}/upload")
    async def upload_file(
        user: str,
        scope: str,
        file: UploadFile = File(...),
        path: str = Query(...),
    ) -> dict[str, Any]:
        try:
            data = await file.read(FILE_UPLOAD_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_file(user, scope, path, data)

    @app.put("/api/users/{user}/files/{scope}/text")
    async def write_file_text(
        user: str,
        scope: str,
        body: TextBody,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.write_file_text(user, scope, path, body.content)

    @app.get("/api/users/{user}/files/{scope}/text")
    async def read_file_text(user: str, scope: str, path: str = Query(...)) -> dict[str, Any]:
        return backend.read_file_text(user, scope, path)

    @app.post("/api/users/{user}/files/{scope}/directory")
    async def make_file_directory(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.make_directory(user, scope, path)

    @app.patch("/api/users/{user}/files/{scope}/move")
    async def move_file(
        user: str,
        scope: str,
        path: str = Query(...),
        new_path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.move_file(user, scope, path, new_path)

    @app.get("/api/users/{user}/files/{scope}/download")
    async def download_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> FileResponse:
        target = backend.file_download(user, scope, path)
        return FileResponse(target, filename=target.name)

    @app.get("/api/users/{user}/files/{scope}/preview")
    async def preview_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> FileResponse:
        target, media_type, _ = backend.file_preview(user, scope, path)
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'",
            },
        )

    @app.delete("/api/users/{user}/files/{scope}")
    async def delete_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.delete_file(user, scope, path)

    @app.post("/api/users/{user}/files/{scope}/delete-many")
    async def delete_files(user: str, scope: str, body: DeleteManyBody) -> dict[str, Any]:
        return backend.delete_files(user, scope, body.paths)

    @app.delete("/api/users/{user}/files/{scope}/all")
    async def delete_all_files(user: str, scope: str) -> dict[str, Any]:
        return backend.delete_all_files(user, scope)

    @app.post("/api/users/{user}/avatar")
    async def upload_avatar(
        user: str,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        content_type = file.content_type
        try:
            data = await file.read(AVATAR_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_avatar(user, data, content_type)

    @app.get("/api/users/{user}/avatar")
    async def avatar(user: str) -> Response:
        target = backend.avatar(user)
        if target is None:
            return Response(status_code=204)
        return FileResponse(target)

    @app.get("/api/tmp")
    async def tmp_files(
        path: str = Query(default="", max_length=500),
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=6, ge=1, le=100),
    ) -> dict[str, Any]:
        return backend.tmp_files(
            path=path,
            search=search,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/tmp/upload")
    async def upload_tmp_file(
        file: UploadFile = File(...),
        path: str = Query(...),
    ) -> dict[str, Any]:
        try:
            data = await file.read(FILE_UPLOAD_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_tmp_file(path, data)

    @app.get("/api/tmp/preview")
    async def preview_tmp_file(path: str = Query(...)) -> FileResponse:
        target, media_type, _ = backend.tmp_file_preview(path)
        return FileResponse(
            target,
            media_type=media_type,
            filename=target.name,
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'",
            },
        )

    @app.put("/api/tmp/text")
    async def write_tmp_text(path: str = Query(...), body: TextBody = ...) -> dict[str, Any]:
        return backend.write_tmp_text(path, body.content)

    @app.get("/api/tmp/text")
    async def read_tmp_text(path: str = Query(...)) -> dict[str, Any]:
        return backend.read_tmp_text(path)

    @app.post("/api/tmp/directory")
    async def make_tmp_directory(path: str = Query(...)) -> dict[str, Any]:
        return backend.make_tmp_directory(path)

    @app.patch("/api/tmp/move")
    async def move_tmp_file(path: str = Query(...), new_path: str = Query(...)) -> dict[str, Any]:
        return backend.move_tmp_file(path, new_path)

    @app.delete("/api/tmp")
    async def delete_tmp_file(path: str = Query(...)) -> dict[str, Any]:
        return backend.delete_tmp_file(path)

    @app.post("/api/tmp/delete-many")
    async def delete_tmp_files(body: DeleteManyBody) -> dict[str, Any]:
        return backend.delete_tmp_files(body.paths)

    @app.delete("/api/tmp/all")
    async def delete_all_tmp_files() -> dict[str, Any]:
        return backend.delete_all_tmp_files()

    @app.get("/api/users/{user}/agents")
    async def agents(user: str) -> dict[str, Any]:
        return backend.agents(user)

    @app.delete("/api/users/{user}/agents/{agent}")
    async def delete_user_agent(user: str, agent: str) -> dict[str, Any]:
        return backend.delete_user_agent(user, agent)

    @app.get("/api/users/{user}/message/status")
    async def message_status(user: str) -> dict[str, Any]:
        return backend.message_status(user)

    @app.post("/api/users/{user}/message/modules/{module_name}/check")
    async def check_message_module(user: str, module_name: str) -> dict[str, Any]:
        return await asyncio.to_thread(backend.check_message_module, user, module_name)

    @app.delete("/api/users/{user}/message/modules/{module_name}")
    async def delete_message_module(user: str, module_name: str) -> dict[str, Any]:
        return backend.delete_message_module(user, module_name)

    @app.get("/api/users/{user}/soul")
    async def user_soul(user: str) -> dict[str, Any]:
        return backend.user_soul(user)

    @app.put("/api/users/{user}/soul")
    async def update_user_soul(user: str, body: SoulBody) -> dict[str, Any]:
        return backend.update_user_soul(user, body.content)

    @app.get("/api/global-soul")
    async def global_soul() -> dict[str, Any]:
        return backend.global_soul()

    @app.put("/api/global-soul")
    async def update_global_soul(body: SoulBody) -> dict[str, Any]:
        return backend.update_global_soul(body.content)

    @app.get("/api/logo")
    async def logo() -> Response:
        target = backend.logo()
        if target is None:
            return Response(status_code=204)
        return FileResponse(target, media_type="image/jpeg")

    @app.get("/api/users/{user}/expand")
    async def expands(user: str) -> dict[str, Any]:
        return backend.expands(user)

    @app.post("/api/users/{user}/expand/{scope}/{module_name}/refresh")
    async def refresh_expand_module(
        user: str, scope: str, module_name: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.refresh_expand_module, user, scope, module_name
        )

    @app.patch("/api/users/{user}/expand/{scope}/{module_name}/enabled")
    async def set_expand_module_enabled(
        user: str,
        scope: str,
        module_name: str,
        body: SkillToggleBody,
    ) -> dict[str, Any]:
        return backend.set_expand_module_enabled(
            user, scope, module_name, body.enabled
        )

    @app.delete("/api/users/{user}/expand/{scope}/{module_name}")
    async def delete_expand_module(
        user: str, scope: str, module_name: str
    ) -> dict[str, Any]:
        return backend.delete_expand_module(user, scope, module_name)

    @app.get("/api/users/{user}/sessions")
    async def sessions(
        user: str,
        source: str = Query(default="web"),
        query: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.sessions(user, source=source, query=query)

    @app.delete("/api/users/{user}/sessions")
    async def delete_all_sessions(
        user: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.delete_all_sessions(user, source=source)

    @app.get("/api/users/{user}/sessions/active")
    async def active_session(
        user: str,
        client_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(backend.active_session, user, client_id)

    @app.post("/api/users/{user}/sessions")
    async def create_session(
        user: str,
        body: SessionClientBody | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.create_session,
            user,
            body.client_id if body is not None else "",
        )

    @app.post("/api/users/{user}/sessions/{session_id}/close")
    async def close_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
        client_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.close_session,
            user,
            session_id,
            source=source,
            client_id=client_id,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/summary/retry")
    async def retry_session_summary(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.retry_session_summary,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/lease")
    async def session_lease(
        user: str,
        session_id: str,
        body: SessionClientBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.session_lease(
            user, session_id, body.client_id, source=source
        )

    @app.post("/api/users/{user}/sessions/{session_id}/lease/release")
    async def release_session_lease(
        user: str,
        session_id: str,
        body: SessionClientBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.release_session_lease(
            user, session_id, body.client_id, source=source
        )

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
        client_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.delete_session(
            user, session_id, source=source, client_id=client_id
        )

    @app.post("/api/users/{user}/sessions/{session_id}/compress")
    async def compress_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.compress_session,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/extract-memory")
    async def extract_session_memory(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.extract_session_memory,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/undo-last-round")
    async def undo_last_round(
        user: str,
        session_id: str,
        body: SessionUndoLastRoundBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.undo_last_round,
            user,
            session_id,
            body.expected_round,
            body.prompt,
            source=source,
        )

    @app.get("/api/users/{user}/sessions/{session_id}/history")
    async def history(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
        limit: int | None = Query(default=None, ge=1, le=100),
        before: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"source": source}
        if limit is not None:
            options["limit"] = limit
        if before is not None:
            options["before"] = before
        return backend.history(user, session_id, **options)

    @app.get("/api/users/{user}/overview")
    async def overview(
        user: str,
        session_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.overview(user, session_id=session_id)

    @app.get("/api/users/{user}/runtime/status")
    async def runtime_status(
        user: str,
        session_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.runtime_status(user, session_id=session_id)

    @app.get("/api/users/{user}/tasks")
    async def tasks(user: str) -> dict[str, Any]:
        return backend.tasks(user)

    @app.post("/api/users/{user}/tasks/plans")
    async def create_plan(user: str, body: dict[str, Any]) -> dict[str, Any]:
        return backend.create_plan(user, body)

    @app.put("/api/users/{user}/tasks/plans/{plan_id}")
    async def update_plan(user: str, plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
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
    async def update_cron(user: str, task_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return backend.update_cron(user, task_id, body)

    @app.delete("/api/users/{user}/tasks/crons/{task_id}")
    async def delete_cron(user: str, task_id: str) -> dict[str, Any]:
        return backend.delete_cron(user, task_id)

    @app.get("/api/users/{user}/knowledge")
    async def knowledge(user: str) -> dict[str, Any]:
        return backend.knowledge(user)

    @app.get("/api/users/{user}/knowledge/{scope}/document")
    async def knowledge_document(user: str, scope: str, path: str = Query(...)) -> dict[str, Any]:
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
    async def delete_knowledge_document(user: str, scope: str, path: str = Query(...)) -> dict[str, Any]:
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
    async def skill_document(user: str, category: str, name: str = Query(...)) -> dict[str, Any]:
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
    async def delete_skill(user: str, category: str, name: str = Query(...)) -> dict[str, Any]:
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
    async def download_skill(user: str, category: str, name: str = Query(...)) -> Response:
        filename, data = backend.skill_archive(user, category, name)
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
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
    async def kemo_provider_models(user: str, response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "no-store"
        return await asyncio.to_thread(backend.kemo_provider_models, user)

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
    async def patch_preferences(user: str, body: PreferencesBody) -> dict[str, Any]:
        return backend.patch_preferences(user, body.model_dump(exclude_none=True))

    @app.post("/api/runs/{run_id}/guidance")
    async def submit_guidance(run_id: str, body: GuidanceBody) -> dict[str, Any]:
        return backend.submit_guidance(body.user, run_id, body.guidance)

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
