"""Exercise validation at the HTTP boundary, before service-side effects."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import Mock

import httpx
import pytest

from web.api_contracts import RuntimeResponse
from web.app import create_app
from web.service import WebRunService
from web.auth import WebAuthConfig, local_unauthenticated_request


@pytest.fixture
def backend(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/global_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/alice/user_config.json").write_text("{}", encoding="utf-8")
    return WebRunService(tmp_path)


@pytest.mark.parametrize("method,path,body,service_method", [
    ("POST", "/tasks/plans", {"title": ["not a string"]}, "create_plan"),
    ("PATCH", "/tasks/plans/p/edit", {"revision": "2"}, "edit_plan"),
    ("POST", "/tasks/plans/p/steps/s/retry", {"revision": 0}, "retry_plan_step"),
    ("POST", "/tasks/plans/p/rollback", {"revision": 1}, "rollback_plan"),
    ("POST", "/tasks/cleanup", {"category": "unknown", "items": []}, "cleanup_completed_tasks"),
    ("POST", "/tasks/crons", {"type": "unknown"}, "create_cron"),
])
def test_invalid_bodies_are_rejected_before_side_effects(backend, monkeypatch, method, path, body, service_method):
    target = Mock(side_effect=AssertionError("invalid request reached service"))
    monkeypatch.setattr(backend, service_method, target)

    async def request():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(service=backend)), base_url="http://test") as client:
            response = await client.request(method, "/api/users/alice" + path, json=body)
        assert response.status_code == 400, response.text
        assert response.json()["error"]["code"] == "invalid_request"
    asyncio.run(request())
    target.assert_not_called()


@pytest.mark.parametrize("sections", ["prompt", "tokens", "api", "external", "maintenance", "congestion", "summary", ""])
def test_every_lazy_runtime_section_preserves_response_contract(backend, sections):
    result = RuntimeResponse.model_validate(backend.runtime_status("alice", sections=sections))
    assert result.tokens.timezone == "Asia/Shanghai"
    assert not any(key.startswith("_status_") for key in result.tokens.model_dump())


def test_congestion_http_snapshots_follow_shared_slots_without_building_prompts(backend, monkeypatch):
    import provider.factory as factory
    import web.services.runtime_status as status_module

    config = {"provider_runtime": {"max_concurrent_requests": 2}}
    (backend.root / "config/global_config.json").write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(factory, "_provider_semaphore", None)
    monkeypatch.setattr(factory, "_provider_semaphore_limit", 0)
    prompt = Mock(side_effect=AssertionError("congestion-only polling must not build prompts"))
    monkeypatch.setattr(status_module, "build_prompt_bundle", prompt)
    app = create_app(service=backend)

    async def snapshot():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/users/alice/runtime/status?session_id=pool-space&sections=congestion")
        assert response.status_code == 200, response.text
        assert response.json()["included_sections"] == ["congestion"]
        return response.json()["congestion"]["provider"]

    def check(active):
        assert asyncio.run(snapshot()) == {
            "active_requests": active, "max_requests": 2,
            "available_requests": 2 - active, "waiting_estimate": 0,
        }

    check(0)
    with factory.provider_request_slot(config):
        check(1)
        with factory.provider_request_slot(config):
            check(2)
        check(1)
    check(0)
    prompt.assert_not_called()


def test_direct_asgi_entry_rejects_remote_unauthenticated_access(backend, monkeypatch):
    target = Mock(side_effect=AssertionError("remote no-auth request reached service"))
    monkeypatch.setattr(backend, "users", target)

    async def request():
        transport = httpx.ASGITransport(app=create_app(service=backend), client=("203.0.113.8", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.get("/api/users")
            health = await client.get("/api/health")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "local_auth_required"
        assert health.status_code == 200
    asyncio.run(request())
    target.assert_not_called()


@pytest.mark.parametrize("headers,trusted,expected", [
    ({}, (), 200),
    ({"x-forwarded-for": "203.0.113.8"}, (), 403),
    ({"x-real-ip": "203.0.113.8"}, (), 403),
    ({"forwarded": "for=203.0.113.8"}, (), 403),
    ({"x-forwarded-proto": "https"}, (), 403),
    ({"x-forwarded-for": "203.0.113.8"}, ("127.0.0.1/32",), 403),
    ({"x-forwarded-for": "127.0.0.2"}, ("127.0.0.1/32",), 200),
    ({"x-real-ip": "::1"}, ("127.0.0.1/32",), 200),
    ({"x-forwarded-for": "127.0.0.1", "x-real-ip": "203.0.113.8"}, ("127.0.0.1/32",), 403),
    ({"x-forwarded-for": "unknown"}, ("127.0.0.1/32",), 403),
    ({"x-forwarded-for": ""}, ("127.0.0.1/32",), 403),
    ({"x-forwarded-for": "127.0.0.1,"}, ("127.0.0.1/32",), 403),
    ({"x-forwarded-for": "10.0.0.5"}, ("127.0.0.0/8", "10.0.0.0/8"), 403),
])
def test_no_auth_proxy_boundary_is_fail_closed(backend, headers, trusted, expected):
    async def request():
        app = create_app(service=backend, auth_config=WebAuthConfig(trusted_proxies=trusted))
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4321))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/users", headers=headers)).status_code == expected
            assert (await client.get("/api/auth/status")).status_code == 200
    asyncio.run(request())


@pytest.mark.parametrize("peer", [None, "testclient", "unknown", "203.0.113.8"])
def test_no_auth_unknown_peer_is_not_assumed_local(peer):
    assert not local_unauthenticated_request(peer, {}, ())


def test_duplicate_forwarded_headers_cannot_hide_the_remote_hop(backend):
    async def request():
        app = create_app(service=backend, auth_config=WebAuthConfig(trusted_proxies=("127.0.0.1",)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/users", headers=[
                ("x-forwarded-for", "127.0.0.1"), ("x-forwarded-for", "203.0.113.8"),
            ])
            assert response.status_code == 403
    asyncio.run(request())
