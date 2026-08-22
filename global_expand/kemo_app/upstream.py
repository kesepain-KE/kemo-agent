"""kemo-agent Web API 的带会话代理客户端。"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from urllib.parse import urlsplit

import httpx


_DEFAULT_UPSTREAM = "http://127.0.0.1:1357"
_RUNTIME_WEB_ENDPOINT_ENV = "KEMO_AGENT_WEB_BASE_URL"
_LOCAL_UPSTREAM_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _valid_runtime_endpoint(value: object) -> str:
    """Accept only a plain HTTP(S) endpoint published by the local launcher."""

    candidate = str(value or "").strip().rstrip("/")
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        if parsed.username or parsed.password:
            return ""
        if parsed.hostname.casefold() not in _LOCAL_UPSTREAM_HOSTS:
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
    def __init__(self, status: int, message: str, body: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


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

    async def ensure_authenticated(self, force: bool = False) -> None:
        async with self._auth_lock:
            if not force:
                try:
                    status = await self.client.get("/api/auth/status")
                    if status.status_code == 200 and bool(status.json().get("authenticated")):
                        return
                except (httpx.HTTPError, ValueError):
                    pass
            if self.token:
                response = await self.client.post("/api/auth/token", json={"token": self.token})
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code, "upstream token authentication failed", self._safe_body(response))
            if self.username or self.password:
                response = await self.client.post("/api/auth/login", json={"username": self.username, "password": self.password})
                if response.status_code >= 400:
                    raise UpstreamError(response.status_code, "upstream password authentication failed", self._safe_body(response))
            status = await self.client.get("/api/auth/status")
            body = self._json(status)
            if body.get("enabled") and not body.get("authenticated"):
                raise UpstreamError(401, "upstream authentication is required but credentials are not configured", body)

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
        await self.ensure_authenticated()
        if sse:
            request = self.client.build_request(
                method,
                path,
                json=json_body,
                params=params,
                timeout=self._sse_timeout,
            )
        else:
            request = self.client.build_request(method, path, json=json_body, params=params)
        response = await self.client.send(request, stream=True)
        if response.status_code == 401:
            await response.aclose()
            await self.ensure_authenticated(force=True)
            if sse:
                request = self.client.build_request(
                    method,
                    path,
                    json=json_body,
                    params=params,
                    timeout=self._sse_timeout,
                )
            else:
                request = self.client.build_request(method, path, json=json_body, params=params)
            response = await self.client.send(request, stream=True)
        if response.status_code >= 400:
            content = await response.aread()
            await response.aclose()
            raise UpstreamError(response.status_code, "upstream stream request failed", content[:2000].decode("utf-8", "replace"))
        return response

    def _json(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise UpstreamError(response.status_code, "upstream request failed", self._safe_body(response))
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(502, "upstream returned invalid JSON") from exc

    @staticmethod
    def _safe_body(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return response.text[:2000]
