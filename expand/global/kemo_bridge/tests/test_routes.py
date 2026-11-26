import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from conftest import peer_headers


def test_branch_capability_denials_and_exact_readonly_projection(bridge, monkeypatch):
    module, client = bridge
    agent = module.PEERS.create("agent", "alice")
    device = module.PEERS.create("device", "alice")
    for path in ("/peer/v1/cron", "/peer/v1/expands/global/example/data", "/peer/v1/config", "/v1/cron"):
        response = client.get(path, headers=peer_headers(agent))
        assert (response.status_code, response.json()) == (403, {"error": "capability_not_granted"})
    assert client.get("/peer/v1/status", headers=peer_headers(device)).status_code == 403
    for method, path in (
        ("PUT", "/v1/whitelist"), ("POST", "/v1/expands/global/example/actions"),
        ("DELETE", "/v1/expands/global/example/data"), ("GET", "/v1/files/download"),
        ("POST", "/v1/auth/user"), ("POST", "/peer/v1/chat"),
    ):
        assert client.request(method, path, headers=peer_headers(device)).status_code == 403
    with pytest.raises(WebSocketDisconnect) as denial:
        with client.websocket_connect("/v1/ws?session_id=demo", headers=peer_headers(agent)):
            pass
    assert denial.value.code == 4403 and denial.value.reason == "capability_not_granted"
    events = module.PEERS.audit_events(peer_id=agent["peer_id"])
    assert sum(e["action"] == "capability_not_granted" for e in events) == 5

    async def upstream(method, path, **options):
        assert (method, path, options["params"]) == ("GET", "/api/users/alice/expand", {"include_files": False})
        return {"expands": [{"scope": "global", "items": [
            {"name": "example", "active_for_main_agent": True, "runtime": {"private": 5},
             "control_document": "operator text", "injected_markdown": "combined",
             "data_injected_markdown": "public data", "collected_markdown": "other"},
            {"name": "hidden", "data_injected_markdown": "", "collected_markdown": "private data"},
        ]}]}

    monkeypatch.setattr(module.UPSTREAM, "request_json", upstream)
    assert client.get("/v1/expands", headers=peer_headers(device)).json()["expands"][0]["items"] == [
        {"name": "example", "active_for_main_agent": True}, {"name": "hidden"},
    ]
    result = client.get("/v1/expands/data", headers=peer_headers(device)).json()
    assert result["summary"]["with_data"] == 1
    assert [item["data"] for item in result["expands"][0]["items"]] == ["public data", None]
    assert client.get("/v1/expands/global/hidden/data", headers=peer_headers(device)).json() == {"data": None}
    assert client.patch("/v1/config", headers=peer_headers(device),
                        json={"changes": {"expand": {"global_whitelist": ["example"]}}}).status_code == 403


def test_internal_auth_works_behind_non_loopback_proxy(bridge, monkeypatch):
    module, client = bridge

    async def health():
        return {"ok": True}

    monkeypatch.setattr(module.UPSTREAM, "health", health)
    for path in ("/internal/health", "/internal/device-command", "/internal/not-a-route"):
        response = client.get(path)
        assert (response.status_code, response.json()) == (403, {"error": "internal_unauthorized"})
    phone = module.PEERS.create("device", "alice")
    queued = client.post("/v1/device-commands", headers=peer_headers(phone), json={
        "action": "timer.start", "arguments": {"duration_seconds": 20},
    }).json()["command"]
    command = {"user": "alice", "device_id": phone["peer_id"], "command": queued}
    assert client.post("/internal/device-command", json=command).status_code == 403
    with TestClient(module.app, client=("10.66.0.3", 42424)) as proxy:
        credential = {"X-Kemo-Internal": module.CONFIG["session_secret"]}
        assert proxy.get("/internal/health", headers={"X-Kemo-Internal": "wrong"}).status_code == 403
        response = proxy.get("/internal/health", headers=credential)
        assert response.status_code == 200 and response.json()["service"] == "kemo_bridge"
        delivered = proxy.post("/internal/device-command", headers=credential, json=command)
        assert delivered.status_code == 200 and delivered.json() == {"ok": True, "delivered": False}



def test_device_queues_and_confirms_only_own_commands(bridge, monkeypatch):
    module, client = bridge
    device = module.PEERS.create("device", "alice")
    other = module.PEERS.create("device", "alice")

    async def lease(method, path, **options):
        assert method == "POST" and "/sessions/example/lease" in path
        return {"ok": True}

    monkeypatch.setattr(module.UPSTREAM, "request_json", lease)
    queued = client.post("/v1/device-commands", headers=peer_headers(device), json={
        "action": "timer.start", "arguments": {"duration_seconds": 20},
    })
    assert queued.status_code == 200
    command = queued.json()["command"]
    assert (command["status"], command["confirmation"], command["device_id"]) == (
        "queued", "system_ui", device["peer_id"],
    )
    path = "/v1/device-commands/" + command["command_id"]
    assert client.get(path, headers=peer_headers(other)).status_code == 404
    socket_path = "/v1/ws?session_id=example"
    impersonation = {**peer_headers(other), "X-Kemo-Device-Id": device["peer_id"]}
    with client.websocket_connect(socket_path, headers=impersonation) as socket:
        assert socket.receive_json()["data"]["device_id"] == other["peer_id"]
        socket.send_json({"type": "device.command.ack", "data": {
            "command_id": command["command_id"], "status": "completed",
        }})
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"
    assert client.get(path, headers=peer_headers(device)).json()["status"] == "queued"

    with client.websocket_connect(socket_path, headers=peer_headers(device)) as socket:
        connected = socket.receive_json()
        assert connected["data"]["peer_id"] == device["peer_id"]
        assert socket.receive_json()["data"]["command_id"] == command["command_id"]
        for status in ("received", "waiting_user", "completed"):
            socket.send_json({"type": "device.command.ack", "data": {
                "command_id": command["command_id"], "status": status,
            }})
        socket.send_json({"type": "ping"})
        assert socket.receive_json()["type"] == "pong"
        assert client.get(path, headers=peer_headers(device)).json()["status"] == "completed"
        module.PEERS.rotate(device["peer_id"])
        socket.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as revoked:
            socket.receive_json()
        assert revoked.value.code == 4401
    audits = module.PEERS.audit_events(peer_id=device["peer_id"])
    assert [r["detail"]["status"] for r in audits if r["action"] == "device_action_ack"] == [
        "completed", "waiting_user", "received",
    ]
    assert any(r["action"] == "device_action_submit" and r["detail"]["environment"] == "system_ui"
               for r in audits)


def test_agent_chat_scopes_ids_and_projects_stream_and_status(bridge, monkeypatch):
    module, client = bridge
    a = module.PEERS.create("agent", "alice")
    b = module.PEERS.create("agent", "alice")
    opened = []

    async def stream(method, path, *, json_body, sse):
        assert method == "POST" and path == "/api/chat" and sse
        opened.append(json_body)
        events = [
            {"type": "error", "metadata": {"retryable": True, "committed": False}},
            {"type": "reasoning_delta", "content": "private scratchpad"},
            {"type": "tool_result", "content": "/private/file", "authorization": "private token"},
            {"type": "text_delta", "content": "reply to " + json_body["prompt"], "metadata": {"private": True}},
            {"type": "done", "metadata": {"status": "completed", "private": True}},
        ]
        return httpx.Response(200, content="".join(f"data: {json.dumps(e)}\n\n" for e in events).encode())

    async def health():
        return {"status": "ok", "sessions": ["private session"]}

    monkeypatch.setattr(module.UPSTREAM, "open_stream", stream)
    monkeypatch.setattr(module.UPSTREAM, "health", health)
    assert client.get("/peer/v1/status", headers=peer_headers(a)).json() == {
        "online": True, "busy": False, "version": "2.1.0",
    }
    key = uuid4().hex
    for peer, name in ((a, "A"), (b, "B"), (a, "A")):
        reply = client.post("/peer/v1/chat", headers=peer_headers(peer), json={
            "session_id": "same", "run_id": key, "prompt": name,
        })
        assert reply.status_code == 200 and reply.headers["X-Kemo-Run-Id"] == key
        events = [json.loads(line[6:]) for line in reply.text.splitlines() if line.startswith("data: ")]
        assert events == [{"type": "text_delta", "content": f"reply to {name}"}, {"type": "done", "status": "completed"}]
    assert len(opened) == 2
    assert opened[0]["session_id"] != opened[1]["session_id"]
    assert opened[0]["run_id"] != opened[1]["run_id"]
    assert {item["user"] for item in opened} == {"alice"}
    assert client.post("/peer/v1/chat", headers=peer_headers(a), json={
        "session_id": "same", "prompt": "hello", "uploaded_files": ["someone-elses.txt"],
    }).status_code == 422
