"""Authentication guard without BaseHTTPMiddleware's streaming receive bridge."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from web.auth import WebAuthenticator, local_unauthenticated_request


class WebAuthMiddleware:
    """Keep the original receive/send channels intact for SSE disconnects."""

    def __init__(self, app: ASGIApp, *, authenticator: WebAuthenticator) -> None:
        self.app = app
        self.authenticator = authenticator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            path = request.url.path
            public = path in {"/api/health", "/api/logo"} or path.startswith("/api/auth/")
            if not public and (path == "/api" or path.startswith("/api/")) and not self.authenticator.config.enabled:
                # Also protect deployments that bypass start_web and invoke
                # create_app directly. No-auth mode is local-only.
                peer = scope.get("client")
                duplicate_proxy_header = any(
                    len(request.headers.getlist(name)) > 1
                    for name in ("x-forwarded-for", "x-real-ip", "forwarded")
                )
                if duplicate_proxy_header or not local_unauthenticated_request(
                    peer[0] if peer else None, request.headers, self.authenticator.config.trusted_proxies,
                ):
                    await JSONResponse(status_code=403, content={"error": {
                        "code": "local_auth_required",
                        "message": "仅允许已确认的本机访问；代理或远程访问请启用实例认证",
                        "status": 403,
                    }})(scope, receive, send)
                    return
            if (
                (path == "/api" or path.startswith("/api/"))
                and not public
                and self.authenticator.config.enabled
                and not self.authenticator.is_authenticated(request.session)
            ):
                response = JSONResponse(
                    status_code=401,
                    content={"error": {
                        "code": "authentication_required",
                        "message": "需要先完成 Web 认证",
                        "status": 401,
                    }},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
