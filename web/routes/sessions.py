"""会话生命周期、历史和会话级运行状态路由。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, Query

from web.schemas import (
    SessionClientBody,
    SessionRenameBody,
    SessionUndoLastRoundBody,
)
from web.service import WebRunService


def register_session_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/sessions")
    async def sessions(
        user: str,
        source: str = Query(default="web"),
        query: str = Query(default=""),
        limit: int = Query(default=50, ge=1, le=100),
        before: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.sessions(
            user,
            source=source,
            query=query,
            limit=limit,
            before=before,
        )

    @app.delete("/api/users/{user}/sessions")
    async def delete_all_sessions(
        user: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.delete_all_sessions(user, source=source)

    @app.get("/api/users/{user}/sessions/active")
    async def active_session(
        user: str,
        client_id: str = Query(default=""),
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.active_session,
            user,
            client_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions")
    async def create_session(
        user: str,
        body: SessionClientBody | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.create_session,
            user,
            body.client_id if body is not None else "",
        )

    @app.post("/api/users/{user}/sessions/{session_id}/close")
    async def close_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
        client_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.close_session,
            user,
            session_id,
            source=source,
            client_id=client_id,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/summary/retry")
    async def retry_session_summary(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.retry_session_summary,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/lease")
    async def session_lease(
        user: str,
        session_id: str,
        body: SessionClientBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.session_lease(user, session_id, body.client_id, source=source)

    @app.post("/api/users/{user}/sessions/{session_id}/lease/release")
    async def release_session_lease(
        user: str,
        session_id: str,
        body: SessionClientBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.release_session_lease(
            user,
            session_id,
            body.client_id,
            source=source,
        )

    @app.patch("/api/users/{user}/sessions/{session_id}")
    async def rename_session(
        user: str,
        session_id: str,
        body: SessionRenameBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return backend.rename_session(user, session_id, body.title, source=source)

    @app.delete("/api/users/{user}/sessions/{session_id}")
    async def delete_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
        client_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.delete_session(
            user,
            session_id,
            source=source,
            client_id=client_id,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/compress")
    async def compress_session(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.compress_session,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/extract-memory")
    async def extract_session_memory(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.extract_session_memory,
            user,
            session_id,
            source=source,
        )

    @app.post("/api/users/{user}/sessions/{session_id}/undo-last-round")
    async def undo_last_round(
        user: str,
        session_id: str,
        body: SessionUndoLastRoundBody,
        source: str = Query(default="web"),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.undo_last_round,
            user,
            session_id,
            body.expected_round,
            body.prompt,
            source=source,
        )

    @app.get("/api/users/{user}/sessions/{session_id}/history")
    async def history(
        user: str,
        session_id: str,
        source: str = Query(default="web"),
        limit: int | None = Query(default=None, ge=1, le=100),
        before: int | None = Query(default=None, ge=1),
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"source": source}
        if limit is not None:
            options["limit"] = limit
        if before is not None:
            options["before"] = before
        return backend.history(user, session_id, **options)

    @app.get("/api/users/{user}/overview")
    async def overview(
        user: str,
        session_id: str = Query(default=""),
    ) -> dict[str, Any]:
        return backend.overview(user, session_id=session_id)

    @app.get("/api/users/{user}/runtime/status")
    async def runtime_status(
        user: str,
        session_id: str = Query(default=""),
        sections: str = Query(default=""),
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.runtime_status,
            user,
            session_id=session_id,
            sections=sections,
        )
