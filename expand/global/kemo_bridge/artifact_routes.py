"""Wire contracts for grants, file download and incoming uploads."""
from __future__ import annotations

from typing import Literal

import mimetypes
from fastapi import Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from artifact_store import FILE_LIMIT_BYTES, ArtifactStore
from bridge_auth import BranchRoutes, require_session
from peer_store import BridgeError, PeerIdentity
from uploads import save_upload


class ArtifactRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    peer_id: str = Field(min_length=36, max_length=36)
    file_path: str = Field(min_length=1, max_length=4096)
    media_kind: Literal["file", "image", "audio", "video"] = "file"
    display_name: str | None = Field(default=None, max_length=240)
    asset_id: str | None = Field(default=None, max_length=300)
    created_by_run: str | None = Field(default=None, max_length=128)


def register_artifact_routes(app, device: BranchRoutes, agent: BranchRoutes, grants: ArtifactStore,
                             *, read_public_url, upload_extensions) -> None:
    @app.post("/internal/artifacts")
    def register(body: ArtifactRegistration):
        return grants.register(**body.model_dump())

    @device.route("GET", "/artifacts", "file_list")
    def list_grants(peer: PeerIdentity = Depends(require_session)):
        return {"items": grants.list(peer_id=peer.peer_id)}

    @device.route("GET", "/artifacts/{grant_id}", "file_list")
    def device_metadata(grant_id: str, peer: PeerIdentity = Depends(require_session)):
        return grants.metadata(peer, grant_id, endpoint="/v1/artifacts/{grant_id}")

    @agent.route("POST", "/artifacts/{grant_id}", "artifact_download")
    def agent_metadata(grant_id: str, request: Request, peer: PeerIdentity = Depends(require_session)):
        result = grants.metadata(peer, grant_id, endpoint="/peer/v1/artifacts/{grant_id}")
        base = read_public_url() or request.scope.get("root_path", "").rstrip("/")
        return {**result, "download_url": f"{base}/peer/v1/artifacts/{result['grant_id']}/content"}

    def response_for(peer: PeerIdentity, grant_id: str, endpoint: str):
        path, stat = grants.content(peer, grant_id, endpoint=endpoint)
        return FileResponse(path, stat_result=stat, filename=path.name,
                            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
                            background=BackgroundTask(grants.downloaded, peer, grant_id, endpoint))

    @device.route("GET", "/artifacts/{grant_id}/content", "file_list")
    def device_content(grant_id: str, peer: PeerIdentity = Depends(require_session)):
        return response_for(peer, grant_id, "/v1/artifacts/{grant_id}/content")

    @agent.route("GET", "/artifacts/{grant_id}/content", "artifact_download")
    def agent_content(grant_id: str, peer: PeerIdentity = Depends(require_session)):
        return response_for(peer, grant_id, "/peer/v1/artifacts/{grant_id}/content")

    @device.route("POST", "/artifacts/{grant_id}/revoke", "file_list")
    def revoke(grant_id: str, peer: PeerIdentity = Depends(require_session)):
        return grants.revoke(grant_id, peer=peer, endpoint="/v1/artifacts/{grant_id}/revoke")

    @device.route("POST", "/upload", "file_upload")
    @agent.route("POST", "/upload", "artifact_upload")
    async def upload(request: Request, file: UploadFile = File(...), filename: str | None = Form(None),
                     display_name: str | None = Form(None), peer: PeerIdentity = Depends(require_session)):
        try:
            if "path" in request.query_params:
                raise BridgeError(400, "upload_path_forbidden")
            if file.size is not None and file.size > FILE_LIMIT_BYTES:
                raise BridgeError(413, "file_too_large")
            return await run_in_threadpool(
                save_upload, grants.peers, peer, file.file, filename or file.filename,
                extensions=upload_extensions(), endpoint=request.scope["route"].path, display_name=display_name,
            )
        finally:
            await file.close()
