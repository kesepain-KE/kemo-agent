"""Authentication, run, and conversation HTTP routes for the Kemo App bridge."""

from __future__ import annotations

from typing import Any

def register_conversation_routes(app: Any, context: dict[str, Any]) -> None:
    globals().update(context)

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
        agent_user = USERS.agent_user(body.username)
        token, expires_at = SESSIONS.issue(agent_user)
        return {
            "ok": True,
            "username": body.username,
            "agent_user": agent_user,
            "session_token": token,
            "expires_at": expires_at,
        }


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
