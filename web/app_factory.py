"""FastAPI application assembly extracted from :mod:`web.app`."""

from __future__ import annotations

def create_app(
    *,
    root: Path | None = None,
    service: WebRunService | None = None,
    auth_config: WebAuthConfig | None = None,
) -> FastAPI:
    """Create the FastAPI application and compose domain route registrars."""

    import importlib
    _app_module = importlib.import_module("web.app")
    Any = _app_module.Any
    AuthFailureLimiter = _app_module.AuthFailureLimiter
    ChatBody = _app_module.ChatBody
    ConflictError = _app_module.ConflictError
    FastAPI = _app_module.FastAPI
    FileResponse = _app_module.FileResponse
    GuidanceBody = _app_module.GuidanceBody
    InvalidRequestError = _app_module.InvalidRequestError
    Iterator = _app_module.Iterator
    JSONResponse = _app_module.JSONResponse
    LoginBody = _app_module.LoginBody
    Path = _app_module.Path
    Request = _app_module.Request
    RequestValidationError = _app_module.RequestValidationError
    RestartBody = _app_module.RestartBody
    RunCancelBody = _app_module.RunCancelBody
    RunEvent = _app_module.RunEvent
    SessionMiddleware = _app_module.SessionMiddleware
    StreamingResponse = _app_module.StreamingResponse
    TERMINAL_EVENTS = _app_module.TERMINAL_EVENTS
    TokenLoginBody = _app_module.TokenLoginBody
    WEB_SESSION_MAX_AGE_SECONDS = _app_module.WEB_SESSION_MAX_AGE_SECONDS
    WebAuthConfig = _app_module.WebAuthConfig
    WebAuthError = _app_module.WebAuthError
    WebAuthenticator = _app_module.WebAuthenticator
    WebRunService = _app_module.WebRunService
    WebServiceError = _app_module.WebServiceError
    _error_body = _app_module._error_body
    _frontend_media_type = _app_module._frontend_media_type
    _safe_internal_message = _app_module._safe_internal_message
    _spawn_restart_helper = _app_module._spawn_restart_helper
    _sse = _app_module._sse
    asyncio = _app_module.asyncio
    project_root = _app_module.project_root
    register_file_routes = _app_module.register_file_routes
    register_identity_routes = _app_module.register_identity_routes
    register_module_routes = _app_module.register_module_routes
    register_session_routes = _app_module.register_session_routes
    register_setting_routes = _app_module.register_setting_routes
    register_task_routes = _app_module.register_task_routes
    resolve_client_ip = _app_module.resolve_client_ip
    threading = _app_module.threading
    # FastAPI resolves nested route annotations against the defining module's
    # globals, not this function's local scope.  Publish the compatibility
    # aliases before registering any routes so Pydantic sees the same models as
    # it did in web.app.
    globals().update(
        {
            name: value
            for name, value in {
                "Any": Any,
                "AuthFailureLimiter": AuthFailureLimiter,
                "ChatBody": ChatBody,
                "ConflictError": ConflictError,
                "FastAPI": FastAPI,
                "FileResponse": FileResponse,
                "GuidanceBody": GuidanceBody,
                "InvalidRequestError": InvalidRequestError,
                "Iterator": Iterator,
                "JSONResponse": JSONResponse,
                "LoginBody": LoginBody,
                "Path": Path,
                "Request": Request,
                "RequestValidationError": RequestValidationError,
                "RestartBody": RestartBody,
                "RunCancelBody": RunCancelBody,
                "RunEvent": RunEvent,
                "SessionMiddleware": SessionMiddleware,
                "StreamingResponse": StreamingResponse,
                "TERMINAL_EVENTS": TERMINAL_EVENTS,
                "TokenLoginBody": TokenLoginBody,
                "WEB_SESSION_MAX_AGE_SECONDS": WEB_SESSION_MAX_AGE_SECONDS,
                "WebAuthConfig": WebAuthConfig,
                "WebAuthError": WebAuthError,
                "WebAuthenticator": WebAuthenticator,
                "WebRunService": WebRunService,
                "WebServiceError": WebServiceError,
                "_error_body": _error_body,
                "_frontend_media_type": _frontend_media_type,
                "_safe_internal_message": _safe_internal_message,
                "_spawn_restart_helper": _spawn_restart_helper,
                "_sse": _sse,
                "asyncio": asyncio,
                "project_root": project_root,
                "register_file_routes": register_file_routes,
                "register_identity_routes": register_identity_routes,
                "register_module_routes": register_module_routes,
                "register_session_routes": register_session_routes,
                "register_setting_routes": register_setting_routes,
                "register_task_routes": register_task_routes,
                "resolve_client_ip": resolve_client_ip,
                "threading": threading,
            }.items()
        }
    )
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
            https_only=configured_auth.cookie_secure,
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
            # Resolve the launcher at request time so tests and embedders can
            # override web.app._spawn_restart_helper after app construction,
            # preserving the original module-global lookup semantics.
            helper = getattr(
                importlib.import_module("web.app"),
                "_spawn_restart_helper",
                _spawn_restart_helper,
            )
            helper_pid = helper(base, body.port)
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
            source=body.source,
            session_id=body.session_id,
            guidance_id=body.guidance_id,
            uploaded_files=body.uploaded_files,
        )

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, body: RunCancelBody) -> dict[str, Any]:
        return backend.cancel_run(
            body.user,
            run_id,
            source=body.source,
            session_id=body.session_id,
        )

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
                        source=body.source,
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
                        source=body.source,
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

