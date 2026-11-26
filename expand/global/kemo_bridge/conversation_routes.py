"""Device conversation, replay and internal service health routes."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Path as FastAPIPath

if TYPE_CHECKING:
    from bridge_auth import BranchRoutes
    from app import (
        APP_SOURCE as APP_SOURCE,
        ChatRequest as ChatRequest,
        Depends as Depends,
        EVENTS as EVENTS,
        GuidanceRequest as GuidanceRequest,
        HTTPException as HTTPException,
        INSTANCE_ID as INSTANCE_ID,
        PEERS as PEERS,
        PeerIdentity as PeerIdentity,
        Query as Query,
        RUNS as RUNS,
        SERVICE_ID as SERVICE_ID,
        SERVICE_NAME as SERVICE_NAME,
        SESSION_ID_MAX_LENGTH as SESSION_ID_MAX_LENGTH,
        StreamingResponse as StreamingResponse,
        UPSTREAM as UPSTREAM,
        UndoLastRoundRequest as UndoLastRoundRequest,
        VERSION as VERSION,
        _run_sse as _run_sse,
        asyncio as asyncio,
        os as os,
        public_event as public_event,
        quote as quote,
        require_session as require_session,
        run_in_threadpool as run_in_threadpool,
        time as time,
        uuid as uuid,
    )


def register_conversation_routes(routes: BranchRoutes, context: dict[str, Any]) -> None:
    globals().update(context)

    async def stream(peer, run_id, session_id, *, after: int, endpoint: str):
        logged = False
        async for event in RUNS.stream(peer.username, run_id, after=after, session_id=session_id):
            if event is None:
                yield b": kemo-keep-alive\n\n"
                continue
            yield _run_sse(event)
            if not logged and (value := public_event(event.data)) and value.get("content"):
                await run_in_threadpool(PEERS.audit, "message_sent", peer, endpoint=endpoint)
                logged = True

    @routes.app.get("/internal/health")
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


    @routes.route("GET", "/health", "status")
    async def device_health() -> dict[str, str]:
        return {"status": "ok", "service": SERVICE_ID, "version": VERSION}


    @routes.route("POST", "/chat", "chat")
    async def chat(body: ChatRequest, session: PeerIdentity = Depends(require_session)) -> StreamingResponse:
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
        await run_in_threadpool(PEERS.audit, "message_received", session, endpoint="/v1/chat")

        headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        headers["X-Kemo-Run-Id"] = str(record["run_id"])
        return StreamingResponse(
            stream(session, str(record["run_id"]), requested_session, after=0, endpoint="/v1/chat"),
            media_type="text/event-stream", headers=headers,
        )


    @routes.route("GET", "/runs/active", "run_status")
    async def active_runs(
        client_id: str = Query("", max_length=128),
        session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
        session: PeerIdentity = Depends(require_session),
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


    @routes.route("GET", "/runs/{run_id}/snapshot", "run_status")
    async def run_snapshot(
        run_id: str,
        after: int = Query(0, ge=0),
        session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
        session: PeerIdentity = Depends(require_session),
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


    @routes.route("GET", "/runs/{run_id}/stream", "run_status")
    async def resume_run_stream(
        run_id: str,
        after: int = Query(0, ge=0),
        session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
        session: PeerIdentity = Depends(require_session),
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

        return StreamingResponse(
            stream(session, run_id, requested_session, after=after, endpoint="/v1/runs/{run_id}/stream"),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Kemo-Run-Id": run_id,
            },
        )


    @routes.route("POST", "/guidance", "chat")
    async def guidance(body: GuidanceRequest, session: PeerIdentity = Depends(require_session)) -> Any:
        try:
            scope = RUNS.scope(session.username, body.run_id)
        except KeyError as exc:
            raise HTTPException(404, "run_not_found") from exc
        requested_session = str(body.session_id or "").strip()
        stored_session = str(scope.get("session_id") or "").strip()
        if not requested_session or not stored_session or requested_session != stored_session:
            raise HTTPException(404, "run_not_found")
        result = await UPSTREAM.request_json(
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
        await run_in_threadpool(PEERS.audit, "message_received", session, endpoint="/v1/guidance")
        return result


    @routes.route("POST", "/runs/{run_id}/cancel", "chat")
    async def cancel_run(
        run_id: str,
        session_id: str = Query("", max_length=SESSION_ID_MAX_LENGTH),
        session: PeerIdentity = Depends(require_session),
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


    @routes.route("GET", "/conversations", "chat")
    async def conversations(
        source: str = Query("app"), query: str = Query(""), limit: int = Query(50, ge=1, le=100), before: str = Query(""),
        session: PeerIdentity = Depends(require_session),
    ) -> Any:
        params = {"source": APP_SOURCE, "query": query, "limit": limit}
        if before:
            params["before"] = before
        return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/sessions", params=params)


    @routes.route("GET", "/conversations/active", "chat")
    async def conversation_active(
        client_id: str = Query("", max_length=128),
        session: PeerIdentity = Depends(require_session),
    ) -> Any:
        return await UPSTREAM.request_json(
            "GET",
            f"/api/users/{quote(session.username, safe='')}/sessions/active",
            params={"source": APP_SOURCE, "client_id": client_id},
        )


    @routes.route("DELETE", "/conversations", "chat")
    async def conversations_delete_all(session: PeerIdentity = Depends(require_session)) -> Any:
        result = await UPSTREAM.request_json(
            "DELETE",
            f"/api/users/{quote(session.username, safe='')}/sessions",
            params={"source": APP_SOURCE},
        )
        RUNS.delete_user(session.username)
        return result


    @routes.route("GET", "/conversations/{session_id}/messages", "chat")
    async def conversation_messages(session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH), source: str = Query("app"), limit: int = Query(100, ge=1, le=100), before: int | None = Query(None), session: PeerIdentity = Depends(require_session)) -> Any:
        params: dict[str, Any] = {"source": APP_SOURCE, "limit": limit}
        if before is not None:
            params["before"] = before
        return await UPSTREAM.request_json("GET", f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/history", params=params)


    @routes.route("DELETE", "/conversations/{session_id}", "chat")
    async def conversation_delete(
        session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
        client_id: str = Query("", max_length=128),
        session: PeerIdentity = Depends(require_session),
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


    @routes.route("POST", "/conversations/{session_id}/close", "chat")
    async def conversation_close(
        session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
        client_id: str = Query("", max_length=128),
        session: PeerIdentity = Depends(require_session),
    ) -> Any:
        params = {"source": APP_SOURCE}
        if client_id:
            params["client_id"] = client_id
        return await UPSTREAM.request_json(
            "POST",
            f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/close",
            params=params,
        )


    @routes.route("POST", "/conversations/{session_id}/compress", "chat")
    async def conversation_compress(session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH), session: PeerIdentity = Depends(require_session)) -> Any:
        return await UPSTREAM.request_json(
            "POST",
            f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/compress",
            params={"source": APP_SOURCE},
        )


    @routes.route("POST", "/conversations/{session_id}/undo-last-round", "chat")
    async def conversation_undo_last_round(
        body: UndoLastRoundRequest,
        session_id: str = FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH),
        session: PeerIdentity = Depends(require_session),
    ) -> Any:
        return await UPSTREAM.request_json(
            "POST",
            f"/api/users/{quote(session.username, safe='')}/sessions/{quote(session_id, safe='')}/undo-last-round",
            params={"source": APP_SOURCE},
            json_body=body.model_dump(),
        )
