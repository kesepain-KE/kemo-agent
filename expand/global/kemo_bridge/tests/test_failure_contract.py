import hashlib
import json

import httpx
import pytest

from conftest import peer_headers


PROMPT = "仅供请求使用的提示"
SECRET = "private-response-marker"
BODY = b'{"error":{"code":"private-code","message":"private-response-marker"}}'


def _mock_upstream(module, monkeypatch, handler):
    upstream = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    monkeypatch.setattr(module.UPSTREAM, "client", upstream)


def _reply_and_record(bridge):
    module, client = bridge
    with client:
        peer = module.PEERS.create("agent", "alice")
        reply = client.post("/peer/v1/chat", headers=peer_headers(peer), json={
            "session_id": "session", "run_id": "turn", "prompt": PROMPT,
        })
        assert reply.status_code == 200
        run_id = f"run_kb_{peer['peer_id'].replace('-', '')}_{hashlib.sha256(b'turn').hexdigest()[:32]}"
        run = module.RUNS.snapshot(peer["peer_id"], run_id)
    return reply, run


def _frames(reply):
    return [json.loads(line[6:]) for line in reply.content.splitlines() if line.startswith(b"data: ")]


def _forensics_file(module):
    return module.BASE_DIR / "logs" / "forensics.log"


@pytest.mark.parametrize("auth,code,status", [
    (None, "upstream_auth_missing", 200),
    ("login", "upstream_auth_rejected", 401),
    ("token", "upstream_auth_rejected", 401),
])
def test_peer_auth_failures_have_public_code_and_redacted_forensics(bridge, monkeypatch, auth, code, status):
    module, _ = bridge
    paths = []
    if auth == "login":
        monkeypatch.setattr(module.UPSTREAM, "username", "bridge")
        monkeypatch.setattr(module.UPSTREAM, "password", "private-password-marker")
    elif auth == "token":
        monkeypatch.setattr(module.UPSTREAM, "token", "private-token-marker")
    body = b'{"enabled":true,"authenticated":false,"private":"private-response-marker"}'

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/api/auth/status":
            return httpx.Response(200, content=body)
        assert request.url.path == f"/api/auth/{auth}"
        return httpx.Response(401, content=BODY, headers={"X-Private": "private-header-marker"})

    _mock_upstream(module, monkeypatch, handler)
    reply, run = _reply_and_record(bridge)
    assert paths == ["/api/auth/status", f"/api/auth/{auth or 'status'}"]
    assert _frames(reply) == [{"type": "done", "status": "failed", "code": code, "transient": False, "phase": "auth"}]
    assert run["error"] == ("上游已启用认证，但桥接未配置凭据。" if auth is None else "上游拒绝了桥接的认证凭据。")
    assert json.loads(run["events"][-1]["data"])["error"] == {
        "message": run["error"], "code": code, "transient": False, "phase": "auth",
    }
    log = _forensics_file(module)
    rendered = log.read_text("utf-8")
    raw = body if auth is None else BODY
    assert json.loads(rendered) == {
        "run_id": run["run_id"], "phase": "auth", "code": code, "attempt": 1,
        "status": status, "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest(),
    }
    for private in (SECRET, PROMPT, "private-code", "private-password-marker", "private-token-marker",
                    "private-header-marker", "authorization"):
        assert private not in rendered
        assert private not in reply.text
    assert not log.with_name("bridge.log").exists()


class _ReadTimeout(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"type":"text_delta","content":"partial"}\n\n'
        raise httpx.ReadTimeout(SECRET)


@pytest.mark.parametrize("case,code,transient,phase,status,attempt", [
    ("connect", "upstream_unreachable", True, "connect", None, 1),
    ("read", "upstream_timeout", True, "stream", 200, 1),
    ("http_401", "upstream_http_error", False, "request", 401, 2),
    ("unreadable_401", "upstream_http_error", False, "request", 401, 2),
    ("http_503", "upstream_http_error", True, "request", 503, 1),
    ("invalid_json", "upstream_invalid_payload", False, "stream", 200, 1),
    ("eof", "upstream_stream_incomplete", True, "stream", 200, 1),
    ("unexpected", "bridge_internal", False, "bridge", None, 1),
])
def test_peer_stream_failures_keep_diagnostics_out_of_events(
    bridge, monkeypatch, capsys, case, code, transient, phase, status, attempt,
):
    module, _ = bridge

    def handler(request):
        if request.url.path == "/api/auth/status":
            return httpx.Response(200, json={"enabled": True, "authenticated": True})
        assert request.url.path == "/api/chat"
        if case == "connect":
            raise httpx.ConnectError(SECRET)
        if case == "read":
            return httpx.Response(200, stream=_ReadTimeout())
        if case == "unreadable_401":
            return httpx.Response(401, stream=_ReadTimeout())
        if case.startswith("http_"):
            return httpx.Response(int(case[5:]), content=BODY, headers={"X-Private": SECRET})
        if case == "invalid_json":
            return httpx.Response(200, content=f"data: {{{SECRET}\n\n")
        if case == "unexpected":
            raise RuntimeError(SECRET)
        return httpx.Response(200, content=b'data: {"type":"text_delta","content":"partial"}\n\n')

    _mock_upstream(module, monkeypatch, handler)
    reply, run = _reply_and_record(bridge)
    assert _frames(reply)[-1] == {
        "type": "done", "status": "failed", "code": code, "transient": transient, "phase": phase,
    }
    assert run["status"] == "failed"
    assert 0 < len(run["error"]) <= 500
    assert SECRET not in run["error"] + run["events"][-1]["data"] + reply.text
    rendered = _forensics_file(module).read_text("utf-8")
    assert SECRET not in rendered
    log = json.loads(rendered)
    assert log["code"] == code and log["phase"] == phase and log["attempt"] == attempt
    assert log.get("status") == status
    if case == "unreadable_401":
        assert "body_bytes" not in log and "body_sha256" not in log
    if case == "unexpected":
        assert "RuntimeError" in run["error"]
        assert [log["traceback"][i]["function"] for i in (0, -1)] == ["_pump", "handler"]
        assert all(set(frame) == {"file", "line", "function"} for frame in log["traceback"])
        assert "traceback" not in run["events"][-1]["data"] and "traceback" not in reply.text
        assert SECRET not in capsys.readouterr().err
    else:
        assert "traceback" not in log


@pytest.mark.parametrize("kind,status,code,transient", [
    ("done", "failed", "bridge_internal", False),
    ("error", "failed", "bridge_internal", False),
    ("done", "interrupted", "upstream_stream_incomplete", True),
    ("done", "cancelled", None, False),
])
def test_upstream_terminal_uses_safe_non_success_projection(bridge, monkeypatch, kind, status, code, transient):
    module, _ = bridge
    stream = {"type": kind, "metadata": {"status": status, "detail": SECRET},
              "error": {"message": SECRET, "code": "upstream_auth_missing", "transient": False, "phase": "auth"}}

    async def open_stream(*_args, **_kwargs):
        return httpx.Response(200, content=f"data: {json.dumps(stream)}\n\n")

    monkeypatch.setattr(module.UPSTREAM, "open_stream", open_stream)
    reply, run = _reply_and_record(bridge)
    assert _frames(reply) == [{"type": "done", "status": status, "code": code, "transient": transient, "phase": "stream"}]
    assert run["status"] == status
    assert SECRET not in run["events"][-1]["data"] + run["error"] + reply.text
    assert _forensics_file(module).exists() == (status != "cancelled")


@pytest.mark.parametrize("close_error", [False, True])
def test_peer_completed_wire_bytes_are_unchanged(bridge, monkeypatch, close_error):
    module, _ = bridge

    class ReplyStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                'data: {"type":"text_delta","content":"在"}\n\n'
                'data: {"type":"done","metadata":{"status":"completed"}}\n\n'
            ).encode("utf-8")

        async def aclose(self):
            if close_error:
                raise RuntimeError(SECRET)

    async def open_stream(*_args, **_kwargs):
        return httpx.Response(200, stream=ReplyStream())

    monkeypatch.setattr(module.UPSTREAM, "open_stream", open_stream)
    reply, _ = _reply_and_record(bridge)
    assert [line for line in reply.content.splitlines() if line.startswith(b"data: ")] == [
        'data: {"type": "text_delta", "content": "在"}'.encode("utf-8"),
        b'data: {"type": "done", "status": "completed"}',
    ]
    assert b'id: 2\ndata: {"type": "done", "status": "completed"}\n\n' in reply.content
    log = _forensics_file(module)
    if close_error:
        rendered = log.read_text("utf-8")
        assert json.loads(rendered)["traceback"][-1]["function"] == "aclose"
        assert SECRET not in rendered
    else:
        assert not log.exists()


def test_forensics_rotation_keeps_only_three_bounded_backups(bridge, monkeypatch):
    from forensics import ForensicsLog

    module, _ = bridge
    assert module.RUNS._forensics._handler.maxBytes == 5 * 1024 * 1024
    assert module.RUNS._forensics._handler.backupCount == 3
    monkeypatch.setattr("forensics.MAX_BYTES", 256)
    path = _forensics_file(module)
    log = ForensicsLog(path)
    try:
        for i in range(20):
            log.write(f"run_{i}", "stream", "upstream_timeout", attempt=1, status=200,
                      body_bytes=None, body_sha256=None, unexpected=None)
    finally:
        log.close()
    files = sorted(path.parent.iterdir())
    assert [entry.name for entry in files] == ["forensics.log", "forensics.log.1", "forensics.log.2", "forensics.log.3"]
    assert all(0 < entry.stat().st_size <= 256 for entry in files)
    assert json.loads(path.read_text("utf-8").splitlines()[-1])["run_id"] == "run_19"


def test_unwritable_forensics_still_returns_terminal_without_stderr_leak(bridge, monkeypatch, capsys):
    module, _ = bridge

    def open_log():
        raise PermissionError(SECRET)

    async def open_stream(*_args, **_kwargs):
        raise httpx.ConnectError(SECRET)

    monkeypatch.setattr(module.RUNS._forensics._handler, "_open", open_log)
    monkeypatch.setattr(module.UPSTREAM, "open_stream", open_stream)
    reply, run = _reply_and_record(bridge)
    assert _frames(reply) == [{
        "type": "done", "status": "failed", "code": "upstream_unreachable", "transient": True, "phase": "connect",
    }]
    assert run["error"] == "桥接无法连接上游服务；本地取证日志写入失败。"
    assert "PermissionError" not in capsys.readouterr().err
    assert not _forensics_file(module).exists()
    assert not _forensics_file(module).with_name("bridge.log").exists()


def test_mobile_keeps_its_error_envelope_and_does_not_log_supplied_run_id(bridge, monkeypatch):
    module, client = bridge

    async def open_stream(*_args, **_kwargs):
        raise httpx.ConnectError("private socket error")

    monkeypatch.setattr(module.UPSTREAM, "open_stream", open_stream)
    with client:
        peer = module.PEERS.create("device", "alice")
        reply = client.post("/v1/chat", headers=peer_headers(peer), json={
            "session_id": "session", "run_id": SECRET, "prompt": PROMPT,
        })
        assert reply.status_code == 200
        assert reply.headers["X-Kemo-Run-Id"] == SECRET
        assert _frames(reply)[-1]["type"] == "error"
        assert _frames(reply)[-1]["error"]["code"] == "upstream_unreachable"
    rendered = _forensics_file(module).read_text("utf-8")
    assert SECRET not in rendered
    assert json.loads(rendered)["run_id"] == "sha256:" + hashlib.sha256(SECRET.encode()).hexdigest()
