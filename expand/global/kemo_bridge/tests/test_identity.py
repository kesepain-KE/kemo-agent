import json
import sqlite3
import uuid

import pytest

from conftest import peer_headers


def test_peer_contract_and_database_identity_guards(bridge):
    module, _ = bridge
    store = module.PEERS
    device = store.create("device", "alice", label="phone")
    agent = store.create("agent", "bob")
    assert uuid.UUID(device["peer_id"]).version == uuid.UUID(agent["peer_id"]).version == 4
    assert device["secret"].startswith("kb_dev_") and agent["secret"].startswith("kb_agt_")
    assert "secret" not in store.get(agent["peer_id"])
    assert all("secret" not in peer and "secret_hash" not in peer for peer in store.list())
    for field, rejected in (("channel", "device"), ("bound_user", "alice")):
        with pytest.raises(RuntimeError) as caught:
            store.update(agent["peer_id"], {field: rejected})
        assert caught.value.status == 403
        assert caught.value.code == "peer_identity_immutable"
        with pytest.raises(sqlite3.IntegrityError, match="peer_identity_immutable"):
            with store.connection(write=True) as db:
                db.execute(f"UPDATE peers SET {field}=? WHERE peer_id=?", (rejected, agent["peer_id"]))
    assert store.get(agent["peer_id"])["bound_user"] == "bob"
    with store.connection() as db:
        row = store.row(db, device["peer_id"])
        assert len(row["secret_hash"]) == 64 and row["secret_hash"] != device["secret"]
        assert row["secret_hint"] == device["secret"][-4:]


def test_three_levels_auth_and_public_identity(bridge):
    module, client = bridge
    device_a = module.PEERS.create("device", "alice")
    device_b = module.PEERS.create("device", "bob")
    unknown = str(uuid.uuid4())
    path = "/v1/version"
    invalid = client.get(path, headers={"Authorization": "Bearer bad", "X-Kemo-Peer-Id": unknown})
    unregistered = client.get(path, headers={**peer_headers(device_a), "X-Kemo-Peer-Id": unknown})
    mismatch = client.get(path, headers={**peer_headers(device_a), "X-Kemo-Peer-Id": device_b["peer_id"]})
    assert (invalid.status_code, invalid.json()) == (401, {"error": "auth_failed"})
    assert (unregistered.status_code, unregistered.json()) == (403, {"error": "peer_not_registered"})
    assert (mismatch.status_code, mismatch.json()) == (403, {"error": "identity_mismatch"})
    event = module.PEERS.audit_events(peer_id=device_b["peer_id"])[0]
    assert event["action"] == "identity_mismatch"
    assert event["detail"]["matched_peer_id"] == device_a["peer_id"]
    response = client.get(path, headers=peer_headers(device_a))
    assert response.json() == {"service": "kemo_bridge", "version": "2.1.0", "public_url": None}
    assert response.headers["X-Kemo-Peer-Id"] == device_a["peer_id"]
    assert response.headers["X-Kemo-Bound-User"] == "alice"
    assert response.headers["X-Kemo-Channel"] == "device"
    assert "expand_read" in response.headers["X-Kemo-Capabilities"].split(",")
    assert device_a["secret"] not in json.dumps(module.PEERS.audit_events())
    from admin_console import save_public_url
    save_public_url("https://bridge.example.test/base/")
    assert client.get(path, headers=peer_headers(device_a)).json()["public_url"] == "https://bridge.example.test/base"


def test_rotate_revoke_and_unknown_capabilities(bridge):
    from capabilities import capabilities_for

    module, client = bridge
    assert capabilities_for("unknown") == frozenset()
    assert capabilities_for("agent") == {"chat", "status", "artifact_upload", "artifact_download"}
    peer = module.PEERS.create("device", "alice")
    rotated = module.PEERS.rotate(peer["peer_id"])
    assert rotated["secret"] != peer["secret"]
    assert client.get("/v1/version", headers=peer_headers(peer)).status_code == 401
    assert client.get("/v1/version", headers=peer_headers(rotated)).status_code == 200
    module.PEERS.revoke(peer["peer_id"])
    assert client.get("/v1/version", headers=peer_headers(rotated)).status_code == 401
    with pytest.raises(sqlite3.IntegrityError, match="peer_id_reserved"):
        with module.PEERS.connection(write=True) as db:
            db.execute("DELETE FROM peers WHERE peer_id=?", (peer["peer_id"],))
    actions = {r["action"] for r in module.PEERS.audit_events(peer_id=peer["peer_id"])}
    assert {"peer_create", "auth_ok", "secret_rotate", "revoke"} <= actions
