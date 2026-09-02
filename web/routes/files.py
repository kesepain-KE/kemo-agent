"""用户文件、临时文件与头像路由。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response

from web.schemas import DeleteManyBody, TextBody
from web.service import (
    AVATAR_MAX_BYTES,
    COMPLETION_SOUND_MAX_BYTES,
    FAILURE_SOUND_MAX_BYTES,
    FILE_UPLOAD_MAX_BYTES,
    WebRunService,
)
from web.routes.file_registration import register_file_routes as _register_file_routes_impl


def _preview_response(target: Any, media_type: str) -> FileResponse:
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=300",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'",
        },
    )


def _thumbnail_response(target: Any) -> FileResponse:
    return FileResponse(
        target,
        media_type="image/webp",
        filename=target.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'",
        },
    )


def _artifact_response(target: Any, media_type: str) -> FileResponse:
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; img-src 'self'; media-src 'self'",
        },
    )


def register_file_routes(app: FastAPI, backend: WebRunService) -> None:
    """Compatibility entry point delegated to file route registration."""

    return _register_file_routes_impl(app, backend)
