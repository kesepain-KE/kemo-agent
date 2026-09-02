"""用于 kemo-agent Web 后端的 FastAPI 应用程序工厂。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.middleware.sessions import SessionMiddleware

from events import RunEvent, TERMINAL_EVENTS
from run.config import project_root
from web.auth import (
    AuthFailureLimiter,
    WEB_SESSION_MAX_AGE_SECONDS,
    WebAuthConfig,
    WebAuthError,
    WebAuthenticator,
    resolve_client_ip,
)
from web.routes.files import register_file_routes
from web.routes.identity import register_identity_routes
from web.routes.modules import register_module_routes
from web.routes.sessions import register_session_routes
from web.routes.settings import register_setting_routes
from web.routes.tasks import register_task_routes
from web.schemas import (
    ChatBody,
    DeleteManyBody,
    GuidanceBody,
    LoginBody,
    LongTaskPreferenceBody,
    MemoryWriteBody,
    PreferencesBody,
    RestartBody,
    RunCancelBody,
    SessionClientBody,
    SessionRenameBody,
    SessionUndoLastRoundBody,
    SkillToggleBody,
    SoulBody,
    TextBody,
    TokenLoginBody,
)
from web.service import ConflictError, InvalidRequestError, WebRunService, WebServiceError
from web.app_factory import create_app as _create_app_impl


__all__ = [
    "ChatBody",
    "DeleteManyBody",
    "GuidanceBody",
    "LoginBody",
    "LongTaskPreferenceBody",
    "MemoryWriteBody",
    "PreferencesBody",
    "RestartBody",
    "RunCancelBody",
    "SessionClientBody",
    "SessionRenameBody",
    "SessionUndoLastRoundBody",
    "SkillToggleBody",
    "SoulBody",
    "TextBody",
    "TokenLoginBody",
    "create_app",
]


def _error_body(code: str, message: str, status: int) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "status": status}}


def _sse(event: RunEvent) -> bytes:
    payload = json.dumps(event.to_dict(), ensure_ascii=False, separators=(",", ":"))
    return f"event: {event.type}\ndata: {payload}\n\n".encode("utf-8")


def _safe_internal_message(_: BaseException) -> str:
    return "Web 服务处理请求失败"


def _frontend_media_type(path: Path) -> str | None:
    return {
        ".css": "text/css",
        ".js": "text/javascript",
        ".json": "application/json",
        ".mjs": "text/javascript",
        ".svg": "image/svg+xml",
        ".wasm": "application/wasm",
    }.get(path.suffix.lower())


def _spawn_restart_helper(base: Path, port: int) -> int:
    command = [
        sys.executable,
        str(base / "restart.py"),
        f"--port={port}",
        f"--parent-pid={os.getpid()}",
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(base),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    return process.pid


def create_app(
    *,
    root: Path | None = None,
    service: WebRunService | None = None,
    auth_config: WebAuthConfig | None = None,
) -> FastAPI:
    """Compatibility entry point delegated to the application factory module."""

    return _create_app_impl(
        root=root,
        service=service,
        auth_config=auth_config,
    )
