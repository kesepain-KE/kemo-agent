from __future__ import annotations
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from run.agents.queue import AgentTask
from run.agents.task_ledger import TaskCoordinator, TaskLedger
from web.app import create_app
from web.auth import WebAuthConfig
from web.service import WebRunService
from web.services.workspace_agents import tasks, task
from web.errors import InvalidRequestError, NotFoundError

@pytest.fixture
def backend(tmp_path):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/global_config.json').write_text('{}', 'utf-8')
    for name in ('alice', 'bob'):
        (tmp_path / 'users' / name).mkdir(parents=True)
        (tmp_path / 'users' / name / 'user_config.json').write_text('{}', 'utf-8')
    return WebRunService(tmp_path)

def register(backend, index=1, source='web', session='scope'):
    coordinator = TaskCoordinator(backend.root, 'alice', source, session, f'run{index}', {'prompt': 'public task'})
    owner = coordinator.ownership('call')
    child = AgentTask(f'agent-task-{index:032x}', 'worker', {'task': 'public description'}, source=source, session_id=session)
    coordinator.ledger.register(child, owner)
    return coordinator, child

def test_empty_reads_do_not_create_database(backend):
    assert tasks(backend, 'alice')['total'] == 0
    assert not (backend.root / 'users/alice/runtime').exists()

def test_scoped_tree_pagination_and_private_fields(backend):
    coordinator, child = register(backend)
    register(backend, 2, session='other')
    register(backend, 3, source='cli')
    result = tasks(backend, 'alice', source='web', session_id='scope')
    assert result['total'] == 1 and result['tasks'][0]['children'][0]['id'] == child.id
    assert tasks(backend, 'alice', page_size=1)['total_pages'] == 3
    assert tasks(backend, 'bob')['tasks'] == []
    for source, session in [('web', 'other'), ('app', 'scope')]:
        with pytest.raises(NotFoundError):
            task(backend, 'alice', child.id, source=source, session_id=session)
    with pytest.raises(NotFoundError):
        task(backend, 'bob', child.id, source='web', session_id='scope')
    with pytest.raises(InvalidRequestError):
        task(backend, 'alice', child.id, source='', session_id='')
    assert 'owner' not in json.dumps(result) and 'process_started' not in json.dumps(result)

def test_artifacts_are_only_existing_user_files(backend):
    coordinator, child = register(backend)
    directory = backend.root / 'users/alice/download'
    directory.mkdir()
    (directory / 'result.md').write_text('public', 'utf-8')
    coordinator.ledger.update(child.id, status='completed', result={'artifacts': [
        {'scope': 'download', 'relative_path': 'result.md'},
        {'scope': 'download', 'relative_path': '../../../config/global_config.json'},
        {'scope': 'tmp', 'relative_path': 'secret'},
        {'scope': 'download', 'relative_path': 'missing.md'},
    ]})
    result = task(backend, 'alice', child.id, source='web', session_id='scope')
    assert len(result['task']['artifacts']) == 1
    assert result['task']['artifacts'][0]['name'] == 'result.md'

def test_http_authentication_no_store_and_no_mutations(backend):
    _, child = register(backend)
    app = create_app(service=backend, auth_config=WebAuthConfig(access_token='qa-token'))
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            base = '/api/users/alice/workspace/agents'
            assert (await client.get(base)).status_code == 401
            assert (await client.post('/api/auth/token', json={'token': 'qa-token'})).status_code == 200
            result = await client.get(base)
            assert result.status_code == 200 and result.json()['total'] == 1
            assert result.headers['cache-control'] == 'private, no-store'
            assert (await client.get(base + '?page_size=1000')).status_code == 400
            assert (await client.get(base + '/' + child.id)).status_code == 400
            assert (await client.get(base + '/' + child.id + '?source=web&session_id=scope')).status_code == 200
            assert (await client.post(base + '/' + child.id)).status_code == 405
    asyncio.run(exercise())


def test_tool_details_are_redacted_persisted_scoped_and_excluded_from_lists(backend):
    coordinator, child = register(backend)
    coordinator.ledger.event(child.id, {
        'status': 'tool_running', 'tool_name': 'shell', 'tool_call_id': 'call-1',
        'arguments': {'command': 'python report.py', 'api_key': 'do-not-persist'},
        'reasoning': 'private-thoughts', 'system_prompt': 'private-system',
    })
    coordinator.ledger.event(child.id, {
        'status': 'tool_finished', 'tool_name': 'shell', 'tool_call_id': 'call-1',
        'arguments': {'command': 'python report.py'},
        'result': {'ok': True, 'result': {'stdout': 'public-output', 'password': 'hidden-password'}},
    })
    detail = task(backend, 'alice', child.id, source='web', session_id='scope')['task']
    assert detail['events'][0]['arguments']['command'] == 'python report.py'
    assert detail['events'][0]['arguments']['api_key'] == '[redacted]'
    assert detail['events'][1]['result']['result']['stdout'] == 'public-output'
    assert detail['events'][0]['tool_call_id'] == detail['events'][1]['tool_call_id']
    assert not any(secret in json.dumps(detail) for secret in (
        'do-not-persist', 'private-thoughts', 'private-system', 'hidden-password',
    ))
    # Redaction happens before persistence, not only at HTTP rendering.
    with coordinator.ledger.db() as db:
        stored = db.execute("SELECT record FROM agent_tasks WHERE id=?", (child.id,)).fetchone()[0]
    assert 'do-not-persist' not in stored and 'hidden-password' not in stored
    listed = tasks(backend, 'alice', source='web', session_id='scope')
    assert 'public-output' not in json.dumps(listed)
    assert 'arguments' not in json.dumps(listed)
    for index in range(200):
        coordinator.ledger.event(child.id, {'status': 'model_request', 'iteration': index})
    assert len(task(backend, 'alice', child.id, source='web', session_id='scope')['task']['events']) == 160


def test_tree_keeps_submission_order_instead_of_completion_order(backend):
    coordinator, first = register(backend)
    second = AgentTask('agent-task-' + 'f' * 32, 'worker', {}, source='web', session_id='scope')
    coordinator.ledger.register(second, coordinator.ownership('call2'))
    for child in (second, first):
        coordinator.ledger.update(child.id, status='completed', execution_live=False,
                                  finished_at='2026-10-25T00:00:00Z')
    listed = tasks(backend, 'alice', source='web', session_id='scope')['tasks'][0]
    assert [child['id'] for child in listed['children']] == [first.id, second.id]
    assert [child['submission_order'] for child in listed['children']] == [1, 2]
    assert 'last_submission_order' not in listed
    assert task(backend, 'alice', second.id, source='web', session_id='scope')['task']['submission_order'] == 2


def test_http_retains_budget_stop_fields_and_hides_pending_guidance(backend):
    coordinator, child = register(backend)
    budget = {"max_tool_calls": 80, "unlimited": False, "tool_calls_used": 80,
              "tool_calls_remaining": 0, "total_tool_calls": 160,
              "timeout": 600, "elapsed_seconds": 240, "last_tool": "shell"}
    coordinator.ledger.update(child.id, status="tool_limit", execution_live=False, run_count=2,
        resumable=True, stop_reason="tool_budget_exhausted", budget=budget,
        result={"status": "partial", "summary": "已有结论"},
        _guidance=["private-not-yet-delivered"], usage={"provider_request_count": 8})
    app = create_app(service=backend, auth_config=WebAuthConfig())
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/api/users/alice/workspace/agents/{child.id}?source=web&session_id=scope")
            assert response.status_code == 200
            record = response.json()["task"]
            assert record["budget"] == budget
            assert record["status"] == "tool_limit" and record["resumable"]
            assert record["stop_reason"] == "tool_budget_exhausted"
            assert record["usage"]["provider_request_count"] == 8
            assert "private-not-yet-delivered" not in response.text
    asyncio.run(exercise())
