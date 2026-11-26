"""Credentials issued to this instance by other bridges."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import yaml

from bridge_config import normalize_public_url
from peer_store import BridgeError

VAULT_NAME = "_workspace_credentials.yaml"
OWNER = "kesepain"
_FIELDS = ("url", "peer_id", "label", "secret")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{0,512}")


@dataclass(frozen=True)
class OutboundTarget:
    url: str
    peer_id: str
    label: str
    secret: str = field(repr=False)
    allowed_users: tuple[str, ...]

    @property
    def target_id(self) -> str:
        return hashlib.sha256(f"{self.url}\n{self.peer_id}".encode()).hexdigest()[:32]

    def public(self) -> dict:
        return {"target_id": self.target_id, "label": self.label, "url": self.url}


def _target(row: object) -> OutboundTarget:
    if not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in _FIELDS):
        raise ValueError
    # Accept operator-owned private addresses and reverse-proxy path prefixes.
    url = normalize_public_url(row["url"])
    if not url or len(url) > 2048 or "?" in url or "#" in url or not _TOKEN.fullmatch(row["secret"]):
        raise ValueError
    peer_id = str(UUID(row["peer_id"]))
    users = row.get("allowed_users", [OWNER])
    if not isinstance(users, list) or len(users) > 64 or any(
        not isinstance(user, str) or not 1 <= len(user) <= 128 or user != user.strip()
        or any(ord(char) < 32 or ord(char) == 127 for char in user)
        for user in users
    ) or len(set(users)) != len(users):
        raise ValueError
    return OutboundTarget(url, peer_id, row["label"], row["secret"], tuple(users))


def _read(path: Path) -> tuple[dict, list[OutboundTarget]]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rows = data.get("outbound", []) if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError
        targets = [_target(row) for row in rows]
        if len({target.target_id for target in targets}) != len(targets):
            raise ValueError
        return data, targets
    except FileNotFoundError:
        return {}, []
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        raise BridgeError(503, "workspace_credentials_unavailable") from None


def read_rows(path: Path) -> list[dict]:
    return _read(path)[0].get("outbound", [])


def allowed_targets(path: Path, username: str) -> list[OutboundTarget]:
    return [target for target in _read(path)[1] if username in target.allowed_users]


def import_credential(path: Path, data: dict) -> dict:
    row = {key: data.get(key) for key in _FIELDS}
    if "allowed_users" in data:
        row["allowed_users"] = data["allowed_users"]
    try:
        target = _target(row)
        if not target.secret:
            raise ValueError
    except ValueError:
        raise BridgeError(400, "invalid_outbound_credential") from None
    vault, targets = _read(path)
    rows = vault.setdefault("outbound", [])
    row.update(url=target.url, peer_id=target.peer_id)
    for i, old in enumerate(targets):
        if old.target_id == target.target_id:
            rows[i] = row
            break
    else:
        rows.append(row)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False,
        ) as handle:
            tmp = Path(handle.name)
            os.chmod(tmp, 0o600)
            yaml.safe_dump(vault, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except (OSError, yaml.YAMLError):
        raise BridgeError(503, "workspace_credentials_unavailable") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    return {"ok": True, **target.public()}
