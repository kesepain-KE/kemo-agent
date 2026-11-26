"""The deliberately small external-agent conversation surface."""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from typing import Any

import httpx
from fastapi import Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from bridge_auth import BranchRoutes, require_session
from bridge_config import (
    MAX_EVENT_ID, PEER_ACK_HEADER, PEER_CHAT_PATH, PEER_FRAME_BYTES, PEER_RUN_HEADER, PEER_RUN_PATTERN, VERSION,
    peer_sse,
)
from peer_store import PeerIdentity, PeerStore
from run_broker import TERMINAL_STATUSES, RunBroker, TerminalStatus, _failure, _terminal_data, _terminal_status
from upstream import UpstreamClient, UpstreamError

_PAGE_EVENTS = 128


class PeerChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1)
    run_id: str = Field(default="", max_length=128, pattern=r"^[A-Za-z0-9_-]*$")


def conversation_key(peer: PeerIdentity, key: str) -> str:
    return f"kb_{peer.peer_id.replace('-', '')}_{hashlib.sha256(key.encode()).hexdigest()[:32]}"


def _chat_keys(peer: PeerIdentity, session_id: str, external_id: str) -> tuple[str, str]:
    return conversation_key(peer, session_id.strip()), "run_" + conversation_key(peer, external_id)


def public_event(raw: str) -> dict | None:
    terminal = _terminal_status(raw)
    if terminal is not None:
        result: dict[str, Any] = {"type": "done", "status": terminal.status}
        if terminal.status in {"failed", "cancelled", "interrupted"}:
            result.update(code=terminal.code, transient=terminal.transient, phase=terminal.phase)
        return result
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if isinstance(value, dict) and value.get("type") == "text_delta" and isinstance(value.get("content"), str):
        return {"type": "text_delta", "content": value["content"]}
    return None


def _sse_response(events, external_id: str) -> StreamingResponse:
    return StreamingResponse(events, media_type="text/event-stream", headers={
        PEER_RUN_HEADER: external_id, "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


def _read_page(
    runs: RunBroker, peer_id: str, run_id: str, session_id: str, after: int,
) -> tuple[list[tuple[bytes, bool]], dict]:
    store = runs.store
    record = store.get(peer_id, run_id)
    if record["session_id"] != session_id:
        raise KeyError(run_id)
    if after > record["last_event_id"]:
        raise HTTPException(400, "invalid_after_event")
    size, cursor, frames, last_value = 0, after, [], None
    with store.iter_events_after(peer_id, run_id, after, limit=_PAGE_EVENTS) as events:
        for event in events:
            value = public_event(event.data)
            if value is not None:
                frame = peer_sse(value, event_id=event.event_id)
                if size + len(frame) > PEER_FRAME_BYTES:
                    if cursor == after:
                        # Older journals may predate splitting. Do not advance past that text.
                        raise HTTPException(413, "peer_event_too_large")
                    break
                frames.append((frame, value["type"] == "text_delta" and bool(value["content"])))
                size += len(frame)
            cursor, last_value = event.event_id, value
            if value is not None and value["type"] == "done":
                return frames, {}
    page = {"type": "page", "after_event": cursor}
    if record["status"] in TERMINAL_STATUSES and cursor >= record["last_event_id"]:
        if cursor == after:
            latest = store.events_after(peer_id, run_id, cursor - 1, limit=1)
            last_value = public_event(latest[0].data) if latest else None
        terminal = last_value
        if terminal is None or terminal["type"] != "done":
            # A restart can mark a run interrupted without appending a terminal event.
            status = record["status"]
            if status in {"completed", "cancelled"}:
                outcome = TerminalStatus(status, phase="stream")
            else:
                code = "upstream_stream_incomplete" if status == "interrupted" else "bridge_internal"
                outcome = replace(_failure(code, "stream"), status=status)
            terminal = public_event(_terminal_data(outcome, {"type": "done"}))
        page["terminal"] = terminal
    return frames, page


def register_agent_routes(routes: BranchRoutes, peers: PeerStore, runs: RunBroker, upstream: UpstreamClient) -> None:
    @routes.route("GET", "/status", "status")
    async def status(peer: PeerIdentity = Depends(require_session)):
        try:
            online = bool(await upstream.health())
        except (httpx.HTTPError, UpstreamError):
            online = False
        return {"online": online, "busy": runs.is_busy(peer.peer_id), "version": VERSION}

    @routes.route("POST", "/chat", "chat")
    async def chat(
        body: PeerChat,
        peer: PeerIdentity = Depends(require_session),
        ack: str = Header(default="", alias=PEER_ACK_HEADER),
    ):
        if not body.session_id.strip() or not body.prompt.strip():
            raise HTTPException(400, "session_and_prompt_required")
        external_id = body.run_id.strip() or uuid.uuid4().hex
        session_id, run_id = _chat_keys(peer, body.session_id, external_id)
        record = await runs.start(peer.peer_id, {
            "user": peer.bound_user, "source": "app", "session_id": session_id,
            "run_id": run_id, "prompt": body.prompt,
        }, peer_run=True)
        await run_in_threadpool(peers.audit, "message_received", peer, endpoint=PEER_CHAT_PATH, run_id=run_id)

        async def generate():
            if ack == "1":
                if await run_in_threadpool(peers.is_current, peer):
                    yield peer_sse({"type": "accepted"})
                return
            logged = False
            async for event in runs.stream(peer.peer_id, record["run_id"], session_id=session_id):
                if not await run_in_threadpool(peers.is_current, peer):
                    return
                if event is None:
                    yield b": kemo-keep-alive\n\n"
                elif (value := public_event(event.data)) is not None:
                    yield peer_sse(value, event_id=event.event_id)
                    if not logged and value["type"] == "text_delta" and value["content"]:
                        await run_in_threadpool(peers.audit, "message_sent", peer, endpoint=PEER_CHAT_PATH, run_id=run_id)
                        logged = True

        return _sse_response(generate(), external_id)

    @routes.route("GET", "/chat", "chat")
    async def replay(
        session_id: str = Query(min_length=1, max_length=128),
        after_event: int = Query(default=0, ge=0, le=MAX_EVENT_ID),
        external_id: str = Header(alias=PEER_RUN_HEADER, max_length=128, pattern=PEER_RUN_PATTERN),
        peer: PeerIdentity = Depends(require_session),
    ):
        if not session_id.strip():
            raise HTTPException(400, "session_id_required")
        session_key, run_id = _chat_keys(peer, session_id, external_id)
        try:
            frames, page = await run_in_threadpool(
                _read_page, runs, peer.peer_id, run_id, session_key, after_event,
            )
        except KeyError:
            # A query must never submit work, including after journal expiry.
            raise HTTPException(404, "run_not_found", headers={PEER_RUN_HEADER: external_id}) from None
        except HTTPException as exc:
            if exc.status_code == 413:
                raise HTTPException(413, exc.detail, headers={PEER_RUN_HEADER: external_id}) from None
            raise

        async def generate():
            if not await run_in_threadpool(peers.is_current, peer):
                return
            logged = False
            for frame, has_text in frames:
                yield frame
                if not logged and has_text:
                    await run_in_threadpool(peers.audit, "message_sent", peer, endpoint=PEER_CHAT_PATH, run_id=run_id)
                    logged = True
            if page:
                yield peer_sse(page)

        return _sse_response(generate(), external_id)
