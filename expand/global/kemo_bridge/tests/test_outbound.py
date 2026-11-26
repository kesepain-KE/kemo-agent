from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
import yaml

from conftest import peer_headers


def admin(operation, data):
    from workspace_actions import execute

    return execute("bridge_admin", {"operation": operation, "data": data},
                   context={"source": "module_ui", "workspace_instance": True})


def credential(**changes):
    return {"url": "http://remote.example.test", "peer_id": str(uuid4()),
            "label": "远端", "secret": "remote-issued-key", **changes}


def install(**changes):
    row = credential(**changes)
    result = admin("import_outbound_credential", row)
    assert result["ok"] and row["secret"] not in json.dumps(result)
    return result["target_id"]


def call(command, *, user="kesepain", **args):
    from start_expand import execute

    return execute(command, args, context={"user": user})


def mock_http(monkeypatch, handler, *, version=None, before_write=None):
    client = httpx.Client
    instance_uuid = str(uuid4())

    def route(request):
        if request.url.path == "/peer/v1/version":
            return version(request) if version else httpx.Response(200, json={"instance_uuid": instance_uuid})
        if trace := request.extensions.get("trace"):
            if before_write is not None:
                before_write()
            # MockTransport has no socket; enter the same pre-write hook before recording delivery.
            trace("http11.send_request_headers.started", {})
        return handler(request)

    def create(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return client(transport=httpx.MockTransport(route), **kwargs)

    monkeypatch.setattr(httpx, "Client", create)


def sse(run_id, payload):
    return httpx.Response(200, text=payload, headers={
        "content-type": "text/event-stream", "X-Kemo-Run-Id": run_id,
    })


def ledger_path(module):
    return module.BASE_DIR.parents[2] / "runtime" / "kemo_bridge_outbound.sqlite3"


def test_outbound_import_is_workspace_only_atomic_private_and_preserves_legacy_rows(bridge, monkeypatch):
    import outbound_credentials
    from workspace_actions import execute

    module, listener = bridge
    path = module.BASE_DIR / "_workspace_credentials.yaml"
    old = credential(label="旧凭据", secret="legacy-peer-key")
    path.write_text(yaml.safe_dump({"access_key": "retained", "outbound": [old]}), encoding="utf-8")
    before = path.read_bytes()
    row = credential(url="https://next.example.test/prefix/", allowed_users=["alice", "bob"])
    assert not call("import_outbound_credential", **row)["ok"]
    assert not execute("bridge_admin", {"operation": "import_outbound_credential", "data": row},
                       context={"source": "module_ui"})["ok"]
    assert listener.post("/internal/import_outbound_credential", json=row).status_code == 403
    assert path.read_bytes() == before

    replace = os.replace
    chmod = os.chmod
    replacements = []
    modes = []

    def record(source, destination):
        assert source.parent == destination.parent and source != destination
        if os.name != "nt":
            assert stat.S_IMODE(source.stat().st_mode) == 0o600
        replacements.append(destination)
        return replace(source, destination)

    monkeypatch.setattr(outbound_credentials.os, "replace", record)
    def record_mode(file, mode):
        modes.append(mode)
        return chmod(file, mode)

    monkeypatch.setattr(outbound_credentials.os, "chmod", record_mode)
    target = install(**row)
    assert replacements == [path] and modes == [0o600]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert data == {"access_key": "retained", "outbound": [old, {**row, "url": row["url"].rstrip("/")}]}
    assert install(**{**row, "secret": "rotated-key"}) == target
    assert len(yaml.safe_load(path.read_text(encoding="utf-8"))["outbound"]) == 2


@pytest.mark.parametrize("bad", ["alice", ["alice", "alice"], [""], [False], ["x" * 129], [str(i) for i in range(65)]])
def test_one_invalid_rule_makes_the_whole_vault_unavailable(bridge, bad):
    module, _ = bridge
    path = module.BASE_DIR / "_workspace_credentials.yaml"
    valid = credential()
    path.write_text(yaml.safe_dump({"outbound": [valid, credential(allowed_users=bad)]}), encoding="utf-8")
    assert call("peer_list") == {"ok": False, "error": "workspace_credentials_unavailable"}
    assert admin("list_legacy_credentials", {})["error"] == "workspace_credentials_unavailable"


def test_peer_acl_uses_context_and_lists_only_public_fields(bridge):
    target = install()
    shared = install(url="https://other.example.test:8443/", allowed_users=["alice"])
    install(url="http://disabled.example.test", allowed_users=[])
    assert call("peer_list")["targets"] == [
        {"target_id": target, "label": "远端", "url": "http://remote.example.test"},
    ]
    assert call("peer_list", user="alice")["targets"] == [
        {"target_id": shared, "label": "远端", "url": "https://other.example.test:8443"},
    ]
    for command in ("peer_status", "peer_chat_submit", "peer_chat_query", "peer_chat"):
        hidden = call(command, user="alice", target_id=target, allowed_users=["alice"], user_name="kesepain")
        assert hidden == call(command, user="alice", target_id="never-configured")
        assert hidden == {"ok": False, "error": "target_not_found"}


@pytest.mark.parametrize("url", ["ftp://example.test", "http://user:password@example.test", "http://example.test/?"])
def test_invalid_outbound_url_is_rejected_without_echo(bridge, url):
    result = admin("import_outbound_credential", credential(url=url))
    assert result == {"ok": False, "error": "invalid_outbound_credential", "status_code": 400}


def test_status_calls_targets_in_parallel_and_never_follows_redirects(bridge, monkeypatch):
    first = install()
    second = install(url="https://other.example.test")
    gate = threading.Barrier(2)
    seen = []

    def status(request):
        assert request.url.path == "/peer/v1/status"
        seen.append(request.url.host)
        gate.wait(timeout=2)
        if request.url.host == "other.example.test":
            return httpx.Response(302, headers={"Location": "https://unconfigured.example.test"})
        return httpx.Response(200, json={"online": True, "busy": False, "version": "test",
                                        "secret": "do-not-forward"})

    mock_http(monkeypatch, status)
    results = {item["target_id"]: item for item in call("peer_status")["targets"]}
    assert set(seen) == {"remote.example.test", "other.example.test"}
    assert results[first]["online"] is True and results[first]["busy"] is False
    assert results[second]["error"] == "upstream_http_error"
    assert "do-not-forward" not in json.dumps(results)


@pytest.mark.parametrize("reject_version", [False, True], ids=["chat", "version"])
def test_401_is_a_chat_failure_not_an_action_error(bridge, monkeypatch, reject_version):
    target = install()

    def reject(request):
        assert request.headers["Authorization"] == "Bearer remote-issued-key"
        assert request.url.path == ("/peer/v1/version" if reject_version else "/peer/v1/chat")
        return httpx.Response(401, text="private-response")

    mock_http(monkeypatch, reject, version=reject if reject_version else None)
    assert call("peer_chat_submit", target_id=target, session_id="raw-session", run_id="rejected", prompt="hi") == {
        "ok": True, "state": "failed", "frames": 1, "text": "", "run_id": "rejected", "after_event": 0,
        "failure": {"code": "upstream_auth_rejected", "transient": False, "phase": "auth"},
    }


def test_missing_local_bearer_is_not_rejected_remote_auth(bridge):
    module, _ = bridge
    (module.BASE_DIR / "_workspace_credentials.yaml").write_text(
        yaml.safe_dump({"outbound": [credential(secret="")]}), encoding="utf-8",
    )
    target = call("peer_list")["targets"][0]["target_id"]
    assert call("peer_chat_submit", target_id=target, session_id="raw", prompt="hello") == {
        "ok": False, "error": "upstream_auth_missing",
    }


def test_integrates_real_peer_projection_paging_and_conversation_isolation(bridge, monkeypatch):
    import agent_routes

    module, listener = bridge
    monkeypatch.setattr(module.EVENTS, "start", lambda: None)
    monkeypatch.setattr(agent_routes, "_PAGE_EVENTS", 2)
    peer = module.PEERS.create("agent", "alice")
    reverse = module.PEERS.create("agent", "bob")
    target = install(peer_id=peer["peer_id"], secret=peer["secret"], allowed_users=["alice", "bob"])
    opened = []
    wire = []
    released = threading.Event()

    async def upstream(method, path, *, json_body, sse):
        opened.append(json_body)
        # No upstream byte is available at the time the initiating POST returns.
        while not released.is_set():
            await asyncio.sleep(0.01)
        events = [
            {"type": "text_delta", "content": "第一段"},
            {"type": "reasoning_delta", "content": "hidden-reasoning"},
            {"type": "text_delta", "content": "第二段"},
            {"type": "done", "status": "completed"},
        ]
        return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))

    monkeypatch.setattr(module.UPSTREAM, "open_stream", upstream)

    def forward(request):
        assert request.url.host == "remote.example.test"
        assert request.headers["Authorization"] == f"Bearer {peer['secret']}"
        assert request.headers["Authorization"] != f"Bearer {reverse['secret']}"
        assert request.headers["X-Kemo-Peer-Id"] == peer["peer_id"]
        wire.append(request)
        reply = listener.request(request.method, request.url.raw_path.decode(), headers=request.headers,
                                 content=request.content)
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    mock_http(monkeypatch, forward, version=forward)
    cmd = dict(target_id=target, user="alice", session_id="bridge-intro", run_id="first-round", prompt="private prompt")
    with listener:
        ack = call("peer_chat_submit", url="http://not-the-configured-url", **cmd)
        assert (ack["state"], ack["frames"], ack["text"], ack["after_event"]) == ("running", 0, "", 0)
        assert not released.is_set()
        assert call("peer_chat_submit", **cmd) == ack
        assert call("peer_chat_submit", **{**cmd, "prompt": "different"}) == {
            "ok": False, "error": "run_prompt_conflict",
        }
        assert call("peer_chat_submit", **{**cmd, "run_id": "other-run", "user": "bob"})["error"] == "session_scope_conflict"
        assert call("peer_chat_query", target_id=target, user="bob", run_id="first-round")["error"] == "run_not_found"
        assert call("peer_chat_query", target_id=target, user="alice", run_id="first-round")["text"] == ""
        released.set()

        deadline = time.monotonic() + 4
        while module.RUNS.is_busy(peer["peer_id"]) and time.monotonic() < deadline:
            time.sleep(0.02)
        partial = call("peer_chat_query", target_id=target, user="alice", run_id="first-round")
        assert (partial["state"], partial["frames"], partial["text"], partial["after_event"]) == ("running", 1, "第一段", 2)
        final = call("peer_chat_query", target_id=target, user="alice", run_id="first-round",
                     after_event=partial["after_event"], session_id="bridge-intro")
        assert (final["state"], final["frames"], final["text"], final["after_event"]) == ("completed", 2, "第二段", 4)
        empty = call("peer_chat_query", target_id=target, user="alice", run_id="first-round", after_event=4)
        assert empty["state"] == "completed" and empty["frames"] == 0 and empty["text"] == ""
        full = call("peer_chat", **{**cmd, "run_id": "second-round", "prompt": "next"})
        assert (full["state"], full["frames"], full["text"]) == ("completed", 3, "第一段第二段")
        for marker in ("id:", "data:", "hidden-reasoning", "private prompt", peer["secret"]):
            assert marker not in json.dumps(full)

        for identity, session, run_id in (
            (peer, "wrong-session", "first-round"), (reverse, "bridge-intro", "first-round"),
            (peer, "bridge-intro", "absent-or-expired"),
        ):
            response = listener.get("/peer/v1/chat", params={"session_id": session},
                                    headers={**peer_headers(identity), "X-Kemo-Run-Id": run_id})
            assert response.status_code == 404

    assert [json.loads(request.content)["run_id"] for request in wire if request.method == "POST"] == [
        "first-round", "second-round",
    ]
    assert [request.url.path for request in wire if request.url.path == "/peer/v1/version"] == ["/peer/v1/version"]
    assert all(request.url.params["session_id"] == "bridge-intro" for request in wire
               if request.method == "GET" and request.url.path == "/peer/v1/chat")
    assert len(opened) == 2 and opened[0]["session_id"] == opened[1]["session_id"]
    assert opened[0]["session_id"].startswith("kb_") and opened[0]["run_id"] != opened[1]["run_id"]
    db = module.BASE_DIR.parents[2] / "runtime" / "kemo_bridge_outbound.sqlite3"
    assert b"private prompt" not in db.read_bytes() and peer["secret"].encode() not in db.read_bytes()


def test_interrupted_stream_retains_partial_text_without_raw_frames(bridge, monkeypatch):
    target = install()

    def truncated(request):
        if request.method == "POST":
            return sse("partial", 'data: {"type":"accepted"}\n\n')
        return sse("partial", 'id: 1\ndata: {"type":"text_delta","content":"partial text"}\n\n'
                              'id: 2\ndata: {"type":"done","status":"completed"}')

    mock_http(monkeypatch, truncated)
    result = call("peer_chat", target_id=target, run_id="partial", session_id="s", prompt="hello")
    waited = result.pop("waited_seconds")
    assert 0 <= waited < 10
    assert result.pop("budget_remaining_seconds") == pytest.approx(60 - waited, abs=0.001)
    assert result.pop("next_action") is None
    assert result == {
        "ok": True, "state": "failed", "frames": 1, "text": "partial text", "run_id": "partial", "after_event": 1,
        "failure": {"code": "upstream_stream_incomplete", "transient": True, "phase": "stream"},
    }


@pytest.mark.parametrize("lost", ["timeout", "empty-sse"])
def test_lost_ack_keeps_idempotent_receipt_and_queries_same_external_run(bridge, monkeypatch, lost):
    import bridge_client

    target = install()
    seen = []

    def lost_ack(request):
        seen.append(request)
        if request.method == "POST":
            if lost == "timeout":
                raise httpx.ReadTimeout("lost reply; request may have reached peer")
            return sse("lost-ack", "")
        assert request.headers["X-Kemo-Run-Id"] == "lost-ack"
        assert request.url.params["session_id"] == "raw"
        if len(seen) == 2:
            # The remote may not have journaled the POST yet.
            return httpx.Response(404, headers={"X-Kemo-Run-Id": "lost-ack"})
        return sse("lost-ack", 'id: 1\ndata: {"type":"done","status":"completed"}\n\n')

    mock_http(monkeypatch, lost_ack)
    args = dict(target_id=target, run_id="lost-ack", session_id="raw", prompt="perform once")
    unknown = call("peer_chat_submit", **args)
    assert unknown["state"] == "unknown" and unknown["run_id"] == "lost-ack"
    assert "不要换新编号" in unknown["message"]
    with closing(sqlite3.connect(ledger_path(bridge[0]))) as db:
        assert db.execute("SELECT send_state FROM submissions WHERE run_id='lost-ack'").fetchone() == ("sent_unknown",)
    # Reloading the module simulates loss of memory between isolated invocations.
    import importlib

    importlib.reload(bridge_client)
    assert call("peer_chat_submit", **args) == unknown
    assert call("peer_chat_submit", **{**args, "prompt": "new prompt"})["error"] == "run_prompt_conflict"
    pending = call("peer_chat", **args)
    assert pending["state"] == "unknown" and "不要换新编号" in pending["message"]
    assert pending["next_action"] == {"command": "peer_chat_query", "params": {
        "target_id": target, "run_id": unknown["run_id"], "after_event": 0,
    }}
    assert call("peer_chat_query", target_id=target, run_id=unknown["run_id"]) == unknown
    assert call("peer_chat_query", target_id=target, run_id=unknown["run_id"])["state"] == "completed"
    assert call("peer_chat_submit", **args) == unknown
    assert [request.method for request in seen] == ["POST", "GET", "GET"]


@pytest.mark.parametrize("same_instance", [True, False], ids=["alias", "different-instance"])
def test_session_owner_is_shared_only_by_the_same_instance_and_peer(bridge, monkeypatch, same_instance):
    peer_id = str(uuid4())
    first = install(url="http://REMOTE.example.test", peer_id=peer_id, allowed_users=["alice"])
    other_url = "http://remote.example.test" if same_instance else "http://other.example.test"
    second = install(url=other_url, peer_id=peer_id, allowed_users=["bob"])
    assert first != second
    instances = {host: str(uuid4()) for host in ("remote.example.test", "other.example.test")}
    posts = []

    def version(request):
        assert request.headers["X-Kemo-Peer-Id"] == peer_id
        assert request.headers["Authorization"] == "Bearer remote-issued-key"
        return httpx.Response(200, json={"instance_uuid": instances[request.url.host]})

    def chat(request):
        run_id = json.loads(request.content)["run_id"]
        posts.append(run_id)
        return sse(run_id, 'data: {"type":"accepted"}\n\n')

    mock_http(monkeypatch, chat, version=version)
    args = dict(session_id="shared-session", prompt="hello")
    assert call("peer_chat_submit", user="alice", target_id=first, run_id="first", **args)["state"] == "running"
    other = call("peer_chat_submit", user="bob", target_id=second, run_id="second", **args)
    if same_instance:
        assert other == {"ok": False, "error": "session_scope_conflict"}
        assert posts == ["first"]
    else:
        assert other["state"] == "running"
        assert posts == ["first", "second"]


@pytest.mark.parametrize("failure", ["offline", "missing-uuid"])
def test_first_use_without_verified_remote_identity_never_posts(bridge, monkeypatch, failure):
    target = install()
    posts = []

    def version(request):
        if failure == "offline":
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json={"version": "2.1.0"})

    mock_http(monkeypatch, posts.append, version=version)
    assert call("peer_chat_submit", target_id=target, run_id="unverified", session_id="private", prompt="hello") == {
        "ok": False, "error": "remote_identity_unavailable", "run_id": "unverified",
    }
    assert posts == []


def test_identity_expiry_never_rebinds_existing_session_ownership(bridge, monkeypatch):
    from bridge_client import _IDENTITY_TTL_SECONDS

    target = install(allowed_users=["alice", "bob"])
    original = str(uuid4())
    identities = iter([original, str(uuid4()), original, original])
    probes, posts = [], []

    def version(request):
        value = next(identities)
        probes.append(value)
        return httpx.Response(200, json={"instance_uuid": value})

    def chat(request):
        run_id = json.loads(request.content)["run_id"]
        posts.append(run_id)
        return sse(run_id, 'data: {"type":"accepted"}\n\n')

    mock_http(monkeypatch, chat, version=version)
    args = dict(target_id=target, session_id="kept", prompt="hello")
    assert call("peer_chat_submit", user="alice", run_id="owner", **args)["state"] == "running"
    with closing(sqlite3.connect(ledger_path(bridge[0]))) as db, db:
        db.execute("UPDATE remote_targets SET checked_at=?", (int(time.time()) - _IDENTITY_TTL_SECONDS - 1,))
    assert call("peer_chat_submit", user="bob", run_id="other", **args)["error"] == "remote_identity_changed"
    with closing(sqlite3.connect(ledger_path(bridge[0]))) as db:
        assert db.execute("SELECT instance_uuid FROM remote_targets").fetchone() == (original,)
    assert call("peer_chat_submit", user="bob", run_id="other", **args)["error"] == "session_scope_conflict"
    assert call("peer_chat_submit", user="alice", run_id="owner-next", **args)["state"] == "running"
    assert len(probes) == 3 and posts == ["owner", "owner-next"]


def test_legacy_unknown_and_archived_owner_survive_ledger_migration(bridge, monkeypatch):
    import hashlib

    target = install(allowed_users=["alice", "bob"])
    alias = install(url="http://alternate.example.test", allowed_users=["bob"])
    path = ledger_path(bridge[0])
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""CREATE TABLE submissions (run_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                           target_id TEXT NOT NULL, session_id TEXT NOT NULL, prompt_sha256 TEXT NOT NULL,
                           receipt TEXT NOT NULL);
                           CREATE TABLE archived_session_owners (target_id TEXT NOT NULL, session_id TEXT NOT NULL,
                           username TEXT NOT NULL, PRIMARY KEY(target_id, session_id));""")
        db.execute("INSERT INTO submissions VALUES (?,?,?,?,?,?)", (
            "legacy-run", "alice", target, "legacy", hashlib.sha256(b"hello").hexdigest(),
            json.dumps({"ok": True, "state": "unknown", "after_event": 0, "text": "", "frames": 0}),
        ))
        db.execute("INSERT INTO archived_session_owners VALUES (?,?,?)", (target, "archived", "alice"))
    chat_requests = []
    mock_http(monkeypatch, chat_requests.append)

    assert call("peer_chat_submit", user="alice", target_id=target, run_id="legacy-run",
                session_id="legacy", prompt="hello")["state"] == "unknown"
    assert call("peer_chat_submit", user="bob", target_id=alias, run_id="new",
                session_id="archived", prompt="hello")["error"] == "session_scope_conflict"
    assert chat_requests == []
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT send_state FROM submissions").fetchone() == ("sent_unknown",)


def test_expiring_before_post_is_retryable_and_fences_off_the_late_worker(bridge, monkeypatch):
    import bridge_client

    target = install()
    arrived, released, settled = threading.Event(), threading.Event(), threading.Event()
    claim = bridge_client._SendAttempt.claim
    posts = []

    def pause_first(attempt, deadline):
        if arrived.is_set():
            return claim(attempt, deadline)
        arrived.set()
        try:
            assert released.wait(3)
            return claim(attempt, deadline)
        finally:
            settled.set()

    def chat(request):
        assert request.method == "POST"
        posts.append(json.loads(request.content)["run_id"])
        return sse(posts[-1], 'data: {"type":"accepted"}\n\n')

    monkeypatch.setattr(bridge_client._SendAttempt, "claim", pause_first)
    mock_http(monkeypatch, chat)
    args = dict(target_id=target, session_id="reserved-session", run_id="reserved-run", prompt="once only")
    try:
        assert call("peer_chat", **args, max_wait_seconds=0.5) == {
            "ok": False, "error": "budget_exhausted_before_sent", "run_id": "reserved-run",
        }
        assert arrived.is_set() and posts == []
        assert call("peer_chat_query", target_id=target, run_id="reserved-run") == {
            "ok": False, "error": "run_not_submitted", "run_id": "reserved-run", "after_event": 0,
        }
        assert call("peer_chat_submit", **{**args, "prompt": "another prompt"})["error"] == "run_prompt_conflict"
        with closing(sqlite3.connect(ledger_path(bridge[0]))) as db:
            assert db.execute("SELECT send_state FROM submissions").fetchone() == ("reserved",)
    finally:
        released.set()
    assert settled.wait(2) and posts == []
    assert call("peer_chat_submit", **args)["state"] == "running"
    assert posts == ["reserved-run"]


def test_budget_expiring_at_send_claim_commit_never_posts(bridge, monkeypatch):
    import bridge_client

    target = install()
    run_id = "slow-claim-commit"
    ledger = bridge_client._ledger
    clock_jump = 0
    posts = []

    @contextmanager
    def slow_claim_commit(path):
        nonlocal clock_jump
        with ledger(path) as db:
            yield db
            row = db.execute(
                "SELECT send_state FROM submissions WHERE run_id=?", (run_id,),
            ).fetchone() if db.in_transaction and not clock_jump else None
        if row and row["send_state"] == "sent_unknown":
            clock_jump = 10

    def chat(request):
        assert request.method == "POST"
        posts.append(json.loads(request.content)["run_id"])
        return sse(run_id, 'data: {"type":"accepted"}\n\n')

    monkeypatch.setattr(bridge_client, "_ledger", slow_claim_commit)
    monkeypatch.setattr(bridge_client, "time", SimpleNamespace(
        monotonic=lambda: time.monotonic() + clock_jump, time=time.time, sleep=time.sleep,
    ))
    mock_http(monkeypatch, chat)
    args = dict(target_id=target, session_id="slow-commit", run_id=run_id, prompt="once only")
    assert call("peer_chat", **args, max_wait_seconds=2) == {
        "ok": False, "error": "budget_exhausted_before_sent", "run_id": run_id,
    }
    assert clock_jump and posts == []
    with closing(sqlite3.connect(ledger_path(bridge[0]))) as db:
        assert db.execute("SELECT send_state FROM submissions WHERE run_id=?", (run_id,)).fetchone() == ("reserved",)
    assert call("peer_chat_submit", **args)["state"] == "running"
    assert posts == [run_id]


@pytest.mark.parametrize("elapsed", [0, 10], ids=["before-deadline", "deadline-crossed"])
def test_connect_timeout_before_write_keeps_the_same_run_retryable(bridge, monkeypatch, elapsed):
    import bridge_client

    target = install()
    run_id = "connect-timeout"
    failed_worker = None
    posts = []

    def connect():
        nonlocal failed_worker
        if failed_worker is None:
            failed_worker = threading.current_thread()
            raise httpx.ConnectTimeout("connection not established")

    def monotonic():
        # Let the failing worker observe the deadline before the caller's wait expires.
        return time.monotonic() + (elapsed if threading.current_thread() is failed_worker else 0)

    def chat(request):
        assert request.method == "POST"
        posts.append(json.loads(request.content)["run_id"])
        return sse(run_id, 'data: {"type":"accepted"}\n\n')

    monkeypatch.setattr(bridge_client, "time", SimpleNamespace(
        monotonic=monotonic, time=time.time, sleep=time.sleep,
    ))
    mock_http(monkeypatch, chat, before_write=connect)
    args = dict(target_id=target, run_id=run_id, session_id="connect-timeout", prompt="once only")
    assert call("peer_chat", **args, max_wait_seconds=2) == {
        "ok": False, "error": "budget_exhausted_before_sent" if elapsed else "upstream_unreachable",
        "run_id": run_id,
    }
    assert posts == []
    assert call("peer_chat_query", target_id=target, run_id=run_id)["error"] == "run_not_submitted"
    assert call("peer_chat_submit", **args)["state"] == "running"
    assert posts == [run_id]


def test_chat_second_page_error_rewinds_to_the_last_delivered_cursor(bridge, monkeypatch):
    from run.extensions.expand_errors import _operation_failure_reason

    target = install()
    attempts = []

    def chat(request):
        if request.method == "POST":
            return sse("page-error", 'data: {"type":"accepted"}\n\n')
        after = int(request.url.params["after_event"])
        attempts.append(after)
        if after == 0:
            return sse("page-error", 'id: 1\ndata: {"type":"text_delta","content":"first "}\n\n'
                                     'data: {"type":"page","after_event":1}\n\n')
        if attempts == [0, 1]:
            return httpx.Response(503)
        return sse("page-error", 'id: 2\ndata: {"type":"text_delta","content":"second"}\n\n'
                                 'id: 3\ndata: {"type":"done","status":"completed"}\n\n')

    mock_http(monkeypatch, chat)
    error = call("peer_chat", target_id=target, run_id="page-error", session_id="s", prompt="hello")
    assert not error["ok"] and error["error"] == "upstream_http_error" and error["after_event"] == 0
    assert "text" not in error and "frames" not in error
    assert "run_id=page-error、after_event=0" in _operation_failure_reason(error)
    first = call("peer_chat_query", target_id=target, run_id=error["run_id"], after_event=error["after_event"])
    second = call("peer_chat_query", target_id=target, run_id=error["run_id"], after_event=first["after_event"])
    assert first["text"] + second["text"] == "first second"
    assert second["state"] == "completed" and attempts == [0, 1, 0, 1]


@pytest.mark.parametrize("large_event", [False, True], ids=["128-events", "one-oversized-event"])
def test_peer_replay_is_bounded_by_utf8_sse_size_and_preserves_all_text(bridge, monkeypatch, large_event):
    from bridge_config import PEER_PAGE_BYTES

    module, listener = bridge
    monkeypatch.setattr(module.EVENTS, "start", lambda: None)
    peer = module.PEERS.create("agent", "alice")
    target = install(peer_id=peer["peer_id"], secret=peer["secret"])
    sample = '测\n"\\😀'  # JSON escaping and non-ASCII both affect the wire size.
    texts = [sample * 110_000] if large_event else [f"{i}:" + sample * 1500 for i in range(128)]
    page_sizes = []

    async def upstream(*args, **kwargs):
        events = [{"type": "text_delta", "content": text} for text in texts]
        events.append({"type": "done", "status": "completed"})
        return httpx.Response(200, text="".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events))

    def forward(request):
        reply = listener.request(request.method, request.url.raw_path.decode(), headers=request.headers,
                                 content=request.content)
        if request.method == "GET" and request.url.path == "/peer/v1/chat":
            page_sizes.append(len(reply.content))
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    monkeypatch.setattr(module.UPSTREAM, "open_stream", upstream)
    mock_http(monkeypatch, forward, version=forward)
    with listener:
        result = call("peer_chat", target_id=target, session_id="long", run_id="large", prompt="hello")
    assert result["state"] == "completed" and result["text"] == "".join(texts)
    if large_event:
        assert result["frames"] > 2
    else:
        assert result["frames"] == 129
    assert len(page_sizes) > 1 and max(page_sizes) <= PEER_PAGE_BYTES


def test_oversized_legacy_journal_event_is_explicit_and_does_not_advance_cursor(bridge, monkeypatch):
    from agent_routes import _chat_keys
    from bridge_config import PEER_PAGE_BYTES

    module, listener = bridge
    row = module.PEERS.create("agent", "alice")
    headers = peer_headers(row)
    peer = module.PEERS.authenticate(headers["Authorization"], headers["X-Kemo-Peer-Id"], endpoint="/peer/v1/chat")
    session_id, run_id = _chat_keys(peer, "legacy", "large-event")
    module.RUNS.store.create(peer.peer_id, {"run_id": run_id, "session_id": session_id})
    for content in ("before", "x" * PEER_PAGE_BYTES):
        module.RUNS.store.append(peer.peer_id, run_id, json.dumps({"type": "text_delta", "content": content}))
    route_headers = {**headers, "X-Kemo-Run-Id": "large-event"}
    first = listener.get("/peer/v1/chat", params={"session_id": "legacy"}, headers=route_headers)
    assert first.status_code == 200 and '"after_event": 1' in first.text
    assert '"content": "before"' in first.text
    next_page = listener.get("/peer/v1/chat", params={"session_id": "legacy", "after_event": 1}, headers=route_headers)
    assert next_page.status_code == 413 and next_page.json() == {"detail": "peer_event_too_large"}
    assert next_page.headers["X-Kemo-Run-Id"] == "large-event"
    module.RUNS.store.set_status(peer.peer_id, run_id, "completed")

    def forward(request):
        reply = listener.request(request.method, request.url.raw_path.decode(), headers=request.headers,
                                 content=request.content)
        return httpx.Response(reply.status_code, headers=reply.headers, content=reply.content)

    target = install(peer_id=row["peer_id"], secret=row["secret"])
    mock_http(monkeypatch, forward, version=forward)
    assert call("peer_chat_submit", target_id=target, run_id="large-event", session_id="legacy",
                prompt="hello")["state"] == "running"
    assert call("peer_chat_query", target_id=target, run_id="large-event", after_event=1) == {
        "ok": False, "error": "peer_event_too_large", "run_id": "large-event", "after_event": 1,
    }


def test_repeated_chat_polling_audits_auth_in_windows_and_text_once_per_run(bridge, monkeypatch):
    from agent_routes import _chat_keys

    module, listener = bridge
    monkeypatch.setattr(module.EVENTS, "start", lambda: None)
    peer = module.PEERS.create("agent", "alice")
    headers = peer_headers(peer)
    released = threading.Event()

    async def upstream(*args, **kwargs):
        while not released.is_set():
            await asyncio.sleep(0.01)
        return httpx.Response(200, text='data: {"type":"done","status":"completed"}\n\n')

    def counts():
        actions = [audit["action"] for audit in module.PEERS.audit_events(peer_id=peer["peer_id"])]
        return tuple(actions.count(action) for action in ("auth_ok", "message_received", "message_sent"))

    def poll(after=0):
        response = listener.get("/peer/v1/chat", params={"session_id": "polling", "after_event": after},
                                headers={**headers, "X-Kemo-Run-Id": "slow"})
        assert response.status_code == 200
        return response.text

    monkeypatch.setattr(module.UPSTREAM, "open_stream", upstream)
    with listener:
        try:
            receipt = listener.post("/peer/v1/chat", headers={**headers, "X-Kemo-Chat-Ack": "1"},
                                    json={"run_id": "slow", "session_id": "polling", "prompt": "no audit text"})
            assert '"accepted"' in receipt.text
            for _ in range(8):
                assert '"after_event": 0' in poll()
            assert counts() == (1, 1, 0)
            identity = module.PEERS.authenticate(headers["Authorization"], peer["peer_id"], endpoint="/peer/v1/chat")
            _, run_id = _chat_keys(identity, "polling", "slow")
            for i in range(1, 5):
                module.RUNS.store.append(peer["peer_id"], run_id, json.dumps({"type": "text_delta", "content": str(i)}))
                assert f'"content": "{i}"' in poll(i - 1)
            assert counts() == (1, 1, 1)
            with module.PEERS.connection(write=True) as db:
                db.execute("UPDATE peer_audit SET created_at='2020-01-01T00:00:00+00:00' WHERE action='auth_ok'")
            assert '"after_event": 4' in poll(4)
            assert counts() == (2, 1, 1)
        finally:
            released.set()


def test_audit_retention_deletes_expired_rows_in_batches_but_keeps_live_run_audits(bridge, monkeypatch):
    import peer_store

    module, _ = bridge
    peers, store = module.PEERS, module.RUNS.store
    headers = peer_headers(peers.create("agent", "alice"))
    identity = peers.authenticate(headers["Authorization"], headers["X-Kemo-Peer-Id"], endpoint="/peer/v1/chat")
    user = identity.peer_id
    for run_id in ("still-running", "ended"):
        store.create(user, {"run_id": run_id, "session_id": "audit"})
        peers.audit("message_received", identity, run_id=run_id)
        peers.audit("message_sent", identity, run_id=run_id)
    peers.audit("message_received", identity)  # Old audit entries did not have a run ID.
    store.set_status(user, "ended", "completed")
    with peers.connection(write=True) as db:
        db.execute("UPDATE peer_audit SET created_at='2020-01-01T00:00:00+00:00' "
                   "WHERE action IN ('message_received','message_sent')")

    monkeypatch.setattr(peer_store, "_AUDIT_PRUNE_BATCH", 1)
    assert peers.prune_audit(store.path) is True
    assert sum(row["run_id"] == "ended" for row in peers.audit_events(peer_id=user)) == 1
    assert peers.prune_audit(store.path) is True
    assert peers.prune_audit(store.path) is False
    rows = {(row["action"], row["run_id"]) for row in peers.audit_events(peer_id=user)}
    assert rows == {("peer_create", None), ("auth_ok", None), ("message_received", None),
                    ("message_received", "still-running"), ("message_sent", "still-running")}
    assert store.get(user, "still-running")["status"] == "starting"
    peers.audit("message_sent", identity, run_id="ended")
    assert all(row["run_id"] != "ended" for row in peers.audit_events(peer_id=user))
    store.set_status(user, "still-running", "completed")
    monkeypatch.setattr(peer_store, "_AUDIT_PRUNE_BATCH", 256)
    peers.prune_audit(store.path)
    assert [row["action"] for row in peers.audit_events(peer_id=user)] == ["auth_ok", "peer_create"]


def test_peer_chat_wait_hint_300_returns_after_one_short_action(bridge, monkeypatch):
    target = install()
    run_id = "wait-hint"

    def running(request):
        if request.method == "POST":
            return sse(run_id, 'data: {"type":"accepted"}\n\n')
        return sse(run_id, 'data: {"type":"page","after_event":0}\n\n')

    mock_http(monkeypatch, running)
    started = time.monotonic()
    result = call("peer_chat", target_id=target, session_id="wait", run_id=run_id,
                  prompt="still running", max_wait_seconds=300)
    elapsed = time.monotonic() - started
    assert result["state"] == "running"
    assert 7 <= elapsed < 10
    assert result["waited_seconds"] == pytest.approx(elapsed, abs=0.05)
    assert result["budget_remaining_seconds"] == pytest.approx(300 - result["waited_seconds"], abs=0.001)
    assert result["next_action"] == {"command": "peer_chat_query", "params": {
        "target_id": target, "run_id": run_id, "after_event": 0,
    }}


def test_retention_prunes_only_expired_peer_terminals_and_preserves_session_owner(bridge, monkeypatch):
    from bridge_client import _TERMINAL_RETENTION_SECONDS

    module, _ = bridge
    target = install(allowed_users=["alice", "bob"])
    posts = []

    def remote(request):
        if request.method == "POST":
            run_id = json.loads(request.content)["run_id"]
            posts.append(run_id)
            if run_id == "unknown":
                raise httpx.ReadTimeout("lost ACK")
            return sse(run_id, 'data: {"type":"accepted"}\n\n')
        run_id = request.headers["X-Kemo-Run-Id"]
        if run_id == "expired":
            return sse(run_id, 'id: 1\ndata: {"type":"done","status":"completed"}\n\n')
        if run_id == "recent":
            return sse(run_id, 'data: {"type":"page","after_event":0,"terminal":'
                              '{"type":"done","status":"failed","code":"bridge_internal",'
                              '"transient":false,"phase":"stream"}}\n\n')
        if run_id == "revoked":
            return httpx.Response(401)
        if run_id == "truncated":
            return sse(run_id, 'id: 1\ndata: {"type":"done","status":"completed"}')
        return sse(run_id, 'data: {"type":"page","after_event":0}\n\n')

    mock_http(monkeypatch, remote)

    def submit(run_id, *, user="alice", session_id=None):
        return call("peer_chat_submit", user=user, target_id=target, session_id=session_id or run_id,
                    run_id=run_id, prompt="run once")

    def query(run_id):
        return call("peer_chat_query", user="alice", target_id=target, run_id=run_id)

    expired = submit("expired")
    for run_id in ("recent", "truncated", "revoked", "running"):
        assert submit(run_id)["state"] == "running"
    assert submit("unknown")["state"] == "unknown"
    assert query("expired")["state"] == "completed"
    assert query("recent")["state"] == "failed"
    assert query("revoked")["failure"]["code"] == "upstream_auth_rejected"
    assert query("truncated")["failure"]["code"] == "upstream_stream_incomplete"

    path = module.BASE_DIR.parents[2] / "runtime" / "kemo_bridge_outbound.sqlite3"
    old = int(time.time()) - _TERMINAL_RETENTION_SECONDS - 1
    with closing(sqlite3.connect(path)) as db:
        assert set(db.execute("SELECT run_id FROM submission_terminals")) == {("expired",), ("recent",)}
        with db:
            db.execute("UPDATE submission_terminals SET observed_at=? WHERE run_id='expired'", (old,))
        # Pending queries still read while another transaction holds the writer lock.
        with db:
            db.execute("BEGIN IMMEDIATE")
            assert query("running")["state"] == "running"
        assert query("expired")["state"] == "completed"
        assert db.execute("SELECT observed_at FROM submission_terminals WHERE run_id='expired'").fetchone() == (old,)
        assert submit("expired") == expired
        assert posts.count("expired") == 1
        assert submit("new")["state"] == "running"
        assert set(db.execute("SELECT run_id FROM submissions")) == {
            ("recent",), ("truncated",), ("revoked",), ("running",), ("unknown",), ("new",),
        }
        assert set(db.execute("SELECT run_id FROM submission_terminals")) == {("recent",)}

    assert query("expired")["error"] == "run_not_found"
    assert submit("unknown")["state"] == "unknown"
    assert posts.count("unknown") == 1
    assert submit("new-bob", user="bob", session_id="expired")["error"] == "session_scope_conflict"


def test_panel_reads_under_real_host_lock_while_remote_chat_is_still_running(bridge, monkeypatch):
    from run.extensions import expand_runtime
    from web.services.module_panels import module_panel_response

    module, _ = bridge
    monkeypatch.setattr(expand_runtime, "load_config", lambda *args: {"expand": {"global_whitelist": ["kemo_bridge"]}})
    polling = threading.Event()
    remote_done = threading.Event()
    request_times = []
    posts = []

    class Peer(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def reply(self, run_id, data):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("X-Kemo-Run-Id", run_id)
            body = data.encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.reply(posts[-1]["run_id"], 'data: {"type":"accepted"}\n\n')

        def do_GET(self):
            if self.path == "/peer/v1/version":
                body = json.dumps({"instance_uuid": remote_uuid}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            assert self.headers["X-Kemo-Run-Id"] == "slow-run"
            assert parse_qs(urlsplit(self.path).query)["session_id"] == ["original"]
            request_times.append(time.monotonic())
            if not polling.is_set():
                polling.set()
                # No headers, not just a slow SSE body. The action must abandon this read.
                remote_done.wait(20)
                return
            if remote_done.is_set():
                self.reply("slow-run", 'id: 1\ndata: {"type":"text_delta","content":"finished"}\n\n'
                                       'id: 2\ndata: {"type":"done","status":"completed"}\n\n')
            else:
                self.reply("slow-run", 'data: {"type":"page","after_event":0}\n\n')

    remote_uuid = str(uuid4())
    with ThreadingHTTPServer(("127.0.0.1", 0), Peer) as peer:
        server = threading.Thread(target=peer.serve_forever, daemon=True)
        server.start()
        target = install(url=f"http://127.0.0.1:{peer.server_port}", allowed_users=["alice"])

        def invoke(command, **params):
            return expand_runtime.invoke_expand(
                root=module.BASE_DIR.parents[2], user="alice", scope="global", module="kemo_bridge",
                command=command, params={"target_id": target, "run_id": "slow-run", **params}, timeout=22,
            )["result"]

        def read_panel():
            return module_panel_response(module.BASE_DIR, "expand")

        try:
            with ThreadPoolExecutor(max_workers=2) as threads:
                submitted = threads.submit(invoke, "peer_chat", session_id="original", prompt="long task",
                                           max_wait_seconds=300)
                assert polling.wait(8), "real control subprocess did not reach the first query"
                assert not submitted.done()
                panel = threads.submit(read_panel)
                assert panel.result(timeout=15)["panel_error"] == ""
                assert not remote_done.is_set()
                pending = submitted.result(timeout=1)
                assert (pending["state"], pending["run_id"], pending["after_event"]) == ("running", "slow-run", 0)
                assert request_times[1] - request_times[0] < 6
                assert time.monotonic() - request_times[0] < 10
                remote_done.set()
            complete = invoke("peer_chat_query", after_event=pending["after_event"])
            assert (complete["state"], complete["text"], complete["frames"]) == ("completed", "finished", 2)
            assert len(posts) == 1
        finally:
            remote_done.set()
            peer.shutdown()
            server.join(timeout=2)
