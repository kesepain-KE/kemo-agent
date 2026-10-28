"""Authenticated background APIs; video responses support byte ranges."""

from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from web.service import WebRunService
from web.errors import InvalidRequestError
from web.services.background_media import BACKGROUND_MAX_BYTES

_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}


def register_background_routes(app: FastAPI, backend: WebRunService) -> None:
    @app.get("/api/users/{user}/background/status")
    def background_status(user: str):
        return JSONResponse(backend.background_status(user), headers=_HEADERS)

    @app.post("/api/users/{user}/background")
    async def upload_background(user: str, file: UploadFile = File(...)):
        # Reject nonexistent/unsafe users before reading a large upload.
        try:
            await run_in_threadpool(backend._background_root, user)
            if file.size is not None and file.size > BACKGROUND_MAX_BYTES:
                raise InvalidRequestError("背景文件不能超过 400 MB")
            chunks, size = [], 0
            while chunk := await file.read(min(1024 * 1024, BACKGROUND_MAX_BYTES - size + 1)):
                size += len(chunk)
                if size > BACKGROUND_MAX_BYTES:
                    raise InvalidRequestError("背景文件不能超过 400 MB")
                chunks.append(chunk)
            data = b"".join(chunks)
            del chunks
        finally:
            await file.close()
        result = await run_in_threadpool(backend.save_background, user, data)
        return JSONResponse(result, headers=_HEADERS)

    @app.get("/api/users/{user}/background")
    def background(user: str):
        target, media_type = backend.background_media(user)
        return FileResponse(target, media_type=media_type, headers=_HEADERS)

    @app.delete("/api/users/{user}/background")
    def delete_background(user: str):
        return JSONResponse(backend.delete_background(user), headers=_HEADERS)
