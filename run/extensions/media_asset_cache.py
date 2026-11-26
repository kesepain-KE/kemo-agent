"""Session-scoped, short-lived references to verified local media."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import sqlite3
from time import time
from typing import Any, Iterator
from uuid import uuid4

from run.extensions.attachments import (
    AttachmentError, RunAssetResolver, describe_local_asset,
    describe_uploaded_asset, _reject_linked_path,
)
from run.extensions.media_input_request import checked_media_path


@contextmanager
def _registry(path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(path, timeout=5.0)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS media_assets (
                source TEXT NOT NULL,
                session_id TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                descriptor TEXT NOT NULL,
                expires_at REAL NOT NULL,
                PRIMARY KEY (source, session_id, asset_id)
            )
        """)
        yield connection


def _delete_expired(connection: sqlite3.Connection, now: float) -> int:
    return connection.execute("DELETE FROM media_assets WHERE expires_at <= ?", (now,)).rowcount


def purge_expired_media_assets(root: Path) -> int:
    """Clear expired references from existing registries without deleting files."""
    root = root.resolve()
    now = time()
    deleted = 0
    for database in (root / "users").glob("*/runtime/media_assets.sqlite3"):
        try:
            if not database.is_file():
                continue
            _reject_linked_path(database, root)
            with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=5.0)) as connection, connection:
                count = _delete_expired(connection, now)
        except (AttachmentError, OSError, sqlite3.Error):
            logging.getLogger(__name__).warning(
                "清理用户 %s 的过期媒体引用失败", database.parents[1].name, exc_info=True,
            )
        else:
            deleted += count
    return deleted


def _retain_references(
    root: Path, user: str, references: list[tuple[str, dict[str, Any]]], *,
    source: str, session_id: str, ttl_seconds: int,
) -> str:
    now = time()
    expires_at = now + ttl_seconds
    expires_text = datetime.fromtimestamp(expires_at, timezone.utc).isoformat()
    database = root / "users" / user / "runtime" / "media_assets.sqlite3"
    _reject_linked_path(database, root.resolve())
    database.parent.mkdir(parents=True, exist_ok=True)
    with _registry(database) as connection:
        _delete_expired(connection, now)
        connection.executemany("""
            INSERT INTO media_assets (source, session_id, asset_id, descriptor, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source, session_id, asset_id) DO UPDATE SET
                descriptor = excluded.descriptor, expires_at = excluded.expires_at
        """, [(source, session_id, asset_id, json.dumps(descriptor, ensure_ascii=False),
               expires_at) for asset_id, descriptor in references])
    return expires_text


def retain_media_descriptors(
    root: Path, user: str, descriptors: list[dict[str, Any]], *,
    source: str, session_id: str, ttl_seconds: int,
) -> list[dict[str, Any]]:
    """Retain accepted inputs, preserving source authorization on every reuse."""
    if not descriptors or not source or not session_id:
        return descriptors
    references = []
    resolver = RunAssetResolver(root, user, descriptors)
    for value in descriptors:
        checked_media_path(root, user, str(value["path"]))
        path, verified = resolver.local_asset(str(value["asset_id"]))
        if verified["origin"] == "message_attachment":
            # The transport deletes its originals at the end of the round.
            # Import an authorized copy into the user's ordinary upload area;
            # like uploads/downloads, its file lifetime is independent of the ID.
            directory = (root / "users" / user / "file_upload" / "message"
                         / verified["source"] / uuid4().hex)
            target = directory / path.name
            _reject_linked_path(target, root.resolve())
            directory.mkdir(parents=True, exist_ok=True)
            try:
                with path.open("rb") as incoming, target.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                snapshot = describe_uploaded_asset(root, user, {"path": str(target)})
                if snapshot["checksum_sha256"] != verified["checksum_sha256"]:
                    raise AttachmentError("消息附件复制时内容发生变化，无法登记")
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            snapshot.update(origin="message_snapshot", source=verified["source"],
                            name=verified["name"])
            verified = snapshot
        references.append((str(value["asset_id"]), verified))
    expires = _retain_references(root, user, references, source=source,
                                 session_id=session_id, ttl_seconds=ttl_seconds)
    return [{**value, "reference_expires_at": expires} for value in descriptors]


def retain_media_assets(
    root: Path, user: str, artifacts: list[dict[str, Any]], *,
    source: str, session_id: str, ttl_seconds: int,
) -> list[dict[str, Any]]:
    """Associate gateway output IDs with their verified downloaded files."""
    if not artifacts or not source or not session_id:
        return artifacts
    references = []
    for artifact in artifacts:
        path = root.resolve() / str(artifact["project_path"])
        _reject_linked_path(path, root.resolve())
        if not path.resolve().is_relative_to((root / "users" / user / "download").resolve()):
            raise AttachmentError("生成资源必须保存在当前用户的 download 目录")
        descriptor = describe_local_asset(root, user, {"path": str(path)})
        if descriptor["checksum_sha256"] != artifact["checksum_sha256"]:
            raise AttachmentError("生成资源保存后内容发生变化，无法登记")
        references.append((str(artifact["asset_id"]), descriptor))
    expires = _retain_references(root, user, references, source=source,
                                 session_id=session_id, ttl_seconds=ttl_seconds)
    return [{**artifact, "reference_expires_at": expires} for artifact in artifacts]


def resolve_retained_media(
    root: Path, user: str, asset_ids: list[str], *, source: str, session_id: str,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Resolve recent references in this exact installation/user/source/session."""
    database = root / "users" / user / "runtime" / "media_assets.sqlite3"
    if not asset_ids or not source or not session_id or not database.exists():
        return asset_ids, []
    _reject_linked_path(database, root.resolve())
    now = time()
    with _registry(database) as connection:
        _delete_expired(connection, now)
        rows = connection.execute(
            "SELECT asset_id, descriptor, expires_at FROM media_assets "
            "WHERE source = ? AND session_id = ? AND asset_id IN ("
            + ",".join("?" for _ in asset_ids) + ")",
            (source, session_id, *asset_ids),
        ).fetchall()
    descriptors = {
        asset_id: {**json.loads(value), "reference_expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}
        for asset_id, value, expires in rows
    }
    return (
        [str(descriptors[value]["asset_id"]) if value in descriptors else value
         for value in asset_ids],
        list(descriptors.values()),
    )


def retain_media_aliases(
    root: Path, user: str, aliases: dict[str, str], *, source: str, session_id: str,
) -> None:
    """Bind upload IDs to local references without extending the original TTL."""
    database = root / "users" / user / "runtime" / "media_assets.sqlite3"
    if not aliases or not source or not session_id or not database.exists():
        return
    _reject_linked_path(database, root.resolve())
    with _registry(database) as connection:
        for alias, local_id in aliases.items():
            connection.execute("""
                INSERT INTO media_assets (source, session_id, asset_id, descriptor, expires_at)
                SELECT source, session_id, ?, descriptor, expires_at FROM media_assets
                WHERE source = ? AND session_id = ? AND expires_at > ?
                    AND (asset_id = ? OR json_extract(descriptor, '$.asset_id') = ?)
                ORDER BY expires_at DESC LIMIT 1
                ON CONFLICT(source, session_id, asset_id) DO UPDATE SET
                    descriptor = excluded.descriptor, expires_at = excluded.expires_at
            """, (alias, source, session_id, time(), local_id, local_id))
