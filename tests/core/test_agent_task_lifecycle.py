from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

from provider.schema import ChatResponse, ToolCall
from provider.protocol.models import ToolCallItem, ToolResultItem
from run.agents import AgentScheduler, AgentTimeoutError
from run.agents.task_ledger import TaskCoordinator, TaskLedger, safe
from run.engine import iter_request_events
from tests.media_input_support import make_root, MediaProvider

SCHEDULERS = {}
JOBS = {}


def eventually(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.02)
    assert predicate()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    make_root(tmp_path)
    directory = tmp_path / 'plugins/subagent_dispatch'
    directory.mkdir()
    manifest = {'name': 'subagent_dispatch', 'description': 'test scoped dispatch',
                'input_schema': {'type': 'object', 'properties': {
                    'action': {'type': 'string'}, 'agent': {'type': 'string'}, 'wait': {'type': 'boolean'}},
                    'required': ['action', 'agent'], 'additionalProperties': False},
                'entrypoint': 'tool.py:run', 'version': '1.0.0', 'enabled': True, 'execution_mode': 'thread'}
    (directory / 'SKILL.md').write_text('# subagent_dispatch\nTest\n\n## Tool\n```json\n' + json.dumps(manifest) + '\n```', 'utf-8')
    (directory / 'tool.py').write_text('''from tests.core.test_agent_task_lifecycle import SCHEDULERS, JOBS

def run(action, agent, wait=False, *, context):
    scheduler = SCHEDULERS[context['root']]
    task_id = scheduler.submit_callable(agent, {}, JOBS[agent], source=context['source'],
        session_id=context['session_id'], ownership=context['task_ownership'])
    if wait:
        value = scheduler.wait(task_id, timeout=3, source=context['source'], session_id=context['session_id'])
        return {'status': 'completed', 'task_id': task_id, 'data': value}
    return {'status': 'queued', 'task_id': task_id}
''', 'utf-8')
    scheduler = AgentScheduler(SimpleNamespace(root=tmp_path, user='alice', config={}))
    SCHEDULERS[str(tmp_path)] = scheduler
    monkeypatch.setattr('run.agents.get_agent_scheduler', lambda *a, **k: scheduler)
    yield tmp_path, scheduler
    scheduler.close(wait=True, cancel_pending=True)
    SCHEDULERS.pop(str(tmp_path), None)
    JOBS.clear()


def launch(root, *, agents=('worker',), wait=False, provider=None, mailbox=None):
    provider = provider or MediaProvider([
        ChatResponse(text='', tool_calls=[ToolCall(id=f'call_{i}', name='subagent_dispatch',
            arguments={'action': 'call', 'agent': agent, 'wait': wait}) for i, agent in enumerate(agents)]),
        ChatResponse(text='draft response'), ChatResponse(text='merged final'),
    ])
    cancel = threading.Event()
    events, errors = [], []
    def run():
        try:
            events.extend(iter_request_events({'user': 'alice', 'source': 'web', 'session_id': 'scope',
                          'run_id': 'run_test', 'prompt': 'perform task', '_guidance_queue': mailbox}, root=root,
                          provider_factory=lambda _: provider, cancel_event=cancel))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, cancel, events, errors, provider


def root_record(root):
    records, _ = TaskLedger(root, 'alice').records('web', 'scope', parent='')
    return records[0] if records else {}


def test_background_final_is_blocked_then_result_resumes_with_paired_tools(setup):
    root, _ = setup
    release = threading.Event()
    JOBS['worker'] = lambda cancel: release.wait(30) and {'answer': 'child-result'}
    thread, _, events, errors, provider = launch(root)
    try:
        eventually(lambda: (root_record(root) or {}).get('status') == 'waiting_children')
        assert thread.is_alive() and len(provider.requests) == 2
        assert not any(e.type == 'done' for e in events)
    finally:
        release.set()
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert len(provider.requests) == 3
    serialized = provider.requests[-1].model_dump_json()
    assert 'child-result' in serialized
    calls = [i.call_id for i in provider.requests[-1].input if isinstance(i, ToolCallItem)]
    results = [i.call_id for i in provider.requests[-1].input if isinstance(i, ToolResultItem)]
    assert calls == results
    assert root_record(root)['status'] == 'completed'
    children, _ = TaskLedger(root, 'alice').records(parent=root_record(root)['id'])
    assert children[0]['handled'] is True


def test_multiple_success_and_failure_results_are_delivered_once(setup):
    root, _ = setup
    release = threading.Event()
    JOBS['one'] = lambda cancel: release.wait(30) and {'answer': 'one-output'}
    def fail(cancel):
        raise ValueError('child failed')
    JOBS['two'] = fail
    thread, _, _, errors, provider = launch(root, agents=('one', 'two'))
    try:
        eventually(lambda: (root_record(root) or {}).get('status') == 'waiting_children')
    finally:
        release.set()
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert 3 <= len(provider.requests) <= 4  # Each result may wake the sole parent independently.
    assert 'one-output' in provider.requests[-1].model_dump_json()
    assert 'child failed' in provider.requests[-1].model_dump_json()
    ledger = TaskLedger(root, 'alice')
    children, _ = ledger.records(parent=root_record(root)['id'])
    assert len(children) == 2 and all(c['handled'] for c in children)


def test_sync_result_does_not_create_a_duplicate_notification(setup):
    root, _ = setup
    JOBS['worker'] = lambda cancel: {'answer': 'sync-output'}
    thread, _, _, errors, provider = launch(root, wait=True)
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert len(provider.requests) == 2
    assert root_record(root)['status'] == 'completed'


def test_cancel_waits_for_real_exit_and_never_resumes_provider(setup):
    root, _ = setup
    release = threading.Event()
    JOBS['worker'] = lambda cancel: release.wait(30) and {'answer': 'late'}
    thread, cancel, _, errors, provider = launch(root)
    eventually(lambda: (root_record(root) or {}).get('status') == 'waiting_children')
    cancel.set()
    try:
        eventually(lambda: (root_record(root) or {}).get('status') == 'cancelling')
        eventually(lambda: TaskLedger(root, 'alice').records(
            'web', 'scope', parent=root_record(root)['id'])[0][0]['status'] == 'cancelling')
        time.sleep(.1)
        assert thread.is_alive() and len(provider.requests) == 2
    finally:
        release.set()
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert root_record(root)['status'] == 'cancelled'
    assert len(provider.requests) == 2


def test_timed_out_running_is_not_an_execution_terminal(setup):
    root, _ = setup
    future = Future()
    def detached(cancel):
        raise AgentTimeoutError('still running', process_terminated=False, completion_future=future)
    JOBS['worker'] = detached
    thread, _, _, errors, provider = launch(root)
    eventually(lambda: (root_record(root) or {}).get('status') == 'waiting_children')
    time.sleep(.1)
    assert thread.is_alive() and len(provider.requests) == 2
    future.set_result({'answer': 'late-output'})
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert 'late-output' in provider.requests[-1].model_dump_json()
    assert root_record(root)['status'] == 'completed'


def test_guidance_wakes_the_same_parent_before_child_exits(setup):
    root, _ = setup
    from run.conversation import GuidanceMailbox
    mailbox = GuidanceMailbox()
    release = threading.Event()
    JOBS['worker'] = lambda cancel: release.wait(30) and {'answer': 'child-output'}
    thread, _, _, errors, provider = launch(root, mailbox=mailbox)
    try:
        eventually(lambda: (root_record(root) or {}).get('status') == 'waiting_children')
        assert mailbox.offer('change the delivery format')[0]
        eventually(lambda: len(provider.requests) >= 3)
        assert 'change the delivery format' in provider.requests[2].model_dump_json()
        assert thread.is_alive()
    finally:
        release.set()
    thread.join(8)
    assert not thread.is_alive() and not errors
    assert len(provider.requests) == 4


def test_fast_scheduler_cannot_overwrite_completed_with_a_slow_queued_snapshot(setup):
    root, scheduler = setup
    coordinator = TaskCoordinator(root, 'alice', 'web', 'fast', 'run', {'prompt': 'fast'})
    for index in range(24):
        task_id = scheduler.submit_callable('worker', {}, lambda cancel: {'answer': 'ok'},
            source='web', session_id='fast', ownership=coordinator.ownership(str(index)))
        scheduler.wait(task_id, timeout=2, source='web', session_id='fast')
        record = coordinator.ledger.get(task_id, 'web', 'fast')
        assert record['status'] == 'completed' and not record['execution_live']
        coordinator.acknowledge_sync(task_id)
    coordinator.acknowledge()
    coordinator.finish('completed')


def test_sync_delivery_waits_for_provider_acceptance_and_retries_without_reexecution(setup):
    root, scheduler = setup
    coordinator = TaskCoordinator(root, 'alice', 'web', 'sync', 'run', {'prompt': 'sync'})
    executions = []
    task_id = scheduler.submit_callable('worker', {}, lambda cancel: executions.append(1) or {'answer': 'ok'},
        source='web', session_id='sync', ownership=coordinator.ownership('sync-call'))
    scheduler.wait(task_id, timeout=2, source='web', session_id='sync')
    coordinator.acknowledge_sync(task_id)
    assert coordinator.ledger.get(task_id, 'web', 'sync')['handled'] is False
    assert coordinator.notifications() is None  # Already present in the paired tool message.
    with pytest.raises(RuntimeError):
        coordinator.finish('completed')
    retry = TaskCoordinator(root, 'alice', 'web', 'sync', 'retry', {'_agent_root_task_id': coordinator.id})
    assert retry.notifications() is not None
    retry.acknowledge()
    retry.finish('completed')
    assert executions == [1]


def test_task_polling_does_not_rewrite_unchanged_records(tmp_path, monkeypatch):
    (tmp_path / 'users/alice').mkdir(parents=True)
    coordinator = TaskCoordinator(tmp_path, 'alice', 'web', 'poll', 'run', {'prompt': 'poll'})
    coordinator.ownership('call')
    writes = []
    original = TaskLedger.put
    def observe(db, record):
        writes.append(record['id'])
        original(db, record)
    monkeypatch.setattr(TaskLedger, 'put', staticmethod(observe))
    for _ in range(3):
        assert coordinator.ledger.get(coordinator.id, 'web', 'poll')
        assert coordinator.ledger.records('web', 'poll')[1] == 1
    assert writes == []


def test_scope_sealing_recovery_and_redaction(tmp_path):
    (tmp_path / 'users/alice').mkdir(parents=True)
    (tmp_path / 'users/bob').mkdir(parents=True)
    coordinator = TaskCoordinator(tmp_path, 'alice', 'web', 'scope', 'run', {'prompt': 'safe'})
    owner = coordinator.ownership('call')
    from run.agents.queue import AgentTask
    child = AgentTask('agent-task-' + 'a' * 32, 'worker', {}, source='web', session_id='scope')
    ledger = coordinator.ledger
    ledger.register(child, owner)
    assert ledger.get(child.id, 'web', 'other') is None
    assert TaskLedger(tmp_path, 'bob').path != ledger.path
    with pytest.raises(RuntimeError):
        coordinator.finish('completed')
    ledger.update(child.id, status='completed', execution_live=False, result={'answer': 1})
    notification = coordinator.notifications()
    assert notification and not coordinator.notifications()
    # New executor attempt redelivers only results whose provider response was not accepted.
    restarted = TaskCoordinator(tmp_path, 'alice', 'web', 'scope', 'run', {'_agent_root_task_id': coordinator.id})
    restarted.ownership('call2')
    assert restarted.notifications()
    restarted.acknowledge()
    restarted.finish('completed')
    with pytest.raises(ValueError):
        ledger.register(AgentTask('late', 'worker', {}, source='web', session_id='scope'), owner)
    ledger.update(child.id, status='running', owner='old-owner', pid=0, process_started=0, execution_live=True)
    assert ledger.get(child.id, 'web', 'scope')['status'] == 'running'
    assert ledger.get(child.id, 'web', 'scope')['execution_live'] is True
    assert ledger.get(child.id, 'web', 'scope')['reconcile_unverified'] is True
    public = safe({'reasoning': 'private', 'password': 'password-value', 'metadata': {'raw': 'unsafe'},
                   'answer': 'Bearer abcdefghijklmnopqrstuvwxyz12345', 'huge': 'x' * 50000})
    assert 'private' not in str(public) and 'password-value' not in str(public) and 'unsafe' not in str(public)
    assert len(json.dumps(public)) < 17000
