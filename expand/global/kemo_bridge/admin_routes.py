"""Dispatch management operations from the authorized host workspace, not the Bridge listener."""
from __future__ import annotations

from uuid import UUID

import start_expand
from admin_console import legacy_credentials, legacy_items, outbound_snapshot, save_public_url, snapshot
from artifact_store import ArtifactStore
from bridge_config import BASE_DIR, USERS_DIR
from outbound_credentials import VAULT_NAME, import_credential
from peer_store import BridgeError, PeerStore


def _uuid(value: object) -> str:
    try:
        return str(UUID(str(value)))
    except ValueError:
        raise BridgeError(400, "invalid_bridge_id") from None


def dispatch(operation: str, data: dict) -> dict:
    if operation == "import_outbound_credential":
        return import_credential(BASE_DIR / VAULT_NAME, data)
    if operation == "list_legacy_credentials":
        return {"items": legacy_items()}
    if operation == "copy_legacy_key":
        peer_id = _uuid(data.get("peer_id"))
        for row in legacy_credentials():
            if (row["url"], row["peer_id"]) == (data.get("url"), peer_id):
                return {"clipboard_text": row["secret"]}
        raise BridgeError(404, "outbound_credential_not_found")
    if operation == "save_public_url":
        save_public_url(data.get("public_url"))
        return {"ok": True}

    peers = PeerStore(BASE_DIR / "_bridge_peers.sqlite3", USERS_DIR)
    if operation == "snapshot":
        if data.get("view") == "outbound":
            return outbound_snapshot(peers)
        return snapshot(peers, start_expand.status())
    if operation == "replace_instance_uuid":
        return {"instance_uuid": peers.replace_instance_uuid()}
    if operation == "set_exposed_user":
        peers.set_exposed_user(data.get("bound_user"))
        return {"ok": True}
    if operation == "create_device":
        return peers.create("device", data.get("bound_user"), label=data.get("label"))
    if operation == "create_external":
        if data.keys() & {"channel", "bound_user"}:
            raise BridgeError(403, "peer_identity_immutable")
        return peers.create_external(_uuid(data.get("peer_id")), label=data.get("label"))
    if operation == "edit_peer":
        return peers.update(_uuid(data.get("peer_id")), {k: v for k, v in data.items() if k != "peer_id"})
    if operation == "rotate_peer":
        return peers.rotate(_uuid(data.get("peer_id")))
    if operation == "revoke_peer":
        return peers.revoke(_uuid(data.get("peer_id")))
    if operation == "copy_external_key":
        return {"clipboard_text": peers.copy_external_key(_uuid(data.get("peer_id")))}
    if operation == "create_grant":
        from pydantic import ValidationError
        from artifact_routes import ArtifactRegistration

        try:
            body = ArtifactRegistration.model_validate(data)
        except ValidationError:
            raise BridgeError(400, "invalid_artifact_grant") from None
        return ArtifactStore(peers).register(**body.model_dump())
    if operation == "revoke_grant":
        return ArtifactStore(peers).revoke(_uuid(data.get("grant_id")))
    if operation == "logs":
        target = _uuid(data["peer_id"]) if data.get("peer_id") else None
        return {"items": peers.traffic_events(peer_id=target), "peers": peers.list()}
    raise BridgeError(403, "bridge_workspace_action_forbidden")
