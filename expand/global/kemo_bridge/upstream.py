"""kemo-agent Web API 的带会话代理客户端。"""

from __future__ import annotations

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx


_DEFAULT_UPSTREAM = "http://127.0.0.1:1357"
_RUNTIME_WEB_ENDPOINT_ENV = "KEMO_AGENT_WEB_BASE_URL"
_LOCAL_UPSTREAM_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _valid_runtime_endpoint(value: object) -> str:
    """Accept a plain local or remote HTTP(S) framework endpoint."""

    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return ""
    if "\\" in candidate or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in candidate):
        return ""
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            return ""
        port = parsed.port
    except ValueError:
        return ""
    if port is not None and not 1 <= port <= 65535:
        return ""
    return candidate


def _is_local_upstream(value: object) -> bool:
    candidate = str(value or "").strip()
    if not candidate:
        return True
    try:
        parsed = urlsplit(candidate)
        return parsed.hostname.casefold() in _LOCAL_UPSTREAM_HOSTS if parsed.hostname else False
    except (AttributeError, ValueError):
        return False


def resolve_upstream_base_url(config: dict[str, Any]) -> str:
    """Resolve the bridge's framework URL without overriding external gateways.

    ``start_web.py`` publishes the endpoint selected for this process through
    ``KEMO_AGENT_WEB_BASE_URL``.  It is authoritative for an empty or local
    configured upstream, which keeps the bridge aligned with fallback ports.
    A configured non-loopback URL remains authoritative for deployment
    topologies that intentionally use a separate gateway.
    """

    configured = str(config.get("upstream") or "").strip().rstrip("/")
    runtime = _valid_runtime_endpoint(os.getenv(_RUNTIME_WEB_ENDPOINT_ENV))
    if runtime and _is_local_upstream(configured):
        return runtime
    return configured or _DEFAULT_UPSTREAM


class UpstreamError(RuntimeError):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str = "upstream_http_error",
        phase: str = "request",
        attempt: int = 1,
        response: httpx.Response | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.phase = phase
        self.attempt = attempt
        self.response_status = response.status_code if response is not None else None
        self.body_bytes: int | None = None
        self.body_sha256: str | None = None
        if response is not None:
            try:
                body = response.content
            except httpx.ResponseNotRead:
                return
            self.body_bytes = len(body)
            self.body_sha256 = hashlib.sha256(body).hexdigest()


def stream_transport_failure(exc: BaseException, *, phase: str, attempt: int) -> UpstreamError | None:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.ProxyError)):
        code, phase = "upstream_unreachable", "connect"
    elif isinstance(exc, httpx.TimeoutException):
        code = "upstream_timeout"
    elif isinstance(exc, httpx.DecodingError):
        code = "upstream_invalid_payload"
    elif isinstance(exc, (httpx.NetworkError, httpx.ProtocolError)):
        code = "upstream_stream_incomplete" if phase == "stream" else "upstream_unreachable"
    else:
        return None
    return UpstreamError(502, code, code=code, phase=phase, attempt=attempt)


class UpstreamClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.base_url = resolve_upstream_base_url(config)
        self.token = str(config.get("upstream_token", ""))
        self.username = str(config.get("upstream_username", ""))
        self.password = str(config.get("upstream_password", ""))
        # Keep the normal REST timeout bounded.  Long-running chat streams are
        # opted into separately in ``open_stream(..., sse=True)`` below so a
        # slow tool invocation does not look like a failed request.
        self.request_timeout = float(config.get("request_timeout", 30))
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.request_timeout, connect=5),
            follow_redirects=False,
        )
        self._sse_timeout = httpx.Timeout(self.request_timeout, connect=5, read=None)
        self._auth_lock = asyncio.Lock()

    async def close(self) -> None:
        await self.client.aclose()

    async def health(self) -> dict[str, Any]:
        response = await self.client.get("/api/health")
        return self._json(response)

    async def ensure_authenticated(self, force: bool = False, *, attempt: int = 1) -> None:
        async with self._auth_lock:
            if not force:
                try:
                    status = await self.client.get("/api/auth/status")
                    if status.status_code == 200:
                        body = status.json()
                        if isinstance(body, dict) and body.get("authenticated"):
                            return
                except (httpx.HTTPError, ValueError):
                    pass
            if self.token:
                response = await self.client.post("/api/auth/token", json={"token": self.token})
                self._check_auth_response(response, attempt)
            if self.username or self.password:
                response = await self.client.post("/api/auth/login", json={"username": self.username, "password": self.password})
                self._check_auth_response(response, attempt)
            status = await self.client.get("/api/auth/status")
            body = self._json(status, phase="auth", attempt=attempt)
            if not isinstance(body, dict):
                raise UpstreamError(502, "upstream auth status must be an object",
                                    code="upstream_invalid_payload", phase="auth", attempt=attempt, response=status)
            if body.get("enabled") and not body.get("authenticated"):
                configured = self.token or self.username or self.password
                code = "upstream_auth_rejected" if configured else "upstream_auth_missing"
                raise UpstreamError(401, "upstream authentication not established",
                                    code=code, phase="auth", attempt=attempt, response=status)

    @staticmethod
    def _check_auth_response(response: httpx.Response, attempt: int) -> None:
        if response.status_code >= 400:
            code = "upstream_auth_rejected" if response.status_code == 401 else "upstream_http_error"
            raise UpstreamError(response.status_code, "upstream authentication request failed",
                                code=code, phase="auth", attempt=attempt, response=response)

    async def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None, files: Any = None) -> httpx.Response:
        await self.ensure_authenticated()
        response = await self.client.request(method, path, params=params, json=json_body, files=files)
        if response.status_code == 401:
            await self.ensure_authenticated(force=True)
            response = await self.client.request(method, path, params=params, json=json_body, files=files)
        return response

    async def request_json(self, method: str, path: str, *, params: dict[str, Any] | None = None, json_body: Any = None) -> Any:
        response = await self.request(method, path, params=params, json_body=json_body)
        return self._json(response)

    @asynccontextmanager
    async def stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        sse: bool = False,
    ) -> AsyncIterator[httpx.Response]:
        response = await self.open_stream(method, path, json_body=json_body, params=params, sse=sse)
        try:
            yield response
        finally:
            await response.aclose()

    async def open_stream(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
        sse: bool = False,
    ) -> httpx.Response:
        """Open a streamed response.

        ``sse=True`` is reserved for the chat SSE endpoint.  It disables only
        the per-read timeout (while retaining connect/write/pool timeouts),
        allowing upstream tools to remain quiet for longer than the regular
        REST timeout.  File downloads and all other callers keep the normal
        client timeout by default.
        """
        attempt = 1
        response = await self._open_stream_attempt(method, path, json_body, params, sse=sse, attempt=attempt)
        if response.status_code == 401:
            await response.aclose()
            attempt = 2
            response = await self._open_stream_attempt(method, path, json_body, params, sse=sse, attempt=attempt)
        if response.status_code >= 400:
            try:
                if sse:
                    await asyncio.wait_for(response.aread(), timeout=self.request_timeout)
                else:
                    await response.aread()
            except (httpx.RequestError, asyncio.TimeoutError):
                if not sse:
                    raise
                # The HTTP failure is known even if its body is unavailable.
            finally:
                await response.aclose()
            raise UpstreamError(response.status_code, "upstream stream request failed", attempt=attempt, response=response)
        if sse:
            response.extensions["kemo_bridge_attempt"] = attempt
        return response

    async def _open_stream_attempt(
        self, method: str, path: str, json_body: Any, params: dict[str, Any] | None, *, sse: bool, attempt: int,
    ) -> httpx.Response:
        phase = "auth"
        try:
            await self.ensure_authenticated(force=attempt > 1, attempt=attempt)
            phase = "request"
            timeout = self._sse_timeout if sse else httpx.USE_CLIENT_DEFAULT
            request = self.client.build_request(method, path, json=json_body, params=params, timeout=timeout)
            return await self.client.send(request, stream=True)
        except httpx.RequestError as exc:
            failure = stream_transport_failure(exc, phase=phase, attempt=attempt) if sse else None
            if failure is None:
                raise
            raise failure from exc

    def _json(self, response: httpx.Response, *, phase: str = "request", attempt: int = 1) -> Any:
        if response.status_code >= 400:
            raise UpstreamError(response.status_code, "upstream request failed",
                                phase=phase, attempt=attempt, response=response)
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(502, "upstream returned invalid JSON", code="upstream_invalid_payload",
                                phase=phase, attempt=attempt, response=response) from exc
