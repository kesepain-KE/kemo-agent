"""Kemo Android App 专用 FastAPI 桥接层。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Path as FastAPIPath, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from auth import (
    Session,
    SessionManager,
    SlidingWindowLimiter,
    UserStore,
    resolve_client_ip,
    token_ok,
    trusted_proxy_networks,
)
from events import EventHub
from device_commands import DeviceCommandStore
from run_broker import RunBroker, RunStore, StoredEvent
from upstream import UpstreamClient, UpstreamError

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"
CONNECTION_STATE_PATH = BASE_DIR / "_connections.json"
DEVICE_COMMAND_PATH = BASE_DIR / "_device_commands.json"
RUN_STORE_PATH = BASE_DIR / "_app_runs.sqlite3"
VERSION = "1.1.5"
SERVICE_ID = "kemo_app"
SERVICE_NAME = "kemo app 桥接服务"
APP_SOURCE = "app"
SESSION_ID_MAX_LENGTH = 128
INSTANCE_ID = str(os.environ.get("KEMO_APP_INSTANCE_ID") or "").strip()


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return config


CONFIG = load_config()
TRUSTED_PROXIES = trusted_proxy_networks(CONFIG.get("trusted_proxies", []))


def _config_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = CONFIG.get(name, default)
    if isinstance(value, bool):
        return default
    try:
        rendered = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, rendered))


USERS = UserStore(BASE_DIR / str(CONFIG.get("users_path", "users.json")), int(CONFIG.get("pbkdf2_iterations", 310000)))
SESSIONS = SessionManager(str(CONFIG.get("session_secret", "")), int(CONFIG.get("session_ttl_seconds", 7200)))
LIMITER = SlidingWindowLimiter()
UPSTREAM = UpstreamClient(CONFIG)
EVENTS = EventHub(UPSTREAM, float(CONFIG.get("poll_interval", 5)), CONNECTION_STATE_PATH)
DEVICE_COMMANDS = DeviceCommandStore(DEVICE_COMMAND_PATH)
RUNS = RunBroker(
    UPSTREAM,
    RunStore(
        RUN_STORE_PATH,
        retention_seconds=_config_int(
            "run_replay_retention_seconds",
            7 * 24 * 60 * 60,
            minimum=60,
            maximum=365 * 24 * 60 * 60,
        ),
        max_terminal_runs_per_user=_config_int(
            "run_replay_max_terminal_per_user",
            500,
            minimum=1,
            maximum=100_000,
        ),
    ),
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("kemo_app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    EVENTS.start()
    try:
        yield
    finally:
        await RUNS.stop()
        await EVENTS.stop()
        await UPSTREAM.close()


app = FastAPI(title=SERVICE_NAME, version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


class UserLogin(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=4096)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=SESSION_ID_MAX_LENGTH)
    prompt: str = ""
    content: list[dict[str, Any]] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list)
    run_id: str = ""
    plan_id: str = ""
    client_id: str = ""
    # This remains a compatibility input for older App builds. The framework
    # now owns reasoning selection through provider.reasoning_effort and Kemo
    # model capabilities, so /v1/chat must not impose the legacy fixed five
    # levels or override the account configuration for one request.
    reasoning_effort: str = Field(default="", max_length=64)


class UndoLastRoundRequest(BaseModel):
    expected_round: int = Field(ge=1)
    prompt: str = Field(min_length=1)


class GuidanceRequest(BaseModel):
    run_id: str
    session_id: str = Field(min_length=1, max_length=SESSION_ID_MAX_LENGTH)
    guidance: str = ""
    guidance_id: str = ""
    uploaded_files: list[str] = Field(default_factory=list)


class WhitelistRequest(BaseModel):
    kind: str
    name: str
    enabled: bool
    scope: str = "global"


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)


class InternalDeviceCommand(BaseModel):
    user: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    command: dict[str, Any]


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for", ""), TRUSTED_PROXIES)


def _normalized_device_id(value: str) -> str:
    normalized = "".join(character if character.isalnum() or character in "-_.:" else "-" for character in str(value or "").strip())
    return normalized[:128] or "unknown"


def _run_sse(event: StoredEvent) -> bytes:
    return f"id: {event.event_id}\ndata: {event.data}\n\n".encode("utf-8")


def _rate_limit(key: str, maximum: int) -> None:
    allowed, retry = LIMITER.allow(key, maximum)
    if not allowed:
        raise HTTPException(429, "rate_limited", headers={"Retry-After": str(retry)})


def require_device(authorization: str = Header(default="")) -> None:
    if not token_ok(authorization, CONFIG):
        raise HTTPException(401, "device_unauthorized")


def require_session(
    request: Request,
    _: None = Depends(require_device),
    x_kemo_session: str = Header(default=""),
) -> Session:
    session = SESSIONS.verify(x_kemo_session)
    if session is None:
        raise HTTPException(401, "session_unauthorized")
    _rate_limit(f"business:{x_kemo_session}", int(CONFIG.get("business_rate_limit_per_minute", 60)))
    return session


@app.exception_handler(UpstreamError)
async def upstream_error(_: Request, exc: UpstreamError) -> JSONResponse:
    status = exc.status if 400 <= exc.status < 600 else 502
    return JSONResponse(status_code=status, content={"error": "upstream_error", "message": str(exc), "detail": exc.body})


@app.exception_handler(httpx.HTTPError)
async def transport_error(_: Request, exc: httpx.HTTPError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "upstream_unavailable", "message": type(exc).__name__})


@app.get("/v1/health")
async def health() -> dict[str, Any]:
    upstream: dict[str, Any] | None = None
    try:
        upstream = await asyncio.wait_for(UPSTREAM.health(), timeout=1)
    except Exception:
        pass
    connections = EVENTS.connection_snapshot()
    return {
        "status": "ok",
        "service": SERVICE_ID,
        "display_name": SERVICE_NAME,
        "version": VERSION,
        "instance_id": INSTANCE_ID,
        "process_pid": os.getpid(),
        "upstream": "online" if upstream else "offline",
        "websocket_connections": connections["websocket_connections"],
        "connected_devices": connections["connected_devices"],
        "ts": int(time.time()),
    }


@app.post("/v1/auth/device")
async def auth_device(request: Request, authorization: str = Header(default="")) -> dict[str, Any]:
    _rate_limit(f"auth:{_client_ip(request)}", int(CONFIG.get("auth_rate_limit_per_minute", 5)))
    if not token_ok(authorization, CONFIG):
        raise HTTPException(401, "device_unauthorized")
    return {"ok": True, "device": "verified"}


@app.post("/v1/auth/user")
async def auth_user(body: UserLogin, request: Request, authorization: str = Header(default="")) -> dict[str, Any]:
    _rate_limit(f"auth:{_client_ip(request)}", int(CONFIG.get("auth_rate_limit_per_minute", 5)))
    if not token_ok(authorization, CONFIG):
        raise HTTPException(401, "device_unauthorized")
    if not USERS.verify(body.username, body.password):
        raise HTTPException(401, "invalid_credentials")
    token, expires_at = SESSIONS.issue(body.username)
    return {"ok": True, "username": body.username, "session_token": token, "expires_at": expires_at}


@app.post("/v1/auth/logout")
async def auth_logout(x_kemo_session: str = Header(default=""), _: None = Depends(require_device)) -> dict[str, bool]:
    SESSIONS.revoke(x_kemo_session)
    return {"ok": True}


@app.post("/v1/chat")
async def chat(body: ChatRequest, session: Session = Depends(require_session)) -> StreamingResponse:
    requested_session = str(body.session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    # Older App clients did not send a run id.  Keep that compatibility
    # contract while ensuring the broker and the SSE subscriber use the same
    # durable identifier.
    run_id = str(body.run_id or "").strip() or f"run_{uuid.uuid4().hex}"
    payload = {
        **body.model_dump(exclude={"reasoning_effort"}),
        "user": session.username,
        "source": APP_SOURCE,
        "session_id": requested_session,
        "run_id": run_id,
    }
    try:
        record = await RUNS.start(session.username, payload)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def generate() -> AsyncIterator[bytes]:
        # The subscriber may disappear at any point.  RUNS owns the upstream
        # stream independently, so generator cancellation only detaches this
        # phone and never cancels the framework run.
        async for event in RUNS.stream(
            session.username,
            str(record["run_id"]),
            after=0,
            session_id=requested_session,
        ):
            if event is None:
                yield b": kemo-keep-alive\n\n"
            else:
                yield _run_sse(event)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    headers["X-Kemo-Run-Id"] = str(record["run_id"])
    return StreamingResponse(generate(), media_type="text/event-stream", headers=headers)


@app.get("/v1/runs/active")
async def active_runs(
    client_id: str = Query("", max_length=128),
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    requested_client = str(client_id or "").strip()
    requested_session = str(session_id or "").strip()
    if not requested_session and not requested_client:
        raise HTTPException(400, "client_id_or_session_id_required")
    return {
        "runs": RUNS.active(
            session.username,
            client_id=requested_client,
            session_id=requested_session,
        ),
    }


@app.get("/v1/runs/{run_id}/snapshot")
async def run_snapshot(
    run_id: str,
    after: int = Query(0, ge=0),
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> dict[str, Any]:
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    try:
        return RUNS.snapshot(
            session.username,
            run_id,
            after,
            session_id=requested_session,
        )
    except KeyError as exc:
        raise HTTPException(404, "run_not_found") from exc


@app.get("/v1/runs/{run_id}/stream")
async def resume_run_stream(
    run_id: str,
    after: int = Query(0, ge=0),
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> StreamingResponse:
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    try:
        RUNS.snapshot(
            session.username,
            run_id,
            after,
            session_id=requested_session,
        )
    except KeyError as exc:
        raise HTTPException(404, "run_not_found") from exc

    async def generate() -> AsyncIterator[bytes]:
        async for event in RUNS.stream(
            session.username,
            run_id,
            after=after,
            session_id=requested_session,
        ):
            if event is None:
                yield b": kemo-keep-alive\n\n"
            else:
                yield _run_sse(event)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Kemo-Run-Id": run_id,
        },
    )


@app.post("/v1/guidance")
async def guidance(body: GuidanceRequest, session: Session = Depends(require_session)) -> Any:
    try:
        scope = RUNS.scope(session.username, body.run_id)
    except KeyError as exc:
        raise HTTPException(404, "run_not_found") from exc
    requested_session = str(body.session_id or "").strip()
    stored_session = str(scope.get("session_id") or "").strip()
    if not requested_session or not stored_session or requested_session != stored_session:
        raise HTTPException(404, "run_not_found")
    return await UPSTREAM.request_json(
        "POST",
        f"/api/runs/{quote(body.run_id, safe='')}/guidance",
        json_body={
            "user": session.username,
            "source": APP_SOURCE,
            "session_id": requested_session,
            "guidance": body.guidance,
            "guidance_id": body.guidance_id,
            "uploaded_files": body.uploaded_files,
        },
    )


@app.post("/v1/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> Any:
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    try:
        scope = RUNS.scope(session.username, run_id)
    except KeyError as exc:
        raise HTTPException(404, "run_not_found") from exc
    stored_session = str(scope.get("session_id") or "").strip()
    if not stored_session or stored_session != requested_session:
        raise HTTPException(404, "run_not_found")
    result = await UPSTREAM.request_json(
        "POST",
        f"/api/runs/{quote(run_id, safe='')}/cancel",
        json_body={
            "user": session.username,
            "source": APP_SOURCE,
            "session_id": requested_session,
        },
    )
    try:
        RUNS.mark_cancelling(session.username, run_id)
    except KeyError:
        pass
    return result


@app.get("/v1/conversations")
async def conversations(
    source: str = Query("app"), query: str = Query(""), limit: int = Query(50, ge=1, le=100), before: str = Query(""),
    session: Session = Depends(require_session),
) -> Any:
    params = {"source": APP_SOURCE, "query": query, "limit": limit}
    if before:
        params["before"] = before
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/sessions", params=params)


@app.get("/v1/conversations/active")
async def conversation_active(
    client_id: str = Query("", max_length=128),
    session: Session = Depends(require_session),
) -> Any:
    return await UPSTREAM.request_json(
        "GET",
        f"/api/users/{quote(session.username, safe='')}/sessions/active",
        params={"source": APP_SOURCE, "client_id": client_id},
    )


@app.delete("/v1/conversations")
async def conversations_delete_all(session: Session = Depends(require_session)) -> Any:
    result = await UPSTREAM.request_json(
        "DELETE",
        f"/api/users/{quote(session.username, safe='')}/sessions",
        params={"source": APP_SOURCE},
    )
    RUNS.delete_user(session.username)
    return result


@app.get("/v1/conversations/{session_id}/messages")
async def conversation_messages(session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH), source: str = Query("app"), limit: int = Query(100, ge=1, le=100), before: int | None = Query(None), session: Session = Depends(require_session)) -> Any:
    params: dict[str, Any] = {"source": APP_SOURCE, "limit": limit}
    if before is not None:
        params["before"] = before
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/history", params=params)


@app.delete("/v1/conversations/{session_id}")
async def conversation_delete(
    session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
    client_id: str = Query("", max_length=128),
    session: Session = Depends(require_session),
) -> Any:
    params = {"source": APP_SOURCE}
    if client_id:
        params["client_id"] = client_id
    result = await UPSTREAM.request_json(
        "DELETE",
        f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}",
        params=params,
    )
    RUNS.delete_session(session.username, session_id)
    return result


@app.post("/v1/conversations/{session_id}/close")
async def conversation_close(
    session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
    client_id: str = Query("", max_length=128),
    session: Session = Depends(require_session),
) -> Any:
    params = {"source": APP_SOURCE}
    if client_id:
        params["client_id"] = client_id
    return await UPSTREAM.request_json(
        "POST",
        f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/close",
        params=params,
    )


@app.post("/v1/conversations/{session_id}/compress")
async def conversation_compress(session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH), session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json(
        "POST",
        f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/compress",
        params={"source": APP_SOURCE},
    )


@app.post("/v1/conversations/{session_id}/undo-last-round")
async def conversation_undo_last_round(
    body: UndoLastRoundRequest,
    session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> Any:
    return await UPSTREAM.request_json(
        "POST",
        f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/undo-last-round",
        params={"source": APP_SOURCE},
        json_body=body.model_dump(),
    )


@app.get("/v1/avatar")
async def avatar(session: Session = Depends(require_session)) -> Response:
    response = await UPSTREAM.request(
        "GET",
        f"/api/users/{quote(session.username, safe='')}/avatar",
    )
    if response.status_code == 204:
        return Response(status_code=204)
    return Response(
        content=response.content,
        media_type=response.headers.get("content-type", "application/octet-stream"),
        headers={"Cache-Control": "private, max-age=60"},
    )


@app.get("/v1/version")
async def version_info(_: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("GET", "/api/version")


@app.post("/v1/upload")
async def upload(file: UploadFile = File(...), path: str = Query("", max_length=500), session: Session = Depends(require_session)) -> Any:
    filename = Path(str(file.filename or "upload.bin")).name.replace("\x00", "").strip()
    if not filename or filename in {".", ".."}:
        filename = "upload.bin"
    filename = filename[:240]
    directory = str(path or "").replace("\\", "/").strip("/")
    target_path = f"{directory}/{filename}" if directory else filename
    data = await file.read(80 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 80 * 1024 * 1024:
        raise HTTPException(413, "file_too_large")
    files = {"file": (filename, data, file.content_type or "application/octet-stream")}
    response = await UPSTREAM.request("POST", f"/api/users/{quote(session.username, safe='')}/files/file_upload/upload", params={"path": target_path}, files=files)
    return UPSTREAM._json(response)


@app.get("/v1/task_plans")
async def task_plans(
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> Any:
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    data = await _tasks(session.username, session_id=requested_session)
    return _select(data, "plans", "task_plans", default=data)


@app.post("/v1/task_plans/{plan_id}/{action}")
async def task_plan_action(
    plan_id: str,
    action: str,
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: Session = Depends(require_session),
) -> Any:
    if action not in {"approve", "pause", "resume", "abort"}:
        raise HTTPException(400, "unsupported_action")
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    return await UPSTREAM.request_json(
        "POST",
        f"/api/users/{quote(session.username, safe='')}/tasks/plans/{quote(plan_id, safe='')}/actions/{action}",
        params={"source": APP_SOURCE, "session_id": requested_session},
    )


@app.get("/v1/cron")
async def cron(session: Session = Depends(require_session)) -> Any:
    data = await _tasks(session.username)
    return _select(data, "cron_tasks", "crons", "cron", "scheduled", default=[])


@app.post("/v1/cron")
async def create_cron(body: dict[str, Any], session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("POST", f"/api/users/{quote(session.username, safe='')}/tasks/crons", json_body=body)


@app.put("/v1/cron/{task_id}")
async def update_cron(task_id: str, body: dict[str, Any], session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("PUT", f"/api/users/{quote(session.username, safe='')}/tasks/crons/{quote(task_id, safe='')}", json_body=body)


@app.delete("/v1/cron/{task_id}")
async def delete_cron(task_id: str, session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("DELETE", f"/api/users/{quote(session.username, safe='')}/tasks/crons/{quote(task_id, safe='')}")


@app.get("/v1/status")
async def status(
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    client_id: str = Query("", max_length=128),
    session: Session = Depends(require_session),
) -> Any:
    user = quote(session.username, safe="")
    resolved_session_id = str(session_id or "").strip()
    # An explicitly selected conversation is authoritative.  ``client_id``
    # may only resolve the active conversation when the caller did not send
    # a session id, otherwise status for session A could silently become B.
    if client_id and not resolved_session_id:
        active = await UPSTREAM.request_json(
            "GET",
            f"/api/users/{user}/sessions/active",
            params={"source": APP_SOURCE, "client_id": client_id},
        )
        if isinstance(active, dict):
            active_session = active.get("session")
            if isinstance(active_session, dict):
                active_id = str(active_session.get("session_id") or "").strip()
                if active_id:
                    resolved_session_id = active_id
    if not resolved_session_id:
        raise HTTPException(400, "session_id_required")
    params = {"source": APP_SOURCE, "session_id": resolved_session_id}
    health_value, overview, runtime = await asyncio.gather(
        UPSTREAM.health(),
        UPSTREAM.request_json("GET", f"/api/users/{user}/overview", params=params),
        UPSTREAM.request_json("GET", f"/api/users/{user}/runtime/status", params=params),
    )
    return {
        "health": health_value,
        "overview": overview,
        "runtime": runtime,
        "resolved_session_id": resolved_session_id,
    }


@app.get("/v1/expands")
async def expands(session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/expand")


def _injected_data_only(value: Any) -> str | None:
    """Extract the data section from the exact prompt injection text."""
    if not isinstance(value, str) or not value.strip():
        return None
    marker = "## 数据采集"
    start = value.find(marker)
    body = value[start + len(marker):] if start >= 0 else value
    end_positions = [
        position
        for heading in ("\n## 操控能力", "\n## 操作能力", "\n## 操作层")
        if (position := body.find(heading)) >= 0
    ]
    if end_positions:
        body = body[:min(end_positions)]
    return body.strip() or None


def _configured_provider_type(value: Any) -> str:
    root = value.get("config", value) if isinstance(value, dict) else {}
    provider = root.get("provider", {}) if isinstance(root, dict) else {}
    return str(provider.get("type") or "").strip().casefold() if isinstance(provider, dict) else ""


@app.get("/v1/expands/data")
async def expands_data(session: Session = Depends(require_session)) -> Any:
    """Return a stable, mobile-friendly view of every expand and its latest snapshot."""
    payload = await expands(session)
    groups = payload.get("expands", []) if isinstance(payload, dict) else []
    normalized: list[dict[str, Any]] = []
    module_count = 0
    with_data = 0
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        scope = str(group.get("scope") or "global")
        items: list[dict[str, Any]] = []
        raw_items = group.get("items", [])
        for item in raw_items if isinstance(raw_items, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("module_name") or item.get("id") or "")
            # The App mirrors only the exact text injected into the system
            # prompt. Collected snapshots, runtime state and operation-layer
            # documents are intentionally not used as display fallbacks.
            snapshot = _injected_data_only(item.get("injected_markdown"))
            enabled = item.get("active_for_main_agent")
            if enabled is None:
                enabled = item.get("whitelisted")
            if enabled is None:
                enabled = item.get("enabled", item.get("active"))
            status = item.get("status") or item.get("health") or item.get("state") or item.get("input_health")
            if not status and enabled is not None:
                status = "ready" if bool(enabled) else "inactive"
            module_count += 1
            if snapshot not in (None, "", {}, []):
                with_data += 1
            items.append({
                "id": str(item.get("id") or name),
                "name": name,
                "display_name": str(item.get("display_name") or item.get("name") or name),
                "description": str(item.get("description") or ""),
                "updated": item.get("updated") or item.get("updated_at") or item.get("recent_update") or item.get("last_update"),
                "status": status,
                "enabled": enabled,
                "data": snapshot,
            })
        normalized.append({"scope": scope, "items": items})
    return {
        "summary": {"scopes": len(normalized), "modules": module_count, "with_data": with_data},
        "expands": normalized,
    }


@app.get("/v1/senses")
async def senses(session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/sense")


@app.get("/v1/expands/{scope}/{name}/data")
async def expand_data(scope: str, name: str, session: Session = Depends(require_session)) -> Any:
    data = await expands(session)
    match = _find_named(data, name, scope)
    if match is None:
        raise HTTPException(404, "module_not_found")
    snapshot = {key: match[key] for key in ("input_data", "data", "summary", "runtime", "health") if key in match}
    if not snapshot:
        raise HTTPException(501, "upstream_has_no_expand_data_endpoint")
    return snapshot


@app.put("/v1/whitelist")
async def whitelist(body: WhitelistRequest, session: Session = Depends(require_session)) -> Any:
    user = quote(session.username, safe="")
    if body.kind == "expand":
        path = f"/api/users/{user}/expand/{quote(body.scope, safe='')}/{quote(body.name, safe='')}/enabled"
    elif body.kind == "sense":
        path = f"/api/users/{user}/sense/{quote(body.name, safe='')}/enabled"
    else:
        raise HTTPException(501, "unknown_whitelist_target")
    return await UPSTREAM.request_json("PATCH", path, json_body={"enabled": body.enabled})


def _upstream_file_scope(scope: str) -> str:
    """Translate the App's friendly upload scope to the web API contract."""
    normalized = scope.strip().lower()
    if normalized == "upload":
        return "file_upload"
    if normalized == "download":
        return normalized
    raise HTTPException(400, "unsupported_file_scope")


@app.get("/v1/files")
async def files(scope: str = Query("download"), path: str = Query(""), search: str = Query(""), page: int = Query(1), page_size: int = Query(50), session: Session = Depends(require_session)) -> Any:
    upstream_scope = _upstream_file_scope(scope)
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/files/{upstream_scope}", params={"path": path, "search": search, "page": page, "page_size": page_size})


@app.get("/v1/files/download")
async def file_download(scope: str = Query("download"), path: str = Query(...), session: Session = Depends(require_session)) -> StreamingResponse:
    upstream_scope = _upstream_file_scope(scope)
    response = await UPSTREAM.open_stream("GET", f"/api/users/{quote(session.username, safe='')}/files/{upstream_scope}/download", params={"path": path})
    async def generate() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
    return StreamingResponse(generate(), media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(Path(path).name)}"})


@app.delete("/v1/files")
async def file_delete(scope: str = Query("download"), path: str = Query(...), session: Session = Depends(require_session)) -> Any:
    upstream_scope = _upstream_file_scope(scope)
    return await UPSTREAM.request_json("DELETE", f"/api/users/{quote(session.username, safe='')}/files/{upstream_scope}", params={"path": path})


@app.get("/v1/knowledge")
async def knowledge(session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/knowledge")


@app.get("/v1/knowledge/search")
async def knowledge_search(q: str = Query(..., min_length=1, max_length=200), session: Session = Depends(require_session)) -> Any:
    data = await knowledge(session)
    hits: list[dict[str, Any]] = []
    _search_json(data, q.casefold(), hits)
    return {"query": q, "items": hits[:100]}


@app.get("/v1/models")
async def models(refresh: bool = Query(False), session: Session = Depends(require_session)) -> Any:
    user = quote(session.username, safe="")
    config = await UPSTREAM.request_json("GET", f"/api/users/{user}/config/full")
    if _configured_provider_type(config) != "kemo":
        raise HTTPException(404, "model_catalog_unavailable_for_chat_protocol")
    catalog = await UPSTREAM.request_json(
        "GET",
        f"/api/users/{user}/provider/models",
        params={"refresh": str(refresh).lower()},
    )
    if isinstance(catalog, dict):
        return {**catalog, "protocol": "kemo"}
    return {"protocol": "kemo", "data": catalog}


@app.get("/v1/models/capabilities")
async def model_capabilities(
    model: str = Query(..., min_length=1, max_length=256),
    refresh: bool = Query(False),
    session: Session = Depends(require_session),
) -> Any:
    user = quote(session.username, safe="")
    return await UPSTREAM.request_json(
        "GET",
        f"/api/users/{user}/provider/model-capabilities",
        params={"model": model, "refresh": str(refresh).lower()},
    )


@app.put("/v1/provider/model")
async def provider_model(body: ModelRequest, session: Session = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("PATCH", f"/api/users/{quote(session.username, safe='')}/config", json_body={"changes": {"provider": {"model": body.model}}})


@app.get("/v1/config")
async def config_mirror(session: Session = Depends(require_session)) -> Any:
    data = await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/config/full")
    return _redact(data)


@app.patch("/v1/config")
async def config_patch(body: dict[str, Any], session: Session = Depends(require_session)) -> Any:
    changes = body.get("changes", body)
    if not isinstance(changes, dict):
        raise HTTPException(400, "invalid_config_changes")
    return await UPSTREAM.request_json(
        "PATCH",
        f"/api/users/{quote(session.username, safe='')}/config",
        json_body={"changes": changes},
    )


@app.post("/internal/device-command")
async def internal_device_command(
    body: InternalDeviceCommand,
    request: Request,
    x_kemo_internal: str = Header(default=""),
) -> dict[str, Any]:
    # This endpoint exists only so the isolated Expand control subprocess can
    # wake the already-running bridge immediately. It is never an App API.
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "internal_loopback_only")
    if not x_kemo_internal or x_kemo_internal != str(CONFIG.get("session_secret") or ""):
        raise HTTPException(401, "internal_unauthorized")
    delivered = await EVENTS.publish_to_device(
        body.user,
        _normalized_device_id(body.device_id),
        "device.command",
        body.command,
    )
    return {"ok": True, "delivered": delivered}


@app.websocket("/v1/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    authorization = websocket.headers.get("authorization", "")
    session_token = websocket.headers.get("x-kemo-session", "")
    if not token_ok(authorization, CONFIG):
        await websocket.close(code=4401)
        return
    session = SESSIONS.verify(session_token)
    if session is None:
        await websocket.close(code=4403)
        return
    device_id = _normalized_device_id(
        websocket.headers.get("x-kemo-device-id", "")
    )
    subscribed_session_id = str(
        websocket.query_params.get("session_id") or ""
    ).strip()
    if not subscribed_session_id or len(subscribed_session_id) > SESSION_ID_MAX_LENGTH:
        # An unbound socket is a user-wide notification channel, not a valid
        # conversation stream.  Refuse it so a stale/legacy client cannot
        # accidentally observe another conversation after reconnecting.
        await websocket.close(code=4400, reason="session_id_required")
        return
    await websocket.accept()
    queue = EVENTS.subscribe(session.username, device_id, subscribed_session_id)
    await websocket.send_json({"type": "connected", "ts": int(time.time()), "session_id": subscribed_session_id, "data": {"user": session.username, "device_id": device_id}})
    for command in DEVICE_COMMANDS.pending_for(session.username, device_id):
        await websocket.send_json({"type": "device.command", "ts": int(time.time()), "data": command})
    try:
        while True:
            receive_task = asyncio.create_task(websocket.receive_text())
            event_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait({receive_task, event_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if not done:
                await websocket.send_json({"type": "ping", "ts": int(time.time()), "data": {}})
                continue
            if event_task in done:
                await websocket.send_json(event_task.result())
            if receive_task in done:
                raw = receive_task.result()
                try:
                    message = json.loads(raw)
                except ValueError:
                    message = {}
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "ts": int(time.time()), "data": {}})
                elif message.get("type") in {"device.command.ack", "device.command.result"}:
                    data = message.get("data") if isinstance(message.get("data"), dict) else {}
                    command_id = str(data.get("command_id") or "").strip()
                    status = str(data.get("status") or "").strip()
                    detail = data.get("detail") if isinstance(data.get("detail"), dict) else {}
                    if command_id and status:
                        try:
                            DEVICE_COMMANDS.update(
                                command_id,
                                username=session.username,
                                device_id=device_id,
                                status=status,
                                detail=detail,
                            )
                        except (RuntimeError, TimeoutError, ValueError) as exc:
                            LOGGER.warning(
                                "ignored invalid device command acknowledgement: %s",
                                type(exc).__name__,
                            )
                elif message.get("type") == "device.capabilities":
                    EVENTS.update_capabilities(queue, message.get("data"))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        EVENTS.unsubscribe(session.username, queue)


async def _tasks(username: str, *, session_id: str = "") -> Any:
    params: dict[str, str] = {"source": APP_SOURCE}
    requested_session = str(session_id or "").strip()
    if requested_session:
        params["session_id"] = requested_session
    return await UPSTREAM.request_json(
        "GET",
        f"/api/users/{quote(username, safe='')}/tasks",
        params=params,
    )


def _select(value: Any, *keys: str, default: Any) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        for nested in value.values():
            selected = _select(nested, *keys, default=None)
            if selected is not None:
                return selected
    return default


def _find_named(value: Any, name: str, scope: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if str(value.get("name", "")) == name and str(value.get("scope", scope)) == scope:
            return value
        for nested in value.values():
            found = _find_named(nested, name, scope)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_named(nested, name, scope)
            if found:
                return found
    return None


def _search_json(value: Any, query: str, hits: list[dict[str, Any]], path: str = "") -> None:
    if isinstance(value, dict):
        text = " ".join(str(item) for item in value.values() if isinstance(item, (str, int, float)))
        if query in text.casefold():
            hits.append({"path": path, "item": value})
        for key, nested in value.items():
            if isinstance(nested, (dict, list)):
                _search_json(nested, query, hits, f"{path}/{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _search_json(nested, query, hits, f"{path}/{index}")


def _redact(value: Any) -> Any:
    sensitive = ("token", "password", "secret", "api_key", "cookie", "credential")
    if isinstance(value, dict):
        return {key: "***" if any(part in str(key).casefold() for part in sensitive) else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
