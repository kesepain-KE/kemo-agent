"""One authentication and capability gate for the bridge's HTTP and WebSocket routes."""
from __future__ import annotations

import hmac

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.routing import compile_path

from bridge_config import INTERNAL_HEADER
from capabilities import capabilities_for
from peer_store import BridgeError, PeerIdentity, PeerStore

BRANCHES = {"/v1": "device", "/peer/v1": "agent"}


def request_path(scope: dict) -> str:
    path = scope["path"]
    base = scope.get("root_path", "").rstrip("/")
    return path[len(base):] if base and path.startswith(base + "/") else path


def within(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def require_session(request: Request) -> PeerIdentity:
    return request.state.peer


class BranchRoutes:
    """Register the route and its permission together. Unregistered methods are denied."""

    def __init__(self, app, prefix: str, gates: dict) -> None:
        self.app, self.prefix, self.gates = app, prefix, gates

    def _gate(self, method: str, path: str, capability: str) -> str:
        path = self.prefix + path
        channel = BRANCHES[self.prefix]
        if capability not in capabilities_for(channel):
            raise ValueError(f"Unavailable capability {capability} on {path}")
        pattern, _, _ = compile_path(path)
        entries = self.gates.setdefault((channel, method), [])
        if any(template == path for _, template, _ in entries):
            raise ValueError(f"Duplicate route {method} {path}")
        entries.append((pattern, path, capability))
        return path

    def route(self, method: str, path: str, capability: str):
        return self.app.api_route(self._gate(method, path, capability), methods=[method])

    def websocket(self, path: str, capability: str):
        return self.app.websocket(self._gate("WEBSOCKET", path, capability))


class BridgeAuthentication:
    def __init__(self, app, *, peers: PeerStore, gates: dict, internal_secret) -> None:
        self.app, self.peers, self.gates = app, peers, gates
        self.internal_secret = internal_secret

    @staticmethod
    async def reject(scope, receive, send, status: int, code: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 4000 + status, "reason": code})
        else:
            await JSONResponse({"error": code}, status_code=status)(scope, receive, send)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        path = request_path(scope)
        headers = Headers(scope=scope)
        if within(path, "/internal"):
            supplied = headers.get(INTERNAL_HEADER, "")
            secret = self.internal_secret()
            if not supplied or not secret or not hmac.compare_digest(supplied.encode(), secret.encode()):
                await self.reject(scope, receive, send, 403, "internal_unauthorized")
                return
            await self.app(scope, receive, send)
            return

        prefix = next((prefix for prefix in BRANCHES if within(path, prefix)), None)
        if prefix is None:
            await self.app(scope, receive, send)
            return
        channel = BRANCHES[prefix]
        method = scope.get("method", "WEBSOCKET")
        # Audit only registered templates, never arbitrary path segments, query strings or headers.
        template, capability = next(
            ((template, cap) for pattern, template, cap in self.gates.get((channel, method), ())
             if pattern.fullmatch(path)),
            (prefix + "/*", None),
        )
        try:
            peer = await run_in_threadpool(
                self.peers.authenticate, headers.get("authorization", ""),
                headers.get("x-kemo-peer-id", ""), endpoint=template,
            )
        except BridgeError as error:
            await self.reject(scope, receive, send, error.status, error.code)
            return

        async def identified(message):
            if message["type"] in {"http.response.start", "websocket.accept"}:
                message.setdefault("headers", [])
                response = MutableHeaders(scope=message)
                response["X-Kemo-Peer-Id"] = peer.peer_id
                response["X-Kemo-Bound-User"] = peer.bound_user
                response["X-Kemo-Channel"] = peer.channel
                response["X-Kemo-Capabilities"] = ",".join(peer.public()["capabilities"])
            await send(message)

        if peer.channel != channel or capability not in capabilities_for(peer.channel):
            await run_in_threadpool(
                self.peers.audit, "capability_not_granted", peer, endpoint=template,
                detail={"method": method, "required": capability, "branch": channel},
            )
            await self.reject(scope, receive, identified, 403, "capability_not_granted")
            return
        scope.setdefault("state", {})["peer"] = peer
        await self.app(scope, receive, identified)
