from __future__ import annotations

from pathlib import Path

import yaml

from conftest import peer_headers


def test_config_get_and_write_never_return_secrets(bridge, tmp_path, monkeypatch):
    module, client = bridge
    peer = module.PEERS.create("device", "alice")
    path = tmp_path / "users/alice/user_config.yaml"
    path.write_text("provider:\n  api_key: ''\n  api_key_env: PRIVATE_ENV\n", encoding="utf-8")
    changes_seen = []

    async def request(method, target, **options):
        if method == "GET":
            assert target == "/api/users/alice/config/full"
            return {"user": "alice", "config": {
                "provider": {"api_key": "***", "api_key_env": "***", "session_secret": "secret"},
                "auth": {"token": "credential-value"}, "key": "key-value", "cookie": "",
                "authorization": None, "agent": {"apiKey": "camel-value", "token_limit": 2000},
            }}
        assert method == "PATCH" and target == "/api/users/alice/config"
        changes_seen.append(options["json_body"]["changes"])
        return {"user": "alice", "updated": True, "config": {
            "provider": {"api_key": "brand-new-key"},
            "web": {"authorization": "credential", "Cookie": "session", "key": "private"},
        }}

    monkeypatch.setattr(module.UPSTREAM, "request_json", request)
    headers = peer_headers(peer)
    result = client.get("/v1/config", headers=headers)
    assert result.status_code == 200
    assert result.json()["config"] == {
        "provider": {"api_key": None, "api_key_env": "***", "session_secret": "***"},
        "auth": "***", "key": "***", "cookie": None, "authorization": None,
        "agent": {"apiKey": "***", "token_limit": 2000},
    }
    result = client.patch("/v1/config", headers=headers, json={"changes": {"provider": {"api_key": "brand-new-key"}}})
    assert result.status_code == 200
    assert result.json()["config"] == {
        "provider": {"api_key": "***", "api_key_env": None},
        "web": {"authorization": "***", "Cookie": "***", "key": "***"},
    }
    assert changes_seen == [{"provider": {"api_key": "brand-new-key"}}]
    for value in ({"api_key": {"append": "secret"}}, {"api_key": "***"}, {"API.AUTH.KEY": "secret"}):
        assert client.patch("/v1/config", headers=headers, json={"provider": value}).status_code == 400
    assert client.patch("/v1/config", headers=headers, json={"skills": {"shared_whitelist": []}}).status_code == 403
    result = client.patch("/v1/config", headers=headers, json=["secret-do-not-echo"])
    assert result.status_code == 422 and "secret-do-not-echo" not in result.text
    assert len(changes_seen) == 1


def test_reasoning_reads_current_model_and_persists_only_declared_effort(bridge, tmp_path, monkeypatch):
    from web.service import WebRunService
    from tests.media_input_support import make_root

    module, client = bridge
    _, _ = make_root(tmp_path, provider_type="kemo")
    peer = module.PEERS.create("device", "alice")
    backend = WebRunService(root=tmp_path)
    checks = []

    async def request(method, target, **options):
        assert target.startswith("/api/users/alice/")
        if target.endswith("/settings"):
            return backend.settings("alice")
        if target.endswith("/model-capabilities"):
            checks.append(options["params"]["model"])
            return {"capabilities": {"reasoning": {"supported": True, "efforts": ["medium", "xxy"]}}}
        assert method == "PATCH" and target.endswith("/config")
        return backend.patch_user_config("alice", options["json_body"]["changes"])

    monkeypatch.setattr(module.UPSTREAM, "request_json", request)
    headers = peer_headers(peer)
    assert client.get("/v1/reasoning", headers=headers).json() == {
        "effort": "medium", "available": ["medium", "xxy"],
    }
    assert client.patch("/v1/reasoning", headers=headers, json={"effort": "max"}).status_code == 400
    result = client.patch("/v1/reasoning", headers=headers, json={"effort": "xxy"})
    assert result.status_code == 200 and result.json() == {"effort": "xxy"}
    assert yaml.safe_load((tmp_path / "users/alice/user_config.yaml").read_text("utf-8"))["provider"]["reasoning_effort"] == "xxy"
    assert checks == ["vision-main"] * 3


def test_skills_projection_is_read_only_and_returns_no_documents(bridge, monkeypatch):
    module, client = bridge
    peer = module.PEERS.create("device", "alice")

    async def request(method, target, **options):
        assert (method, target) == ("GET", "/api/users/alice/skills")
        def skill(name, scope, category, enabled):
            return {"name": name, "scope": scope, "category": category, "active_for_main_agent": enabled,
                    "title": name, "description": "summary", "path": "/private", "markdown": "body"}
        return {"prompt_skills": [
            skill("first", "user", "agent_generated", True),
            skill("second", "user", "user_created", False),
            skill("third", "shared", "shared", True),
        ], "tools": [{"name": "shell"}]}

    monkeypatch.setattr(module.UPSTREAM, "request_json", request)
    headers = peer_headers(peer)
    result = client.get("/v1/skills?scope=user&enabled=true", headers=headers)
    assert result.json() == {"total": 1, "items": [{
        "name": "first", "scope": "agent_create", "title": "first", "description": "summary",
        "enabled": True, "origin": "user",
    }]}
    assert client.get("/v1/skills?scope=shared", headers=headers).json()["items"][0]["name"] == "third"
    assert client.patch("/v1/skills", headers=headers, json={}).status_code == 403


def test_token_usage_reads_real_cardstream_rollups_without_credentials(bridge, tmp_path):
    from run.cards import CardStore, IntegrationStore

    module, client = bridge
    a = module.PEERS.create("device", "alice")
    b = module.PEERS.create("device", "bob")
    for user, count in (("alice", 4), ("bob", 200)):
        integration = IntegrationStore(CardStore(tmp_path, user), "app", "chat-1", f"integration_{user}")
        integration.start()
        integration.update_usage({"input_tokens": count, "output_tokens": 3, "cached_input_tokens": 2,
                                  "provider_request_count": 1, "provider_raw": {
                                      "api_key": "not-for-mobile", "secret_fingerprint": "fp",
                                      "billing_unit_price": 9}})
        with integration.store.db(write=True) as db:
            db.execute("UPDATE card_integrations SET updated_at='2026-11-20T17:30:00+00:00' WHERE integration_id=?",
                       (integration.id,))
    test_day = "since=2026-11-21&until=2026-11-21"
    uri = "/v1/token_usage?" + test_day
    alice_db = tmp_path / "users/alice/history/cards.sqlite3"
    original = alice_db.read_bytes()
    result = client.get(uri, headers=peer_headers(a))
    assert result.status_code == 200, result.text
    total = {"input": 4, "output": 3, "cached": 2, "requests": 1}
    assert result.json() == {
        "total": total, "series": [{"bucket": "2026-11-21", **total}],
        "by_model": [{"model": "unknown", "input": 4, "output": 3}],
        "budget": {"daily_limit": None, "used_ratio": 0.0},
    }
    assert client.get(uri + "&group_by=session", headers=peer_headers(a)).json()["series"][0]["bucket"] == "app:chat-1"
    assert client.get(uri + "&group_by=model", headers=peer_headers(a)).json()["series"][0]["bucket"] == "unknown"
    assert client.get(uri, headers=peer_headers(b)).json()["total"]["input"] == 200
    assert client.get("/v1/token_usage?since=2026-11-22&until=2026-11-21", headers=peer_headers(a)).status_code == 400
    assert alice_db.read_bytes() == original
