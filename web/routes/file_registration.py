"""File and media route registration extracted from :mod:`web.routes.files`."""

from __future__ import annotations

def register_file_routes(app: FastAPI, backend: WebRunService) -> None:
    """Register file-space endpoints without changing their public contract."""

    import importlib
    _files = importlib.import_module("web.routes.files")
    AVATAR_MAX_BYTES = _files.AVATAR_MAX_BYTES
    Any = _files.Any
    COMPLETION_SOUND_MAX_BYTES = _files.COMPLETION_SOUND_MAX_BYTES
    DeleteManyBody = _files.DeleteManyBody
    FAILURE_SOUND_MAX_BYTES = _files.FAILURE_SOUND_MAX_BYTES
    FILE_UPLOAD_MAX_BYTES = _files.FILE_UPLOAD_MAX_BYTES
    FastAPI = _files.FastAPI
    File = _files.File
    FileResponse = _files.FileResponse
    Query = _files.Query
    Request = _files.Request
    Response = _files.Response
    TextBody = _files.TextBody
    UploadFile = _files.UploadFile
    WebRunService = _files.WebRunService
    _artifact_response = _files._artifact_response
    _preview_response = _files._preview_response
    _thumbnail_response = _files._thumbnail_response
    globals().update(
        {
            key: value
            for key, value in {
                "Any": Any,
                "AVATAR_MAX_BYTES": AVATAR_MAX_BYTES,
                "COMPLETION_SOUND_MAX_BYTES": COMPLETION_SOUND_MAX_BYTES,
                "DeleteManyBody": DeleteManyBody,
                "FAILURE_SOUND_MAX_BYTES": FAILURE_SOUND_MAX_BYTES,
                "FILE_UPLOAD_MAX_BYTES": FILE_UPLOAD_MAX_BYTES,
                "FastAPI": FastAPI,
                "File": File,
                "FileResponse": FileResponse,
                "Query": Query,
                "Request": Request,
                "Response": Response,
                "TextBody": TextBody,
                "UploadFile": UploadFile,
                "WebRunService": WebRunService,
            }.items()
        }
    )

    @app.get("/api/users/{user}/files/{scope}")
    async def files(
        user: str,
        scope: str,
        path: str = Query(default="", max_length=500),
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=6, ge=1, le=100),
    ) -> dict[str, Any]:
        return backend.files(
            user,
            scope,
            path=path,
            search=search,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/users/{user}/files/{scope}/upload")
    async def upload_file(
        user: str,
        scope: str,
        file: UploadFile = File(...),
        path: str = Query(...),
    ) -> dict[str, Any]:
        try:
            data = await file.read(FILE_UPLOAD_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_file(user, scope, path, data)

    @app.put("/api/users/{user}/files/{scope}/text")
    async def write_file_text(
        user: str,
        scope: str,
        body: TextBody,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.write_file_text(user, scope, path, body.content)

    @app.get("/api/users/{user}/files/{scope}/text")
    async def read_file_text(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.read_file_text(user, scope, path)

    @app.post("/api/users/{user}/files/{scope}/directory")
    async def make_file_directory(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.make_directory(user, scope, path)

    @app.patch("/api/users/{user}/files/{scope}/move")
    async def move_file(
        user: str,
        scope: str,
        path: str = Query(...),
        new_path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.move_file(user, scope, path, new_path)

    @app.get("/api/users/{user}/files/{scope}/download")
    async def download_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> FileResponse:
        target = backend.file_download(user, scope, path)
        return FileResponse(target, filename=target.name)

    @app.get("/api/users/{user}/artifacts/{checksum}")
    async def generated_artifact(
        user: str,
        checksum: str,
        path: str = Query(default="", max_length=500),
        size: int = Query(..., ge=1),
    ) -> FileResponse:
        target, media_type = backend.download_artifact(
            user,
            checksum,
            path=path,
            size=size,
        )
        return _artifact_response(target, media_type)

    @app.get("/api/users/{user}/files/{scope}/preview")
    async def preview_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> FileResponse:
        target, media_type, _ = backend.file_preview(user, scope, path)
        return _preview_response(target, media_type)

    @app.get("/api/users/{user}/attachment-thumbnails/{checksum}")
    async def attachment_thumbnail(
        user: str,
        checksum: str,
        path: str = Query(default="", max_length=500),
    ) -> FileResponse:
        target = backend.attachment_thumbnail(user, checksum, path=path)
        return _thumbnail_response(target)

    @app.delete("/api/users/{user}/files/{scope}")
    async def delete_file(
        user: str,
        scope: str,
        path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.delete_file(user, scope, path)

    @app.post("/api/users/{user}/files/{scope}/delete-many")
    async def delete_files(
        user: str,
        scope: str,
        body: DeleteManyBody,
    ) -> dict[str, Any]:
        return backend.delete_files(user, scope, body.paths)

    @app.delete("/api/users/{user}/files/{scope}/all")
    async def delete_all_files(user: str, scope: str) -> dict[str, Any]:
        return backend.delete_all_files(user, scope)

    @app.post("/api/users/{user}/avatar")
    async def upload_avatar(
        user: str,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        content_type = file.content_type
        try:
            data = await file.read(AVATAR_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_avatar(user, data, content_type)

    @app.get("/api/users/{user}/avatar")
    async def avatar(user: str) -> Response:
        target = backend.avatar(user)
        if target is None:
            return Response(status_code=204)
        return FileResponse(target)

    @app.post("/api/users/{user}/completion-sound")
    async def upload_completion_sound(
        user: str,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        content_type = file.content_type
        try:
            data = await file.read(COMPLETION_SOUND_MAX_BYTES + 1)
        finally:
            await file.close()
        status = backend.save_completion_sound(user, data, content_type)
        return {"ok": True, "status": status}

    @app.get("/api/users/{user}/completion-sound/status")
    async def completion_sound_status(user: str) -> dict[str, Any]:
        return backend.completion_sound_status(user)

    @app.post("/api/users/{user}/completion-sound/fallback")
    async def completion_sound_fallback(user: str) -> dict[str, Any]:
        return backend.play_completion_sound_fallback(user)

    @app.get("/api/users/{user}/completion-sound")
    async def completion_sound(user: str, request: Request) -> Response:
        target = backend.completion_sound_path(user)
        if target is None:
            return Response(status_code=204)
        status = backend.completion_sound_status(user)
        try:
            stat = target.stat()
        except OSError:
            return Response(status_code=204)
        etag = f'W/"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        cache_headers = {
            "Cache-Control": "private, no-cache",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; media-src 'self'",
        }
        if request.headers.get("if-none-match", "").strip() == etag:
            return Response(status_code=304, headers=cache_headers)
        data = backend.load_completion_sound(user)
        if data is None:
            return Response(status_code=204)
        return Response(
            content=data,
            media_type=status["mime_type"],
            headers=cache_headers,
        )

    @app.delete("/api/users/{user}/completion-sound")
    async def delete_completion_sound(user: str) -> dict[str, Any]:
        return {"ok": True, "deleted": backend.delete_completion_sound(user)}

    @app.post("/api/users/{user}/failure-sound")
    async def upload_failure_sound(
        user: str,
        file: UploadFile = File(...),
    ) -> dict[str, Any]:
        content_type = file.content_type
        try:
            data = await file.read(FAILURE_SOUND_MAX_BYTES + 1)
        finally:
            await file.close()
        status = backend.save_failure_sound(user, data, content_type)
        return {"ok": True, "status": status}

    @app.get("/api/users/{user}/failure-sound/status")
    async def failure_sound_status(user: str) -> dict[str, Any]:
        return backend.failure_sound_status(user)

    @app.post("/api/users/{user}/failure-sound/fallback")
    async def failure_sound_fallback(user: str) -> dict[str, Any]:
        return backend.play_failure_sound_fallback(user)

    @app.get("/api/users/{user}/failure-sound")
    async def failure_sound(user: str, request: Request) -> Response:
        target = backend.failure_sound_path(user)
        if target is None:
            return Response(status_code=204)
        status = backend.failure_sound_status(user)
        try:
            stat = target.stat()
        except OSError:
            return Response(status_code=204)
        etag = f'W/"{stat.st_size:x}-{stat.st_mtime_ns:x}"'
        cache_headers = {
            "Cache-Control": "private, no-cache",
            "ETag": etag,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; media-src 'self'",
        }
        if request.headers.get("if-none-match", "").strip() == etag:
            return Response(status_code=304, headers=cache_headers)
        data = backend.load_failure_sound(user)
        if data is None:
            return Response(status_code=204)
        return Response(
            content=data,
            media_type=status["mime_type"],
            headers=cache_headers,
        )

    @app.delete("/api/users/{user}/failure-sound")
    async def delete_failure_sound(user: str) -> dict[str, Any]:
        return {"ok": True, "deleted": backend.delete_failure_sound(user)}

    @app.get("/api/tmp")
    async def tmp_files(
        path: str = Query(default="", max_length=500),
        search: str = Query(default="", max_length=200),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=6, ge=1, le=100),
    ) -> dict[str, Any]:
        return backend.tmp_files(
            path=path,
            search=search,
            page=page,
            page_size=page_size,
        )

    @app.post("/api/tmp/upload")
    async def upload_tmp_file(
        file: UploadFile = File(...),
        path: str = Query(...),
    ) -> dict[str, Any]:
        try:
            data = await file.read(FILE_UPLOAD_MAX_BYTES + 1)
        finally:
            await file.close()
        return backend.save_tmp_file(path, data)

    @app.get("/api/tmp/preview")
    async def preview_tmp_file(path: str = Query(...)) -> FileResponse:
        target, media_type, _ = backend.tmp_file_preview(path)
        return _preview_response(target, media_type)

    @app.put("/api/tmp/text")
    async def write_tmp_text(
        path: str = Query(...),
        body: TextBody = ...,
    ) -> dict[str, Any]:
        return backend.write_tmp_text(path, body.content)

    @app.get("/api/tmp/text")
    async def read_tmp_text(path: str = Query(...)) -> dict[str, Any]:
        return backend.read_tmp_text(path)

    @app.post("/api/tmp/directory")
    async def make_tmp_directory(path: str = Query(...)) -> dict[str, Any]:
        return backend.make_tmp_directory(path)

    @app.patch("/api/tmp/move")
    async def move_tmp_file(
        path: str = Query(...),
        new_path: str = Query(...),
    ) -> dict[str, Any]:
        return backend.move_tmp_file(path, new_path)

    @app.delete("/api/tmp")
    async def delete_tmp_file(path: str = Query(...)) -> dict[str, Any]:
        return backend.delete_tmp_file(path)

    @app.post("/api/tmp/delete-many")
    async def delete_tmp_files(body: DeleteManyBody) -> dict[str, Any]:
        return backend.delete_tmp_files(body.paths)

    @app.delete("/api/tmp/all")
    async def delete_all_tmp_files() -> dict[str, Any]:
        return backend.delete_all_tmp_files()



