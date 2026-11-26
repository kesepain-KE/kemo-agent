"""Local state for the host's embedded Bridge console."""
from __future__ import annotations

import json

from artifact_store import ArtifactStore
from bridge_config import BASE_DIR, normalize_public_url
from initialize_config import _atomic_json
from outbound_credentials import VAULT_NAME, read_rows
from panel_control import status_payload
from peer_store import BridgeError, PeerStore


def snapshot(peers: PeerStore, runtime: dict) -> dict:
    rows = peers.list(with_copy_state=True)
    users = []
    if peers.users_root.is_dir():
        for path in sorted(peers.users_root.iterdir(), key=lambda p: p.name.casefold()):
            try:
                peers.user_directory(path.name)
            except BridgeError:
                continue
            users.append(path.name)
    return {
        **peers.local_identity(),
        "state": status_payload(runtime),
        "users": users,
        "peers": rows,
        "grants": ArtifactStore(peers).list(local=True),
        "mobile_users": sorted({p["bound_user"] for p in rows if p["channel"] == "device" and p["enabled"]}),
        "external_count": sum(p["channel"] == "agent" and not p["revoked_at"] for p in rows),
        "traffic_count": peers.traffic_count(),
    }


def save_public_url(value: object) -> None:
    try:
        if isinstance(value, str) and len(value) > 2048:
            raise ValueError
        address = normalize_public_url(value)
    except ValueError:
        raise BridgeError(400, "invalid_public_url") from None

    path = BASE_DIR / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError
    except FileNotFoundError:
        raise BridgeError(409, "bridge_not_initialized") from None
    except (OSError, UnicodeError, ValueError):
        raise BridgeError(503, "bridge_config_unavailable") from None
    section = config.get("bridge")
    config["bridge"] = {**(section if isinstance(section, dict) else {}), "public_url": address}
    _atomic_json(path, config, sensitive=True)


def legacy_credentials() -> list[dict]:
    """Keep old credentials issued by other instances separate from our peer keys."""
    return read_rows(BASE_DIR / VAULT_NAME)


def legacy_items() -> list[dict]:
    return [{key: row[key] for key in ("url", "peer_id", "label")} for row in legacy_credentials()]


def outbound_snapshot(peers: PeerStore) -> dict:
    result = {
        "exposed_user": peers.local_identity()["exposed_user"],
        "peers": [row for row in peers.list(with_copy_state=True) if row["channel"] == "agent" and not row["revoked_at"]],
    }
    try:
        result["legacy_items"] = legacy_items()
    except BridgeError as error:
        result.update(legacy_items=[], legacy_error=error.code)
    return result
