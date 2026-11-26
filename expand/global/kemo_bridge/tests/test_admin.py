import json
import subprocess
import sys
from uuid import UUID, uuid4

import httpx
import yaml
from fastapi.testclient import TestClient

from conftest import peer_headers


def console(operation, **data):
    from workspace_actions import execute
    return execute("bridge_admin", {"operation": operation, "data": data},
                   context={"user": "alice", "source": "module_ui", "workspace_instance": True})


def test_private_management_transport_is_host_workspace_only(bridge, monkeypatch):
    import start_expand
    from workspace_actions import execute

    module, local = bridge
    monkeypatch.setattr(start_expand, "status", lambda: {"active": False})
    recorded = module.PEERS.create("device", "bob")
    for client in (local, TestClient(module.app, client=("10.66.0.2", 30101))):
        for headers in ({}, peer_headers(recorded), {"X-Kemo-Internal": module.CONFIG["session_secret"]},
                        {"X-Kemo-Admin": "legacy-key"}):
            assert client.get("/admin/api/snapshot", headers=headers).status_code == 404
            assert client.post("/admin/api/peers", headers=headers).status_code == 404
        assert client.get("/admin").status_code == 404
        assert client.get("/admin/static/admin.js").status_code == 404

    for source in (None, "expand_call", "module_panel", "module_panel_status", "module_ui"):
        denied = execute("bridge_admin", {"operation": "snapshot"}, context={"source": source})
        assert denied["status_code"] == 403
    assert not start_expand.execute("bridge_admin", {"operation": "snapshot"},
                                    context={"user": "alice", "source": "module_ui"})["ok"]
    data = console("snapshot")
    assert data["state"]["bridge_state"] == "已停止"
    assert data["mobile_users"] == ["bob"] and data["external_count"] == data["traffic_count"] == 0
    assert recorded["secret"] not in json.dumps(data)


def test_instance_identity_and_bound_user_are_stable_until_authorized_change(bridge):
    from peer_store import PeerStore

    module, client = bridge
    store = module.PEERS
    initial_uuid = console("snapshot")["instance_uuid"]
    assert UUID(initial_uuid).version == 4
    assert PeerStore(store.path, store.users_root).local_identity()["instance_uuid"] == initial_uuid
    remote_id = str(uuid4())
    assert console("create_external", peer_id=remote_id)["error"] == "exposed_user_required"
    assert console("set_exposed_user", bound_user="missing")["status_code"] == 400
    assert console("set_exposed_user", bound_user="alice")["ok"]
    assert console("create_external", peer_id=initial_uuid)["error"] == "self_binding"
    assert console("create_external", peer_id=remote_id, bound_user="bob")["status_code"] == 403
    peer = console("create_external", peer_id=remote_id, label="远端实例")
    assert (peer["peer_id"], peer["channel"], peer["bound_user"]) == (remote_id, "agent", "alice")
    assert console("create_external", peer_id=remote_id)["status_code"] == 409
    assert console("set_exposed_user", bound_user="bob")["error"] == "external_user_in_use"
    assert console("edit_peer", peer_id=remote_id, bound_user="bob")["status_code"] == 403
    assert console("edit_peer", peer_id=remote_id, channel="device")["status_code"] == 403
    assert console("edit_peer", peer_id=remote_id, label="改名")["label"] == "改名"

    path = "/peer/v1/version"
    assert client.get(path).status_code == 401
    assert client.get(path, headers=peer_headers(peer)).json()["instance_uuid"] == initial_uuid
    assert client.get("/v1/version", headers=peer_headers(peer)).status_code == 403
    assert console("copy_external_key", peer_id=remote_id)["clipboard_text"] == peer["secret"]
    staged = console("snapshot")
    assert staged["external_count"] == 1 and staged["exposed_user"] == "alice"
    assert staged["peers"][0]["can_copy"] and peer["secret"] not in json.dumps(staged)

    new_uuid = console("replace_instance_uuid")["instance_uuid"]
    assert new_uuid != initial_uuid and UUID(new_uuid).version == 4
    assert client.get(path, headers=peer_headers(peer)).json()["instance_uuid"] == new_uuid
    assert PeerStore(store.path, store.users_root).local_identity()["instance_uuid"] == new_uuid
    assert console("save_public_url", public_url="https://bridge.example.test/bridge/")["ok"]
    assert client.get(path, headers=peer_headers(peer)).json()["public_url"] == "https://bridge.example.test/bridge"

    rotated = console("rotate_peer", peer_id=remote_id)
    assert rotated["secret"] != peer["secret"]
    assert console("copy_external_key", peer_id=remote_id)["clipboard_text"] == rotated["secret"]
    assert client.get(path, headers=peer_headers(peer)).status_code == 401
    assert client.get(path, headers=peer_headers(rotated)).status_code == 200
    assert console("revoke_peer", peer_id=remote_id)["revoked_at"]
    assert client.get(path, headers=peer_headers(rotated)).status_code == 401
    assert console("snapshot")["external_count"] == 0
    assert console("copy_external_key", peer_id=remote_id)["status_code"] == 403
    assert console("set_exposed_user", bound_user="bob")["ok"]
    assert console("create_external", peer_id=remote_id)["error"] == "peer_id_reserved"

    phone = console("create_device", bound_user="alice", label="我的手机")
    assert client.get("/v1/version", headers=peer_headers(phone)).status_code == 200
    assert console("copy_external_key", peer_id=phone["peer_id"])["status_code"] == 403
    assert console("snapshot")["mobile_users"] == ["alice"]
    assert phone["secret"] not in json.dumps(store.audit_events())


def test_old_credentials_stay_separate_and_readable_in_workspace(bridge):
    module, client = bridge
    peer_id = str(uuid4())
    row = {"url": "https://other.example.test", "peer_id": peer_id, "label": "旧凭据", "secret": "private-test-key"}
    vault = module.BASE_DIR / "_workspace_credentials.yaml"
    vault.write_text(yaml.safe_dump({"access_key": "unused-admin-key", "outbound": [row]}), encoding="utf-8")
    previous = vault.read_bytes()
    visible = [{k: v for k, v in row.items() if k != "secret"}]
    assert console("list_legacy_credentials")["items"] == visible
    assert console("snapshot", view="outbound")["legacy_items"] == visible
    assert console("copy_legacy_key", url=row["url"], peer_id=peer_id)["clipboard_text"] == row["secret"]
    assert client.get("/peer/v1/version", headers=peer_headers(row)).status_code == 401
    assert console("snapshot")["external_count"] == 0 and vault.read_bytes() == previous


def test_outbound_snapshot_does_not_probe_daemon_or_hide_cards_if_old_vault_is_invalid(bridge, monkeypatch):
    import start_expand

    module, _ = bridge
    assert console("set_exposed_user", bound_user="alice")["ok"]
    console("create_device", bound_user="bob")
    peer = console("create_external", peer_id=str(uuid4()))
    revoked = console("create_external", peer_id=str(uuid4()))
    console("revoke_peer", peer_id=revoked["peer_id"])
    (module.BASE_DIR / "_workspace_credentials.yaml").write_text("outbound: [", encoding="utf-8")

    def do_not_probe():
        raise AssertionError("External credentials do not depend on daemon health")

    monkeypatch.setattr(start_expand, "status", do_not_probe)
    view = console("snapshot", view="outbound")
    assert view["exposed_user"] == "alice"
    assert [row["peer_id"] for row in view["peers"]] == [peer["peer_id"]]
    assert view["peers"][0]["can_copy"] and peer["secret"] not in json.dumps(view)
    assert view["legacy_items"] == [] and view["legacy_error"] == "workspace_credentials_unavailable"


def test_read_only_workspace_child_does_not_load_http_framework(bridge):
    module, _ = bridge
    code = """
import sys
sys.path.insert(0, '.')
from workspace_actions import execute

context = {"source": "module_ui", "workspace_instance": True}
for data in ({"operation": "snapshot"}, {"operation": "snapshot", "data": {"view": "outbound"}},
             {"operation": "logs"}, {"operation": "list_legacy_credentials"}):
    assert execute("bridge_admin", data, context=context).get("ok") is not False
assert execute("panel_status", {}, context={"source": "module_panel_status"})["bridge_state"]
assert "fastapi" not in sys.modules and "pydantic" not in sys.modules
"""
    subprocess.run([sys.executable, "-I", "-B", "-c", code], cwd=module.BASE_DIR, check=True, timeout=10)


def test_console_counts_only_recent_payload_traffic(bridge, monkeypatch):
    module, client = bridge
    assert console("set_exposed_user", bound_user="alice")["ok"]
    peer = console("create_external", peer_id=str(uuid4()))
    headers = peer_headers(peer)
    assert client.get("/peer/v1/version", headers=headers).status_code == 200
    assert client.get("/peer/v1/version", headers={}).status_code == 401
    assert client.get("/v1/cron", headers=headers).status_code == 403
    assert console("logs")["items"] == []

    async def upstream(*args, **kwargs):
        events = [
            {"type": "reasoning_delta", "content": "private-reasoning"},
            {"type": "text_delta", "content": "private-output"},
            {"type": "text_delta", "content": "second-private-delta"},
            {"type": "done", "metadata": {"status": "completed"}},
        ]
        return httpx.Response(200, content="".join(f"data: {json.dumps(e)}\n\n" for e in events))

    monkeypatch.setattr(module.UPSTREAM, "open_stream", upstream)
    with client:
        received = client.post("/peer/v1/chat", headers=headers, json={"session_id": "s", "prompt": "private-input"})
    assert received.status_code == 200 and "private-output" in received.text
    assert client.post("/peer/v1/upload", headers=headers, files={"file": ("private.txt", b"file")}).status_code == 200
    path = module.USERS_DIR / "alice" / "download" / "private-download.txt"
    path.parent.mkdir()
    path.write_bytes(b"contents")
    grant = console("create_grant", peer_id=peer["peer_id"], file_path=str(path), media_kind="file")
    download = "/peer/v1/artifacts/" + grant["grant_id"] + "/content"
    assert client.get(download, headers={**headers, "Range": "bytes=99-105"}).status_code == 416
    assert client.get(download, headers={**headers, "Range": "bytes=0-2"}).content == b"con"
    console("revoke_grant", grant_id=grant["grant_id"])
    assert client.get(download, headers=headers).status_code == 403

    expected = ["artifact_download", "artifact_upload", "message_sent", "message_received"]
    items = console("logs", peer_id=peer["peer_id"])["items"]
    assert [item["action"] for item in items] == expected
    assert console("snapshot")["traffic_count"] == len(expected)
    assert all(item["peer_id"] == peer["peer_id"] for item in items)
    audit = module.PEERS.audit_events()
    assert {"auth_ok", "auth_fail", "capability_not_granted", "artifact_denied"} <= {i["action"] for i in audit}
    for private in ("private-input", "private-output", "private.txt", "private-download.txt", peer["secret"]):
        assert private not in json.dumps(items)
    for private in ("private-input", "private-output", peer["secret"]):
        assert private not in json.dumps(audit)

    with module.PEERS.connection(write=True) as db:
        db.execute("UPDATE peer_audit SET created_at='2020-01-01T00:00:00+00:00' WHERE action='message_received'")
    assert [item["action"] for item in console("logs")["items"]] == expected[:-1]
    assert console("snapshot")["traffic_count"] == len(expected) - 1

def test_workspace_manifest_static_safety_and_aggregate_only_injection(bridge, tmp_path):
    import data_update
    from run.modules import read_manifest
    from web.services.module_workspace_schema import workspace_declaration

    module, client = bridge
    meta = read_manifest(module.BASE_DIR, kind_hint="expand", scope="global")
    declaration = workspace_declaration(meta)
    assert {action["id"] for action in declaration["actions"]} == {"bridge_admin"}
    assert meta.ui_action_entry == module.BASE_DIR / "workspace_actions.py"
    assert (module.BASE_DIR / "ui/dist/main.js").is_file()
    html = (module.BASE_DIR / "ui/dist/index.html").read_text("utf-8")
    assert all(f'data-view="{view}"' in html for view in ("overview", "bindings", "outbound", "logs"))
    assert "127.0.0.1" not in html and "admin_console.py" not in html
    a = module.PEERS.create("device", "alice", label="private-label")
    b = module.PEERS.create("agent", "bob")
    assert client.get("/v1/version", headers=peer_headers(a)).status_code == 200
    client.get("/v1/version", headers={**peer_headers(a), "X-Kemo-Peer-Id": b["peer_id"]})
    data_update._write_online_output({"upstream": "online"}, update_time="2026-11-21 12:00:00")
    output = (module.BASE_DIR / "input_data.md").read_text("utf-8")
    assert "手机 1，外部智能体 1" in output and "身份不一致：1" in output
    for private in ("alice", "bob", "private-label", a["peer_id"], b["peer_id"], a["secret"], b["secret"]):
        assert private not in output


def test_packer_protects_bridge_runtime_file_names():
    from deploy.core.policy import protected

    base = "expand/global/kemo_bridge/"
    for name in ("config.json", "users.json", "_admin_key.sha256", "_workspace_credentials.yaml", "_bridge_peers.sqlite3",
                 "_bridge_peers.sqlite3-wal", "_bridge_runs.sqlite3", "_connections.json",
                 "_activated.json", "_server.pid", "logs/bridge.log"):
        assert protected(base + name), name
    assert not protected(base + "ui/admin/index.html")
