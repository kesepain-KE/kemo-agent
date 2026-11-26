"""kemo-bridge HTTP, SSE and WebSocket entry point."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from agent_routes import public_event, register_agent_routes
from artifact_routes import register_artifact_routes
from artifact_store import ArtifactStore
from bridge_auth import BranchRoutes, BridgeAuthentication, require_session
from bridge_config import BASE_DIR, SERVICE_ID, SERVICE_NAME, USERS_DIR, VERSION, public_url
from bridge_events import EventHub
from device_commands import DeviceCommandStore
from payload_tools import (
    search_json as _search_json,
    select as _select,
)
from run_broker import RunBroker, RunStore, StoredEvent
from peer_store import BridgeError, PeerIdentity, PeerStore
from upstream import UpstreamClient, UpstreamError
from workbench import config_projection, local_config, register_workbench

CONFIG_PATH = BASE_DIR / "config.json"
CONNECTION_STATE_PATH = BASE_DIR / "_connections.json"
DEVICE_COMMAND_PATH = BASE_DIR / "_device_commands.json"
RUN_STORE_PATH = BASE_DIR / "_bridge_runs.sqlite3"
APP_SOURCE = "app"
SESSION_ID_MAX_LENGTH = 128
INSTANCE_ID = str(os.environ.get("KEMO_BRIDGE_INSTANCE_ID") or "").strip()


def load_config() -> dict[str, Any]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return config


CONFIG = load_config()


def runtime_public_url() -> str | None:
    return public_url(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))



def _config_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = CONFIG.get(name, default)
    if isinstance(value, bool):
        return default
    try:
        rendered = int(value)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, rendered))


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
LOGGER = logging.getLogger(SERVICE_ID)
PEERS = PeerStore(BASE_DIR / "_bridge_peers.sqlite3", USERS_DIR)
PEERS.local_identity()
GRANTS = ArtifactStore(PEERS)


async def _prune_peer_audit() -> None:
    while True:
        more = False
        try:
            more = await run_in_threadpool(PEERS.prune_audit, RUNS.store.path)
        except sqlite3.Error:
            LOGGER.warning("Peer audit retention could not run; will retry.")
        await asyncio.sleep(0.25 if more else 60)


@asynccontextmanager
async def lifespan(_: FastAPI):
    EVENTS.start()
    audit_pruner = asyncio.create_task(_prune_peer_audit())
    try:
        yield
    finally:
        audit_pruner.cancel()
        await asyncio.gather(audit_pruner, return_exceptions=True)
        await RUNS.stop()
        await EVENTS.stop()
        await UPSTREAM.close()


app = FastAPI(title=SERVICE_NAME, version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
GATES: dict = {}
device = BranchRoutes(app, "/v1", GATES)
agent = BranchRoutes(app, "/peer/v1", GATES)
app.add_middleware(BridgeAuthentication, peers=PEERS, gates=GATES,
                   internal_secret=lambda: str(CONFIG.get("session_secret") or ""))


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


class DeviceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any]
    ttl_seconds: int = Field(default=300, ge=30, le=86400)


class ModelRequest(BaseModel):
    model: str = Field(min_length=1, max_length=300)


class InternalDeviceCommand(BaseModel):
    user: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1, max_length=128)
    command: dict[str, Any]


def _run_sse(event: StoredEvent) -> bytes:
    return f"id: {event.event_id}\ndata: {event.data}\n\n".encode("utf-8")


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Validation errors may embed the original request body (including new secrets).
    return JSONResponse({"error": "invalid_request"}, status_code=422)


@app.exception_handler(BridgeError)
async def bridge_error(_: Request, exc: BridgeError) -> JSONResponse:
    return JSONResponse({"error": exc.code}, status_code=exc.status)


@app.exception_handler(UpstreamError)
async def upstream_error(_: Request, exc: UpstreamError) -> JSONResponse:
    status = exc.status if 400 <= exc.status < 600 else 502
    return JSONResponse(status_code=status, content={"error": "upstream_error"})


@app.exception_handler(httpx.HTTPError)
async def transport_error(_: Request, exc: httpx.HTTPError) -> JSONResponse:
    return JSONResponse(status_code=502, content={"error": "upstream_unavailable", "message": type(exc).__name__})


from conversation_routes import register_conversation_routes

register_conversation_routes(device, globals())
register_agent_routes(agent, PEERS, RUNS, UPSTREAM)
register_workbench(device, PEERS, UPSTREAM)
register_artifact_routes(app, device, agent, GRANTS, read_public_url=runtime_public_url,
                         upload_extensions=lambda: CONFIG.get("bridge", {}).get("upload_extensions"))

@device.route("GET", "/avatar", "status")
async def avatar(session: PeerIdentity = Depends(require_session)) -> Response:
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


@device.route("GET", "/version", "status")
@agent.route("GET", "/version", "status")
def version_info(peer: PeerIdentity = Depends(require_session)) -> Any:
    result = {"service": SERVICE_ID, "version": VERSION, "public_url": runtime_public_url()}
    if peer.channel == "agent":
        result["instance_uuid"] = PEERS.local_identity()["instance_uuid"]
    return result


@device.route("GET", "/task_plans", "task_plan")
async def task_plans(
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: PeerIdentity = Depends(require_session),
) -> Any:
    requested_session = str(session_id or "").strip()
    if not requested_session:
        raise HTTPException(400, "session_id_required")
    data = await _tasks(session.username, session_id=requested_session)
    return _select(data, "plans", "task_plans", default=data)


@device.route("POST", "/task_plans/{plan_id}/{action}", "task_plan")
async def task_plan_action(
    plan_id: str,
    action: str,
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    session: PeerIdentity = Depends(require_session),
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


@device.route("GET", "/cron", "cron")
async def cron(session: PeerIdentity = Depends(require_session)) -> Any:
    data = await _tasks(session.username)
    return _select(data, "cron_tasks", "crons", "cron", "scheduled", default=[])


@device.route("POST", "/cron", "cron")
async def create_cron(body: dict[str, Any], session: PeerIdentity = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("POST", f"/api/users/{quote(session.username, safe='')}/tasks/crons", json_body=body)


@device.route("PUT", "/cron/{task_id}", "cron")
async def update_cron(task_id: str, body: dict[str, Any], session: PeerIdentity = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("PUT", f"/api/users/{quote(session.username, safe='')}/tasks/crons/{quote(task_id, safe='')}", json_body=body)


@device.route("DELETE", "/cron/{task_id}", "cron")
async def delete_cron(task_id: str, session: PeerIdentity = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("DELETE", f"/api/users/{quote(session.username, safe='')}/tasks/crons/{quote(task_id, safe='')}")


@device.route("GET", "/status", "status")
async def status(
    session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
    client_id: str = Query("", max_length=128),
    session: PeerIdentity = Depends(require_session),
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


def _configured_provider_type(value: Any) -> str:
    root = value.get("config", value) if isinstance(value, dict) else {}
    provider = root.get("provider", {}) if isinstance(root, dict) else {}
    return str(provider.get("type") or "").strip().casefold() if isinstance(provider, dict) else ""


async def _expand_projection(session: PeerIdentity, *, include_data: bool) -> dict[str, Any]:
    payload = await UPSTREAM.request_json(
        "GET", f"/api/users/{quote(session.username, safe='')}/expand", params={"include_files": False},
    )
    groups = payload.get("expands", []) if isinstance(payload, dict) else []
    result = []
    for group in groups:
        items = []
        for item in group.get("items", []):
            projection = {
                key: item[key] for key in
                ("id", "name", "display_name", "description", "active_for_main_agent", "updated_at")
                if key in item
            }
            if include_data:
                # The upstream explicitly supplies the data-only injected fragment.
                data = item.get("data_injected_markdown")
                projection["data"] = (data.strip() or None) if isinstance(data, str) else None
            items.append(projection)
        result.append({"scope": group.get("scope"), "items": items})
    return {"expands": result}


@device.route("GET", "/expands", "expand_read")
async def expands(session: PeerIdentity = Depends(require_session)) -> Any:
    return await _expand_projection(session, include_data=False)


@device.route("GET", "/expands/data", "expand_read")
async def expands_data(session: PeerIdentity = Depends(require_session)) -> Any:
    result = await _expand_projection(session, include_data=True)
    items = [item for group in result["expands"] for item in group["items"]]
    return {"summary": {
        "scopes": len(result["expands"]), "modules": len(items),
        "with_data": sum(item["data"] is not None for item in items),
    }, **result}


@device.route("GET", "/expands/{scope}/{name}/data", "expand_read")
async def expand_data(scope: str, name: str, session: PeerIdentity = Depends(require_session)) -> Any:
    result = await _expand_projection(session, include_data=True)
    for group in result["expands"]:
        if group["scope"] == scope:
            for item in group["items"]:
                if item.get("name") == name:
                    return {"data": item["data"]}
    raise HTTPException(404, "module_not_found")


def _upstream_file_scope(scope: str) -> str:
    """Translate the App's friendly upload scope to the web API contract."""
    normalized = scope.strip().lower()
    if normalized == "upload":
        return "file_upload"
    if normalized == "download":
        return normalized
    raise HTTPException(400, "unsupported_file_scope")


@device.route("GET", "/files", "file_list")
async def files(scope: str = Query("download"), path: str = Query(""), search: str = Query(""), page: int = Query(1), page_size: int = Query(50), session: PeerIdentity = Depends(require_session)) -> Any:
    upstream_scope = _upstream_file_scope(scope)
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/files/{upstream_scope}", params={"path": path, "search": search, "page": page, "page_size": page_size})


@device.route("GET", "/knowledge", "knowledge_ref")
async def knowledge(session: PeerIdentity = Depends(require_session)) -> Any:
    return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/knowledge")


@device.route("GET", "/knowledge/search", "knowledge_ref")
async def knowledge_search(q: str = Query(..., min_length=1, max_length=200), session: PeerIdentity = Depends(require_session)) -> Any:
    data = await knowledge(session)
    hits: list[dict[str, Any]] = []
    _search_json(data, q.casefold(), hits)
    return {"query": q, "items": hits[:100]}


@device.route("GET", "/models", "config")
async def models(refresh: bool = Query(False), session: PeerIdentity = Depends(require_session)) -> Any:
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


@device.route("GET", "/models/capabilities", "config")
async def model_capabilities(
    model: str = Query(..., min_length=1, max_length=256),
    refresh: bool = Query(False),
    session: PeerIdentity = Depends(require_session),
) -> Any:
    user = quote(session.username, safe="")
    return await UPSTREAM.request_json(
        "GET",
        f"/api/users/{user}/provider/model-capabilities",
        params={"model": model, "refresh": str(refresh).lower()},
    )


@device.route("PUT", "/provider/model", "config")
async def provider_model(body: ModelRequest, session: PeerIdentity = Depends(require_session)) -> Any:
    payload = await UPSTREAM.request_json(
        "PATCH", f"/api/users/{quote(session.username, safe='')}/config",
        json_body={"changes": {"provider": {"model": body.model}}},
    )
    actual = await run_in_threadpool(local_config, PEERS.user_directory(session.bound_user))
    return config_projection(payload, actual)


@device.route("POST", "/device-commands", "device_action")
async def submit_device_command(body: DeviceAction, peer: PeerIdentity = Depends(require_session)) -> dict:
    try:
        command = await run_in_threadpool(
            DEVICE_COMMANDS.enqueue, username=peer.bound_user, device_id=peer.peer_id,
            action=body.action, arguments=body.arguments, ttl_seconds=body.ttl_seconds,
        )
    except ValueError as exc:
        raise BridgeError(400, "invalid_device_action") from exc
    delivered = await EVENTS.publish_to_device(peer.bound_user, peer.peer_id, "device.command", command)
    await run_in_threadpool(PEERS.audit, "device_action_submit", peer, endpoint="/v1/device-commands",
                            detail={"command_id": command["command_id"], "action": command["action"],
                                    "environment": "system_ui", "delivered": delivered})
    return {"command": command, "delivered": delivered}


@device.route("GET", "/device-commands/{command_id}", "device_action")
async def get_device_command(command_id: str, peer: PeerIdentity = Depends(require_session)) -> dict:
    command = await run_in_threadpool(DEVICE_COMMANDS.get, command_id, username=peer.bound_user)
    if command is None or command["device_id"] != peer.peer_id:
        raise BridgeError(404, "device_command_not_found")
    return command


@app.post("/internal/device-command")
async def internal_device_command(body: InternalDeviceCommand) -> dict[str, Any]:
    command = await run_in_threadpool(
        DEVICE_COMMANDS.get, str(body.command.get("command_id") or ""), username=body.user,
    )
    if command is None or command["device_id"] != body.device_id:
        raise BridgeError(404, "device_command_not_found")
    delivered = await EVENTS.publish_to_device(body.user, body.device_id, "device.command", command)
    return {"ok": True, "delivered": delivered}


@device.websocket("/ws", "run_status")
async def websocket_endpoint(websocket: WebSocket) -> None:
    session: PeerIdentity = websocket.state.peer
    device_id = session.peer_id
    subscribed_session_id = str(
        websocket.query_params.get("session_id") or ""
    ).strip()
    if not subscribed_session_id or len(subscribed_session_id) > SESSION_ID_MAX_LENGTH:
        # An unbound socket is a user-wide notification channel, not a valid
        # conversation stream.  Refuse it so a stale/legacy client cannot
        # accidentally observe another conversation after reconnecting.
        await websocket.close(code=4400, reason="session_id_required")
        return
    lease_client_id = "app_ws_" + hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:24]
    lease_path = (
        f"/api/users/{quote(session.username, safe='')}/sessions/"
        f"{quote(subscribed_session_id, safe='')}/lease"
    )
    try:
        await UPSTREAM.request_json(
            "POST", lease_path, params={"source": APP_SOURCE},
            json_body={"client_id": lease_client_id},
        )
    except UpstreamError:
        await websocket.close(code=4404, reason="conversation_not_active")
        return
    await websocket.accept()
    queue = EVENTS.subscribe(session.username, device_id, subscribed_session_id)
    last_lease_refresh = time.monotonic()
    await websocket.send_json({"type": "connected", "ts": int(time.time()), "session_id": subscribed_session_id, "data": {**session.public(), "device_id": device_id}})
    for command in DEVICE_COMMANDS.pending_for(session.username, device_id):
        await websocket.send_json({"type": "device.command", "ts": int(time.time()), "data": command})
        await run_in_threadpool(PEERS.audit, "message_sent", session, endpoint="/v1/ws")
    try:
        while True:
            if not await run_in_threadpool(PEERS.is_current, session):
                await websocket.close(code=4401, reason="auth_failed")
                return
            if time.monotonic() - last_lease_refresh >= 30:
                await UPSTREAM.request_json(
                    "POST", lease_path, params={"source": APP_SOURCE},
                    json_body={"client_id": lease_client_id},
                )
                last_lease_refresh = time.monotonic()
            receive_task = asyncio.create_task(websocket.receive_text())
            event_task = asyncio.create_task(queue.get())
            done, pending = await asyncio.wait({receive_task, event_task}, timeout=30, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            if not await run_in_threadpool(PEERS.is_current, session):
                await websocket.close(code=4401, reason="auth_failed")
                return
            if not done:
                await websocket.send_json({"type": "ping", "ts": int(time.time()), "data": {}})
                continue
            if event_task in done:
                await websocket.send_json(event_task.result())
                await run_in_threadpool(PEERS.audit, "message_sent", session, endpoint="/v1/ws")
            if receive_task in done:
                raw = receive_task.result()
                try:
                    message = json.loads(raw)
                except ValueError:
                    message = {}
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "ts": int(time.time()), "data": {}})
                elif message.get("type") in {"device.command.ack", "device.command.result"}:
                    data = (checked_651 if isinstance(checked_651 := message.get("data"), dict) else {})
                    command_id = str(data.get("command_id") or "").strip()
                    status = str(data.get("status") or "").strip()
                    detail = (checked_654 if isinstance(checked_654 := data.get("detail"), dict) else {})
                    if command_id and status:
                        try:
                            updated = await run_in_threadpool(
                                DEVICE_COMMANDS.update, command_id,
                                username=session.username,
                                device_id=device_id,
                                status=status,
                                detail=detail,
                            )
                            if updated is not None:
                                await run_in_threadpool(
                                    PEERS.audit, "device_action_ack", session, endpoint="/v1/ws",
                                    detail={"command_id": command_id, "status": updated["status"],
                                            "environment": "system_ui"},
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
        try:
            await UPSTREAM.request_json(
                "POST", lease_path + "/release", params={"source": APP_SOURCE},
                json_body={"client_id": lease_client_id},
            )
        except UpstreamError:
            pass


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
