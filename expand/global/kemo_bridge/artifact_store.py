"""Durable single-file grants. Media asset references are optional provenance."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any
from uuid import uuid4

from peer_store import BridgeError, PeerIdentity, PeerStore, now

# Same ceiling as the framework's ordinary file-upload pipe (web.constants.FILE_UPLOAD_MAX_BYTES).
FILE_LIMIT_BYTES = 80 * 1024 * 1024
FILE_ROOTS = ("download", "file_upload")
# Delivery has the same per-kind ceilings as the framework's Web file previews.
ARTIFACT_LIMITS = {
    "file": FILE_LIMIT_BYTES, "image": 10 * 1024 * 1024,
    "audio": 100 * 1024 * 1024, "video": 300 * 1024 * 1024,
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifact_grants (
    grant_id TEXT PRIMARY KEY NOT NULL,
    peer_id TEXT NOT NULL REFERENCES peers(peer_id),
    asset_id TEXT,
    file_path TEXT NOT NULL,
    display_name TEXT,
    media_kind TEXT NOT NULL CHECK(media_kind IN ('file','image','audio','video')),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    created_by_run TEXT,
    created_at TEXT NOT NULL,
    download_count INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS artifact_grants_peer ON artifact_grants(peer_id, created_at);
"""


def is_link(path: Path) -> bool:
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def file_under(user: Path, path: Path) -> Path:
    if (
        not path.is_absolute() or ".." in path.parts or path == user
        or not path.is_relative_to(user)
    ):
        raise BridgeError(400, "artifact_path_forbidden")
    relative = path.relative_to(user)
    if len(relative.parts) < 2 or relative.parts[0] not in FILE_ROOTS:
        raise BridgeError(400, "artifact_path_forbidden")
    # Reject linked directories as well as linked files; do not treat a symlink
    # pointing to another otherwise allowed file as a grant for the target.
    current = user
    for part in relative.parts:
        current /= part
        if is_link(current):
            raise BridgeError(400, "artifact_path_forbidden")
    if path.resolve() != path:
        raise BridgeError(400, "artifact_path_forbidden")
    return path


def _metadata(row: sqlite3.Row) -> dict[str, Any]:
    return {
        **{k: row[k] for k in ("grant_id", "display_name", "media_kind", "size_bytes", "created_at",
                                "download_count", "revoked_at")},
        "file_name": Path(row["file_path"]).name,
        **({"asset_id": row["asset_id"]} if row["asset_id"] else {}),
    }


class ArtifactStore:
    def __init__(self, peers: PeerStore) -> None:
        self.peers = peers
        with peers.connection() as db:
            db.executescript(_SCHEMA)

    def register(self, peer_id: str, file_path: str, media_kind: str, *,
                 display_name: str | None = None, asset_id: str | None = None,
                 created_by_run: str | None = None) -> dict[str, Any]:
        if media_kind not in ARTIFACT_LIMITS:
            raise BridgeError(400, "invalid_media_kind")
        peer = self.peers.get(peer_id)
        if not peer["enabled"]:
            raise BridgeError(403, "peer_disabled")
        path = file_under(self.peers.user_directory(peer["bound_user"]), Path(file_path))
        if not path.is_file():
            raise BridgeError(400, "artifact_file_required")
        size = path.stat().st_size
        if size > ARTIFACT_LIMITS[media_kind]:
            raise BridgeError(413, "file_too_large")
        grant_id = str(uuid4())
        with self.peers.connection(write=True) as db:
            row = self.peers.row(db, peer_id)
            if not row["enabled"] or row["revoked_at"]:
                raise BridgeError(403, "peer_disabled")
            db.execute(
                "INSERT INTO artifact_grants(grant_id,peer_id,asset_id,file_path,display_name,media_kind,"
                "size_bytes,created_by_run,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (grant_id, peer_id, asset_id, str(path), display_name, media_kind, size, created_by_run, now()),
            )
            self.peers.audit_row(db, "artifact_grant", peer_id=peer_id, channel=row["channel"],
                                 detail={"grant_id": grant_id, "size_bytes": size, "media_kind": media_kind})
            return _metadata(self._row(db, grant_id))

    @staticmethod
    def _row(db, grant_id: str):
        return db.execute("SELECT * FROM artifact_grants WHERE grant_id=?", (grant_id,)).fetchone()

    def list(self, *, peer_id: str | None = None, local: bool = False) -> list[dict]:
        with self.peers.connection() as db:
            where = "WHERE peer_id=?" if peer_id is not None else ""
            rows = db.execute(f"SELECT * FROM artifact_grants {where} ORDER BY created_at DESC, grant_id",
                              (peer_id,) if peer_id is not None else ())
            return [({**_metadata(row), "peer_id": row["peer_id"], "created_by_run": row["created_by_run"]}
                     if local else _metadata(row)) for row in rows]

    def authorized(self, peer: PeerIdentity, grant_id: str, *, endpoint: str) -> sqlite3.Row:
        with self.peers.connection() as db:
            row = self._row(db, grant_id)
        if row is None or row["peer_id"] != peer.peer_id or row["revoked_at"]:
            self.peers.audit("artifact_denied", peer, endpoint=endpoint)
            raise BridgeError(403, "artifact_not_granted")
        return row

    def metadata(self, peer: PeerIdentity, grant_id: str, *, endpoint: str) -> dict:
        return _metadata(self.authorized(peer, grant_id, endpoint=endpoint))

    def content(self, peer: PeerIdentity, grant_id: str, *, endpoint: str):
        row = self.authorized(peer, grant_id, endpoint=endpoint)
        try:
            path = file_under(self.peers.user_directory(peer.bound_user), Path(row["file_path"]))
            if not path.is_file():
                raise FileNotFoundError
            metadata = path.stat()
        except (BridgeError, OSError, RuntimeError) as exc:
            raise BridgeError(410, "artifact_gone") from exc
        if metadata.st_size > ARTIFACT_LIMITS[row["media_kind"]]:
            raise BridgeError(413, "file_too_large")
        return path, metadata

    def downloaded(self, peer: PeerIdentity, grant_id: str, endpoint: str) -> None:
        with self.peers.connection(write=True) as db:
            db.execute("UPDATE artifact_grants SET download_count=download_count+1 WHERE grant_id=?", (grant_id,))
            self.peers.audit_row(db, "artifact_download", peer_id=peer.peer_id, channel=peer.channel,
                                 endpoint=endpoint, detail={"grant_id": grant_id})

    def revoke(self, grant_id: str, *, peer: PeerIdentity | None = None, endpoint: str | None = None) -> dict:
        if peer is not None:
            self.authorized(peer, grant_id, endpoint=endpoint or "/v1/artifacts/{grant_id}/revoke")
        with self.peers.connection(write=True) as db:
            row = self._row(db, grant_id)
            if row is None:
                raise BridgeError(404, "artifact_not_found")
            if not row["revoked_at"]:
                db.execute("UPDATE artifact_grants SET revoked_at=? WHERE grant_id=?", (now(), grant_id))
                owner = self.peers.row(db, row["peer_id"])
                self.peers.audit_row(db, "artifact_revoke", peer_id=owner["peer_id"], channel=owner["channel"],
                                     endpoint=endpoint, detail={"grant_id": grant_id})
            return _metadata(self._row(db, grant_id))
