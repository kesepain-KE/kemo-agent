"""Actual HTTP serialization must reject malformed critical JSON responses."""
import asyncio
from unittest.mock import Mock

from fastapi.routing import APIRoute
import httpx
import pytest

from web.app import create_app
from web.auth import WebAuthenticator
from tests.web.test_audit_api_contracts import backend as backend


@pytest.mark.parametrize("method,path,target,value", [
    ("POST", "/api/users/alice/tasks/plans", "create_plan", {
        "user": "alice", "updated": True, "plan": {"plan_id": {"secret": "private-response"}},
    }),
    ("DELETE", "/api/users/alice/sessions", "delete_all_sessions", {
        "user": "alice", "source": "all", "deleted": True,
        "deleted_sessions": -1, "deleted_windows": 0,
    }),
    ("DELETE", "/api/users/alice/sessions/s", "delete_session", {
        "user": "alice", "source": "web", "session_id": "s", "deleted": "yes",
    }),
])
def test_operation_response_contracts_run_at_http_boundary(backend, monkeypatch, method, path, target, value):
    monkeypatch.setattr(backend, target, Mock(return_value=value))
    async def request():
        transport = httpx.ASGITransport(app=create_app(service=backend), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            result = await client.request(method, path, json={} if method == "POST" else None)
            assert result.status_code == 500, result.text
            assert result.json()["error"]["code"] == "internal_error"
            assert "private-response" not in result.text
    asyncio.run(request())


@pytest.mark.parametrize("endpoint,target,payload", [
    ("agents", "tasks", {"tasks": [{"id": 123}], "total": 1, "page": 1, "total_pages": 1}),
    ("jobs", "list_jobs", {"user": "alice", "jobs": [], "active_count": "zero"}),
])
def test_workspace_no_longer_bypasses_response_validation(backend, monkeypatch, endpoint, target, payload):
    monkeypatch.setattr(f"web.routes.workspace.{target}", Mock(return_value=payload))
    async def request():
        transport = httpx.ASGITransport(app=create_app(service=backend), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/users/alice/workspace/" + endpoint)
            assert response.status_code == 500
    asyncio.run(request())


def test_auth_status_is_public_but_its_schema_is_not_optional(backend, monkeypatch):
    monkeypatch.setattr(WebAuthenticator, "status", Mock(return_value={
        "enabled": False, "authenticated": "sure",
    }))
    async def request():
        transport = httpx.ASGITransport(app=create_app(service=backend), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/auth/status")).status_code == 500
    asyncio.run(request())


def test_critical_route_registration_requires_field_level_models(backend):
    routes = [r for r in create_app(service=backend).routes if isinstance(r, APIRoute)]
    expected = {
        ("GET", "/api/auth/status"), ("POST", "/api/auth/token"),
        ("POST", "/api/auth/login"), ("POST", "/api/auth/logout"),
        ("DELETE", "/api/users/{user}/sessions"),
        ("DELETE", "/api/users/{user}/sessions/{session_id}"),
        ("GET", "/api/users/{user}/workspace/agents"),
        ("GET", "/api/users/{user}/workspace/agents/{task_id}"),
        ("GET", "/api/users/{user}/workspace/jobs"),
        ("GET", "/api/users/{user}/workspace/jobs/{job_id}"),
    }
    expected |= {
        (method, route.path)
        for route in routes if "/tasks/" in route.path
        for method in route.methods or set() if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    found = set()
    for route in routes:
        for method in route.methods or set():
            if (method, route.path) in expected:
                assert getattr(route.response_model, "model_fields", None), route.path
                found.add((method, route.path))
    assert found == expected
