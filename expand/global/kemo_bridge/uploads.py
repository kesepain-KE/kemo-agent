"""Bound-user upload storage without implicit model attachment."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from artifact_store import FILE_LIMIT_BYTES, file_under, is_link
from peer_store import BridgeError, PeerIdentity, PeerStore

_BAD = frozenset('<>:"/\\|?*')
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
             *(f"{p}{i}" for p in ("COM", "LPT") for i in "123456789¹²³")}


def clean_file_name(value: str | None) -> str:
    value = (value or "").replace("\\", "/").split("/")[-1]
    value = "".join(ch for ch in value if ch not in _BAD and not unicodedata.category(ch).startswith("C"))
    value = re.sub(r"\.{2,}", ".", value).strip(" .")
    if not value:
        return "upload.bin"
    stem, suffix = Path(value).stem, Path(value).suffix[:30]
    if value.partition(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    # Keep headroom for the collision suffix, on both UTF-8 and UTF-16 file systems.
    stem = stem.encode("utf-8")[:120].decode("utf-8", "ignore").rstrip(" .")
    suffix = suffix.encode("utf-8")[:40].decode("utf-8", "ignore").rstrip(" .")
    return (stem or "upload") + suffix


def save_upload(peers: PeerStore, peer: PeerIdentity, stream: BinaryIO, filename: str | None, *,
                extensions: list[str] | None, endpoint: str, display_name: str | None = None) -> dict:
    name = clean_file_name(filename)
    if extensions is not None and Path(name).suffix.casefold() not in {
        "." + extension.strip().lstrip(".").casefold() for extension in extensions
    }:
        raise BridgeError(415, "file_extension_forbidden")
    user = peers.user_directory(peer.bound_user)
    directory = user / "file_upload"
    if is_link(directory):
        raise BridgeError(403, "upload_directory_forbidden")
    directory.mkdir(exist_ok=True)
    file_id = uuid4().hex
    path = file_under(user, directory / name)
    try:
        target = path.open("xb")
    except FileExistsError:
        path = file_under(user, directory / f"{Path(name).stem}-{file_id}{Path(name).suffix}")
        target = path.open("xb")
    digest, size = hashlib.sha256(), 0
    try:
        with target:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > FILE_LIMIT_BYTES:
                    raise BridgeError(413, "file_too_large")
                digest.update(chunk)
                target.write(chunk)
        peers.audit("artifact_upload", peer, endpoint=endpoint,
                    detail={"file_name": path.name, "size_bytes": size, "sha256": digest.hexdigest(),
                            **({"display_name": clean_file_name(display_name)} if display_name else {})})
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return {"file_id": file_id, "path": path.relative_to(peers.users_root.resolve().parent).as_posix(),
            "size_bytes": size}
