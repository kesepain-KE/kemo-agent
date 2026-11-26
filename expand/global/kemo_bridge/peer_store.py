"""Peer identities, their one-time secrets and their local audit trail."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from bridge_config import PEER_RETENTION_DAYS
from capabilities import capabilities_for
from payload_tools import redact

_USER_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SCHEMA = """
CREATE TABLE IF NOT EXISTS peers (
    peer_id TEXT PRIMARY KEY NOT NULL CHECK(length(peer_id) = 36 AND peer_id = lower(peer_id)),
    channel TEXT NOT NULL CHECK(channel IN ('device', 'agent')),
    bound_user TEXT NOT NULL CHECK(length(bound_user) BETWEEN 1 AND 128),
    secret_hash TEXT NOT NULL UNIQUE,
    secret_hint TEXT NOT NULL CHECK(length(secret_hint) = 4),
    label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    revoked_at TEXT
);
CREATE TRIGGER IF NOT EXISTS immutable_peer_identity
BEFORE UPDATE OF peer_id, channel, bound_user ON peers
WHEN OLD.peer_id <> NEW.peer_id OR OLD.channel <> NEW.channel OR OLD.bound_user <> NEW.bound_user
BEGIN SELECT RAISE(ABORT, 'peer_identity_immutable'); END;
CREATE TRIGGER IF NOT EXISTS reserve_peer_id
BEFORE INSERT ON peers WHEN EXISTS(SELECT 1 FROM peers WHERE peer_id=NEW.peer_id)
BEGIN SELECT RAISE(ABORT, 'peer_id_reserved'); END;
CREATE TRIGGER IF NOT EXISTS keep_peer_id
BEFORE DELETE ON peers BEGIN SELECT RAISE(ABORT, 'peer_id_reserved'); END;
CREATE TRIGGER IF NOT EXISTS irreversible_peer_revocation
BEFORE UPDATE OF revoked_at ON peers
WHEN OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NOT OLD.revoked_at
BEGIN SELECT RAISE(ABORT, 'peer_revoked'); END;
CREATE TABLE IF NOT EXISTS peer_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT REFERENCES peers(peer_id),
    channel TEXT,
    action TEXT NOT NULL,
    endpoint TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS peer_audit_by_peer ON peer_audit(peer_id, action, id);
CREATE INDEX IF NOT EXISTS peer_audit_recent ON peer_audit(created_at, action);
CREATE TABLE IF NOT EXISTS peer_deliveries (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS peer_deliveries_by_age ON peer_deliveries(created_at);
CREATE TABLE IF NOT EXISTS bridge_identity (
    id INTEGER PRIMARY KEY CHECK(id=1),
    instance_uuid TEXT NOT NULL,
    exposed_user TEXT
);
CREATE TABLE IF NOT EXISTS agent_peer_keys (
    peer_id TEXT PRIMARY KEY REFERENCES peers(peer_id),
    secret TEXT NOT NULL
);
"""
_PUBLIC_COLUMNS = (
    "peer_id", "channel", "bound_user", "secret_hint", "label", "enabled",
    "created_at", "last_seen_at", "revoked_at",
)
_TRAFFIC_ACTIONS = ("message_received", "message_sent", "device_action_ack", "artifact_upload", "artifact_download")
_TRAFFIC_WHERE = "created_at>=? AND action IN (" + ",".join("?" for _ in _TRAFFIC_ACTIONS) + ")"
_AUTH_AUDIT_WINDOW = timedelta(minutes=1)
_AUDIT_PRUNE_BATCH = 256


def _day_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")


class BridgeError(RuntimeError):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


@dataclass(frozen=True)
class PeerIdentity:
    peer_id: str
    channel: str
    bound_user: str
    proof: str = field(repr=False)

    @property
    def username(self) -> str:
        return self.bound_user

    def public(self) -> dict[str, Any]:
        return {
            "peer_id": self.peer_id,
            "channel": self.channel,
            "bound_user": self.bound_user,
            "capabilities": sorted(capabilities_for(self.channel)),
        }


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _secret(channel: str) -> str:
    return ("kb_dev_" if channel == "device" else "kb_agt_") + secrets.token_urlsafe(32)


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _label(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 120 or any(ord(ch) < 32 for ch in value):
        raise BridgeError(400, "invalid_peer_label")
    return value.strip() or None


def _public(row: sqlite3.Row) -> dict[str, Any]:
    result = {key: row[key] for key in _PUBLIC_COLUMNS}
    result["enabled"] = bool(result["enabled"] and not result["revoked_at"])
    return result


class PeerStore:
    def __init__(self, path: Path, users_root: Path) -> None:
        self.path = path
        self.users_root = users_root
        with self.connection() as db:
            db.executescript(_SCHEMA)
            if not any(col["name"] == "run_id" for col in db.execute("PRAGMA table_info(peer_audit)")):
                db.execute("BEGIN IMMEDIATE")
                if not any(col["name"] == "run_id" for col in db.execute("PRAGMA table_info(peer_audit)")):
                    db.execute("ALTER TABLE peer_audit ADD COLUMN run_id TEXT")
        if os.name != "nt":
            self.path.chmod(0o600)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.path, timeout=2.5, uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            with db:
                if write:
                    db.execute("BEGIN IMMEDIATE")
                yield db

    @staticmethod
    def audit_row(db: sqlite3.Connection, action: str, *, peer_id: str | None = None,
                  channel: str | None = None, endpoint: str | None = None,
                  detail: dict[str, Any] | None = None, run_id: str | None = None) -> None:
        db.execute(
            "INSERT INTO peer_audit(peer_id,channel,action,endpoint,detail,created_at,run_id) VALUES(?,?,?,?,?,?,?)",
            (peer_id, channel, action, endpoint, json.dumps(redact(detail or {}), ensure_ascii=False), now(), run_id),
        )

    def audit(self, action: str, peer: PeerIdentity | None = None, *, endpoint: str | None = None,
              detail: dict[str, Any] | None = None, run_id: str | None = None) -> None:
        once = action == "message_sent" and run_id is not None
        if once:
            with self.connection() as db:
                if db.execute("SELECT 1 FROM peer_deliveries WHERE run_id=?", (run_id,)).fetchone():
                    return
        with self.connection(write=True) as db:
            if once and not db.execute("INSERT OR IGNORE INTO peer_deliveries VALUES (?, ?)",
                                       (run_id, now())).rowcount:
                return
            self.audit_row(db, action, peer_id=peer.peer_id if peer else None,
                           channel=peer.channel if peer else None, endpoint=endpoint, detail=detail, run_id=run_id)

    def prune_audit(self, journal: Path) -> bool:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=PEER_RETENTION_DAYS)).isoformat(timespec="seconds")
        with self.connection() as db:
            # Read the existing run journal without initializing or mutating it.
            db.execute("ATTACH DATABASE ? AS journal", (journal.resolve().as_uri() + "?mode=ro",))
            audits = db.execute("""DELETE FROM peer_audit WHERE id IN (
                SELECT a.id FROM peer_audit AS a WHERE a.created_at<=?
                AND NOT EXISTS (SELECT 1 FROM journal.app_runs AS r
                                WHERE r.run_id=a.run_id AND r.status IN ('starting','running','cancelling'))
                AND NOT (a.run_id IS NULL AND a.channel IS 'agent'
                    AND a.action IN ('message_received','message_sent') AND EXISTS (
                        SELECT 1 FROM journal.app_runs AS r WHERE r.username=a.peer_id
                        AND r.status IN ('starting','running','cancelling')))
                ORDER BY a.created_at LIMIT ?
            )""", (cutoff, _AUDIT_PRUNE_BATCH)).rowcount
            # Keep the once-per-run marker while an old terminal can still be replayed.
            markers = db.execute("""DELETE FROM peer_deliveries WHERE run_id IN (
                SELECT d.run_id FROM peer_deliveries AS d WHERE d.created_at<=?
                AND NOT EXISTS (SELECT 1 FROM journal.app_runs AS r WHERE r.run_id=d.run_id)
                ORDER BY d.created_at LIMIT ?
            )""", (cutoff, _AUDIT_PRUNE_BATCH)).rowcount
        return max(audits, markers) == _AUDIT_PRUNE_BATCH

    @staticmethod
    def row(db: sqlite3.Connection, peer_id: str) -> sqlite3.Row:
        result = db.execute("SELECT * FROM peers WHERE peer_id=?", (peer_id,)).fetchone()
        if result is None:
            raise BridgeError(404, "peer_not_found")
        return result

    def user_directory(self, bound_user: str) -> Path:
        if (
            not isinstance(bound_user, str) or not _USER_NAME.fullmatch(bound_user)
            or bound_user.endswith(".")
        ):
            raise BridgeError(400, "invalid_bound_user")
        root = self.users_root.resolve()
        directory = root / bound_user
        if not directory.is_dir() or directory.resolve() != directory:
            raise BridgeError(400, "unknown_bound_user")
        return directory

    def local_identity(self) -> dict[str, Any]:
        sql = "SELECT instance_uuid, exposed_user FROM bridge_identity WHERE id=1"
        with self.connection() as db:
            if row := db.execute(sql).fetchone():
                return dict(row)
        with self.connection(write=True) as db:
            db.execute("INSERT OR IGNORE INTO bridge_identity(id, instance_uuid) VALUES(1,?)", (str(uuid.uuid4()),))
            return dict(db.execute(sql).fetchone())

    def replace_instance_uuid(self) -> str:
        self.local_identity()
        value = str(uuid.uuid4())
        with self.connection(write=True) as db:
            db.execute("UPDATE bridge_identity SET instance_uuid=? WHERE id=1", (value,))
        return value

    def set_exposed_user(self, user: str) -> None:
        self.user_directory(user)
        self.local_identity()
        with self.connection(write=True) as db:
            if db.execute("""SELECT 1 FROM peers WHERE channel='agent' AND revoked_at IS NULL
                             AND bound_user<>? LIMIT 1""", (user,)).fetchone():
                raise BridgeError(409, "external_user_in_use")
            db.execute("UPDATE bridge_identity SET exposed_user=? WHERE id=1", (user,))

    def create(self, channel: str, bound_user: str, *, label: str | None = None) -> dict[str, Any]:
        if not capabilities_for(channel):
            raise BridgeError(400, "invalid_channel")
        self.user_directory(bound_user)
        with self.connection(write=True) as db:
            return self._create(db, str(uuid.uuid4()), channel, bound_user, label)

    def create_external(self, peer_id: str, *, label: str | None = None) -> dict[str, Any]:
        self.local_identity()
        with self.connection(write=True) as db:
            identity = db.execute("SELECT * FROM bridge_identity WHERE id=1").fetchone()
            user = identity["exposed_user"]
            if not user:
                raise BridgeError(409, "exposed_user_required")
            if peer_id == identity["instance_uuid"]:
                raise BridgeError(400, "self_binding")
            self.user_directory(user)
            peer = self._create(db, peer_id, "agent", user, label)
            db.execute("INSERT INTO agent_peer_keys(peer_id,secret) VALUES(?,?)", (peer_id, peer["secret"]))
            return peer

    def _create(self, db, peer_id: str, channel: str, user: str, label: str | None) -> dict[str, Any]:
        if db.execute("SELECT 1 FROM peers WHERE peer_id=?", (peer_id,)).fetchone():
            raise BridgeError(409, "peer_id_reserved")
        secret = _secret(channel)
        db.execute(
            "INSERT INTO peers(peer_id,channel,bound_user,secret_hash,secret_hint,label,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (peer_id, channel, user, _digest(secret), secret[-4:], _label(label), now()),
        )
        self.audit_row(db, "peer_create", peer_id=peer_id, channel=channel)
        return {**_public(self.row(db, peer_id)), "secret": secret}

    def list(self, *, with_copy_state: bool = False) -> list[dict[str, Any]]:
        with self.connection() as db:
            if with_copy_state:
                rows = db.execute("""
                    SELECT p.*, EXISTS(SELECT 1 FROM agent_peer_keys k WHERE k.peer_id=p.peer_id) AS can_copy
                    FROM peers p ORDER BY p.created_at, p.peer_id
                """)
                return [{**_public(row), "can_copy": bool(row["can_copy"])} for row in rows]
            return [_public(row) for row in db.execute("SELECT * FROM peers ORDER BY created_at, peer_id")]

    def overview(self) -> dict[str, Any]:
        cutoff = _day_ago()
        with self.connection() as db:
            row = db.execute("""
                SELECT count(*) AS total,
                       count(CASE WHEN channel='device' THEN 1 END) AS device,
                       count(CASE WHEN channel='agent' THEN 1 END) AS agent,
                       count(CASE WHEN enabled=1 AND revoked_at IS NULL THEN 1 END) AS active,
                       max(last_seen_at) AS last_seen_at
                FROM peers
            """).fetchone()
            denied = dict(db.execute("""
                SELECT action, count(*) FROM peer_audit WHERE created_at>=? AND action IN
                    ('auth_fail','identity_mismatch','capability_not_granted','artifact_denied') GROUP BY action
            """, (cutoff,)).fetchall())
            return {**dict(row), "identity_mismatch_24h": denied.get("identity_mismatch", 0),
                    "denied_24h": sum(denied.values())}

    def get(self, peer_id: str) -> dict[str, Any]:
        with self.connection() as db:
            return _public(self.row(db, peer_id))

    def update(self, peer_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        if changes.keys() & {"peer_id", "channel", "bound_user"}:
            raise BridgeError(403, "peer_identity_immutable")
        if changes.keys() - {"label", "enabled"}:
            raise BridgeError(400, "invalid_peer_changes")
        if "enabled" in changes and type(changes["enabled"]) is not bool:
            raise BridgeError(400, "invalid_peer_enabled")
        with self.connection(write=True) as db:
            row = self.row(db, peer_id)
            if row["revoked_at"]:
                raise BridgeError(403, "peer_revoked")
            label = _label(changes["label"]) if "label" in changes else row["label"]
            enabled = int(changes.get("enabled", row["enabled"]))
            db.execute("UPDATE peers SET label=?, enabled=? WHERE peer_id=?", (label, enabled, peer_id))
            self.audit_row(db, "peer_update", peer_id=peer_id, channel=row["channel"],
                           detail={"enabled": bool(enabled)})
            return _public(self.row(db, peer_id))

    def rotate(self, peer_id: str) -> dict[str, Any]:
        with self.connection(write=True) as db:
            row = self.row(db, peer_id)
            if row["revoked_at"]:
                raise BridgeError(403, "peer_revoked")
            secret = _secret(row["channel"])
            db.execute("UPDATE peers SET secret_hash=?, secret_hint=? WHERE peer_id=?",
                       (_digest(secret), secret[-4:], peer_id))
            if row["channel"] == "agent":
                db.execute("""INSERT INTO agent_peer_keys(peer_id,secret) VALUES(?,?)
                              ON CONFLICT(peer_id) DO UPDATE SET secret=excluded.secret""", (peer_id, secret))
            self.audit_row(db, "secret_rotate", peer_id=peer_id, channel=row["channel"])
            return {**_public(self.row(db, peer_id)), "secret": secret}

    def copy_external_key(self, peer_id: str) -> str:
        with self.connection() as db:
            row = self.row(db, peer_id)
            if row["channel"] != "agent" or row["revoked_at"]:
                raise BridgeError(403, "external_key_forbidden")
            key = db.execute("SELECT secret FROM agent_peer_keys WHERE peer_id=?", (peer_id,)).fetchone()
            if key is None:
                raise BridgeError(409, "external_key_not_copyable")
            return key["secret"]

    def revoke(self, peer_id: str) -> dict[str, Any]:
        with self.connection(write=True) as db:
            row = self.row(db, peer_id)
            if not row["revoked_at"]:
                db.execute("UPDATE peers SET enabled=0, revoked_at=? WHERE peer_id=?", (now(), peer_id))
                db.execute("DELETE FROM agent_peer_keys WHERE peer_id=?", (peer_id,))
                self.audit_row(db, "revoke", peer_id=peer_id, channel=row["channel"])
            return _public(self.row(db, peer_id))

    def authenticate(self, authorization: str, peer_id: str, *, endpoint: str) -> PeerIdentity:
        scheme, _, candidate = authorization.partition(" ")
        proof = _digest(candidate.strip()) if scheme.lower() == "bearer" and 0 < len(candidate) <= 200 else ""
        with self.connection(write=True) as db:
            credential = db.execute(
                "SELECT * FROM peers WHERE secret_hash=? AND enabled=1 AND revoked_at IS NULL", (proof,),
            ).fetchone()
            if credential is None:  # 1: validate the secret before consulting the claimed UUID.
                error = BridgeError(401, "auth_failed")
                self.audit_row(db, "auth_fail", endpoint=endpoint, detail={"reason": error.code})
            else:
                registered = db.execute("SELECT * FROM peers WHERE peer_id=?", (peer_id,)).fetchone()
                if registered is None:  # 2: the claim must be a registered peer.
                    error = BridgeError(403, "peer_not_registered")
                    self.audit_row(db, "auth_fail", peer_id=credential["peer_id"], channel=credential["channel"],
                                   endpoint=endpoint, detail={"reason": error.code})
                elif registered["peer_id"] != credential["peer_id"]:  # 3: bind claim to credential.
                    error = BridgeError(403, "identity_mismatch")
                    self.audit_row(db, "identity_mismatch", peer_id=registered["peer_id"],
                                   channel=registered["channel"], endpoint=endpoint,
                                   detail={"matched_peer_id": credential["peer_id"]})
                else:
                    db.execute("UPDATE peers SET last_seen_at=? WHERE peer_id=?", (now(), peer_id))
                    cutoff = (datetime.now(timezone.utc) - _AUTH_AUDIT_WINDOW).isoformat(timespec="seconds")
                    latest = db.execute("SELECT created_at FROM peer_audit WHERE peer_id=? AND action='auth_ok' "
                                        "ORDER BY id DESC LIMIT 1", (peer_id,)).fetchone()
                    if latest is None or latest["created_at"] <= cutoff:
                        self.audit_row(db, "auth_ok", peer_id=peer_id, channel=registered["channel"], endpoint=endpoint)
                    return PeerIdentity(peer_id, registered["channel"], registered["bound_user"], proof)
        # Commit failure audits before raising. Never log the supplied bearer or raw unknown ID.
        raise error

    def is_current(self, peer: PeerIdentity) -> bool:
        with self.connection() as db:
            return db.execute(
                "SELECT 1 FROM peers WHERE peer_id=? AND secret_hash=? AND enabled=1 AND revoked_at IS NULL",
                (peer.peer_id, peer.proof),
            ).fetchone() is not None

    def audit_events(self, *, peer_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as db:
            where, params = ("WHERE peer_id=?", (peer_id,)) if peer_id else ("", ())
            return [{**dict(row), "detail": json.loads(row["detail"] or "{}")} for row in db.execute(
                f"SELECT * FROM peer_audit {where} ORDER BY id DESC LIMIT ?", (*params, min(100, max(1, limit))),
            )]

    def traffic_count(self) -> int:
        with self.connection() as db:
            return db.execute(f"SELECT count(*) FROM peer_audit WHERE {_TRAFFIC_WHERE}",
                              (_day_ago(), *_TRAFFIC_ACTIONS)).fetchone()[0]

    def traffic_events(self, *, peer_id: str | None = None) -> list[dict[str, Any]]:
        where, params = _TRAFFIC_WHERE, (_day_ago(), *_TRAFFIC_ACTIONS)
        if peer_id:
            where, params = where + " AND peer_id=?", (*params, peer_id)
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT created_at, action, peer_id, channel, endpoint FROM peer_audit "
                f"WHERE {where} ORDER BY id DESC LIMIT 50", params,
            )]
