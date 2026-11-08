"""R1-16: input errors remain outside queues, slots and completion barriers."""
from __future__ import annotations

from dataclasses import replace
import json
import shutil
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.subagent_dispatch import tool as dispatch
from provider.protocol.models import ToolResultItem
from provider.schema import ChatResponse, ToolCall
from run.agents import AgentRunner, AgentScheduler
from run.agents.contracts import AgentInputError
from run.agents.queue import _agent_task_error_detail
from run.agents.task_ledger import TaskLedger
from run.engine import handle_request
from run.tools import ToolDefinition, ToolRegistry, tool_result_payload
from tests.media_input_support import PROJECT, MediaProvider, make_root


def setup_agents(root):
    config, _ = make_root(root)
    for name in ("general_worker", "time_plan", "task_plan"):
        shutil.copytree(PROJECT / "agents" / name, root / "agents" / name)
    return config


def ctx(root, **extra):
    return {"root": str(root), "user": "alice", "source": "web",
            "session_id": "input-preflight", **extra}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


@pytest.mark.parametrize("data,missing,unknown,type_mismatch", [
    ({}, ["task"], [], None),
    ({"goal": "private-input-sentinel"}, ["task"], ["goal"], None),
    ({"task": "work", "extra": 1}, [], ["extra"], None),
    ({"task": ""}, [], [], None),
    ({"task": "   "}, [], [], None),
    ({"task": 9}, [], [], {"field": "task", "expected": "string"}),
    ({"task": "work", "constraints": 9}, [], [],
     {"field": "constraints", "expected": "string|array"}),
])
def test_invalid_input_never_gets_scheduler_or_task_record(
    tmp_path, monkeypatch, data, missing, unknown, type_mismatch
):
    setup_agents(tmp_path)
    scheduler = Mock()
    monkeypatch.setattr(dispatch, "get_agent_scheduler", scheduler)
    result = dispatch.run("call", "general_worker", input=data, wait=False, context=ctx(tmp_path))
    assert result["ok"] is False and result["status"] == "invalid_input"
    assert result["error"]["code"] == "invalid_agent_input"
    assert result["error"]["missing"] == missing and result["error"]["unknown"] == unknown
    assert result["error"]["allowed"] == ["task", "context", "constraints", "acceptance_criteria"]
    assert result["error"]["required"] == ["task"]
    if type_mismatch is None:
        assert "type_mismatch" not in result["error"]
    else:
        assert result["error"]["type_mismatch"] == [type_mismatch]
    assert len(encoded(result)) <= 400
    assert "private-input-sentinel" not in encoded(result)
    scheduler.assert_not_called()
    assert not TaskLedger(tmp_path, "alice").path.exists()


def test_non_ascii_unknown_keys_report_count_not_names(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    scheduler = Mock()
    monkeypatch.setattr(dispatch, "get_agent_scheduler", scheduler)
    keys = ["目标", "必查内容", "输出要求", "限制", "说明", "验收"]
    result = dispatch.run(
        "call",
        "general_worker",
        input={key: "private-input-sentinel" for key in keys},
        wait=False,
        context=ctx(tmp_path),
    )
    encoded_result = encoded(result)
    assert result["error"]["unknown_unsafe"] == 6
    assert result["error"]["truncated"] is True
    assert result["error"]["note"] == "移除未在 allowed 中声明的字段"
    assert all(key not in encoded_result for key in keys)
    assert "private-input-sentinel" not in encoded_result
    assert len(encoded_result) <= 400
    scheduler.assert_not_called()


def test_field_path_with_user_supplied_key_is_not_echoed(tmp_path):
    from run.agents.contracts import agent_input_hint

    hint = agent_input_hint(
        {
            "type": "object",
            "properties": {"sub": {"type": "string"}},
            "additionalProperties": False,
        },
        {"目标": {"sub": 1}},
        field_path="$.目标.sub",
        expected_type="string",
        reason="type",
    )
    bounded = dispatch.bounded_schema_hint(hint, limit=401)
    encoded_result = encoded(bounded)
    assert "目标" not in encoded_result
    assert "type_mismatch" not in bounded


def test_40_unknown_fields_return_bounded_names_without_values(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    scheduler = Mock()
    monkeypatch.setattr(dispatch, "get_agent_scheduler", scheduler)
    result = dispatch.run("call", "general_worker",
                          input={f"unknown_field_{i}": "private-input-sentinel" for i in range(40)},
                          wait=False, context=ctx(tmp_path))
    assert result["ok"] is False and result["status"] == "invalid_input"
    assert result["error"]["missing"] == ["task"]
    assert result["error"]["truncated"] is True
    assert len(encoded(result)) <= 400
    assert "private-input-sentinel" not in encoded(result)
    scheduler.assert_not_called()


def test_valid_calls_keep_consecutive_submission_order_and_invalid_calls_use_no_slots(tmp_path, monkeypatch):
    config = setup_agents(tmp_path)
    runner = AgentRunner(tmp_path, "alice", config=config)
    from run.agents.contracts import AgentRunResult
    monkeypatch.setattr(AgentRunner, "run", lambda _self, name, *_args, **_kwargs: AgentRunResult(name, {}, "", {}, "offline"))
    scheduler = AgentScheduler(runner)
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *_args, **_kwargs: scheduler)
    try:
        first = dispatch.run("call", "general_worker", input={"task": "one"}, wait=False, context=ctx(tmp_path))
        invalid = dispatch.run("call", "general_worker", input={"goal": "bad"}, wait=False, context=ctx(tmp_path))
        second = dispatch.run("call", "general_worker", input={"task": "two"}, wait=False, context=ctx(tmp_path))
        assert first["status"] == second["status"] == "queued"
        assert invalid["status"] == "invalid_input" and "task_id" not in invalid
        scheduler.wait(first["task_id"], 2, source="web", session_id="input-preflight")
        scheduler.wait(second["task_id"], 2, source="web", session_id="input-preflight")
        assert scheduler.get(first["task_id"], source="web", session_id="input-preflight")["submission_order"] == 1
        assert scheduler.get(second["task_id"], source="web", session_id="input-preflight")["submission_order"] == 2
        assert len(scheduler._tasks) == 2
    finally:
        scheduler.close(wait=True, cancel_pending=True)


def test_invalid_input_in_real_main_run_neither_registers_tasks_nor_enters_barrier(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    scheduler = Mock(side_effect=AssertionError("invalid input must not start a scheduler"))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", scheduler)
    definition = ToolDefinition(
        name="subagent_dispatch", description="production dispatch", input_schema={"type": "object"},
        version="1", enabled=True, directory=tmp_path, entrypoint="tool.py:run",
        source="test", execution_mode="thread", _callable=dispatch.run,
    )
    call = ToolCall(id="callid_bad_input", name="subagent_dispatch",
                    arguments={"action": "call", "agent": "general_worker", "input": {"goal": "bad"}})
    provider = MediaProvider([ChatResponse(text="", tool_calls=[call]), ChatResponse(text="correct the input")])
    result = handle_request({"user": "alice", "source": "web", "session_id": "input-preflight", "prompt": "work"},
                            root=tmp_path, provider_factory=lambda _: provider,
                            tool_registry_factory=lambda *_: ToolRegistry({definition.name: definition}))
    assert result["text"] == "correct the input" and len(provider.requests) == 2
    response = next(item for item in provider.requests[-1].input if isinstance(item, ToolResultItem))
    payload = response.content[0].model_dump()["data"]
    assert payload["result"]["ok"] is False and payload["result"]["result"]["status"] == "invalid_input"
    assert payload["result"]["result"]["error"]["required"] == ["task"]
    scheduler.assert_not_called()
    assert not TaskLedger(tmp_path, "alice").path.exists()


@pytest.mark.parametrize("agent,data,invalid", [
    ("general_worker", {"task": 9}, True),
    ("general_worker", {"task": "valid task"}, False),
    ("time_plan", {"action": "delete", "request": "cancel the task", "extra_field": "allowed"}, False),
])
def test_strict_and_loose_builtin_schemas_keep_their_rules(tmp_path, monkeypatch, agent, data, invalid):
    setup_agents(tmp_path)
    scheduler = SimpleNamespace(submit=Mock(return_value="offline-task"))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *_args, **_kwargs: scheduler)
    result = dispatch.run("call", agent, input=data, wait=False, context=ctx(tmp_path))
    assert result["ok"] is not invalid
    assert result["status"] == ("invalid_input" if invalid else "queued")
    assert scheduler.submit.call_count == (0 if invalid else 1)


@pytest.mark.parametrize("mode", ["missing", "raises"])
def test_unavailable_plugin_schema_fails_open(tmp_path, monkeypatch, mode):
    setup_agents(tmp_path)
    original = dispatch._public(tmp_path, "alice")[0]
    if mode == "missing":
        definition = replace(original, name="plugin_agent", input_schema=None)
    else:
        class Unavailable:
            name = "plugin_agent"
            execution = "sync"
            executor = "builtin:llm"
            @property
            def input_schema(self):
                raise OSError("schema unavailable")
        definition = Unavailable()
    monkeypatch.setattr(dispatch, "_public", lambda *_: [definition])
    scheduler = SimpleNamespace(submit=Mock(return_value="offline-plugin"))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *_args, **_kwargs: scheduler)
    result = dispatch.run("call", "plugin_agent", input={"opaque": "payload"}, wait=False, context=ctx(tmp_path))
    assert result["ok"] is True and result["status"] == "queued"
    scheduler.submit.assert_called_once()


def test_external_binding_schema_is_not_newly_rejected(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    monkeypatch.setattr(dispatch, "resolve_external_agent", lambda *_: SimpleNamespace(timeout=1))
    scheduler = SimpleNamespace(submit_callable=Mock(return_value="offline-external"))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *_args, **_kwargs: scheduler)
    monkeypatch.setattr(dispatch, "_wait_for_task", lambda *_args, **_kwargs: (True, {"ok": True, "answer": "offline"}))
    result = dispatch.run("call", "external:opaque", input={"opaque": "payload"}, context=ctx(tmp_path))
    assert result["ok"] is True
    scheduler.submit_callable.assert_called_once()


def test_runner_validation_retains_field_hint_and_queue_preserves_it(tmp_path, monkeypatch):
    config = setup_agents(tmp_path)
    runner = AgentRunner(tmp_path, "alice", config=config)
    with pytest.raises(AgentInputError) as caught:
        runner.run("general_worker", {"goal": "private-input-sentinel"})
    detail = _agent_task_error_detail(caught.value)
    assert detail["schema_hint"]["required"] == ["task"]
    assert detail["schema_hint"]["allowed"] == ["task", "context", "constraints", "acceptance_criteria"]
    assert detail["schema_hint"]["unknown"] == ["goal"]
    assert len(detail["message"]) + len(encoded(detail["schema_hint"])) <= 600
    assert "private-input-sentinel" not in encoded(detail)
    payload = tool_result_payload("subagent_dispatch", {"ok": False, "error": detail}, root=tmp_path, user="alice")
    assert payload["result"]["error"]["schema_hint"] == detail["schema_hint"]


def test_runner_business_rejection_after_valid_schema_keeps_safe_hint(tmp_path, monkeypatch):
    config = setup_agents(tmp_path)
    runner = AgentRunner(tmp_path, "alice", config=config)
    error = AgentInputError("private-message-" * 100, schema_hint={
        "allowed": ["task"], "required": ["task"], "note": "safe",
        "default": "secret-default", "enum": ["secret-enum"],
    })
    monkeypatch.setattr("run.agents.runner.build_agent_prompt_bundle", Mock(side_effect=error))
    with pytest.raises(AgentInputError) as caught:
        runner.run("general_worker", {"task": "valid"})
    detail = _agent_task_error_detail(caught.value)
    assert detail["schema_hint"]["required"] == ["task"]
    assert len(detail["message"]) + len(encoded(detail["schema_hint"])) <= 600
    assert "secret-default" not in encoded(detail) and "secret-enum" not in encoded(detail)


def test_long_schema_hint_preserves_bounded_plain_identifiers_through_delivery(tmp_path):
    from run.agents import bounded_schema_hint, delivery
    fields = [f"field_{i:02d}" for i in range(40)]
    error = AgentInputError("invalid", schema_hint={
        "allowed": fields, "required": fields, "missing": fields,
        "unknown": fields, "description": "secret-description",
        "pattern": "secret-pattern", "default": "secret-default",
    })
    detail = _agent_task_error_detail(error)
    assert len(detail["message"]) + len(encoded(detail["schema_hint"])) <= 600
    assert detail["schema_hint"]["truncated"] is True
    result = delivery({"id": "offline", "status": "failed", "error": detail})
    assert result["error"]["schema_hint"]["required"]
    assert "secret-" not in encoded(result)
    assert bounded_schema_hint({"allowed": True})["truncated"] is True


@pytest.mark.parametrize('action', [None, 'unknown'])
def test_task_plan_invalid_action_never_registers_a_child(tmp_path, monkeypatch, action):
    setup_agents(tmp_path)
    scheduler = Mock(side_effect=AssertionError('invalid input must not start a scheduler'))
    owner = Mock()
    monkeypatch.setattr(dispatch, 'get_agent_scheduler', scheduler)
    data = {'goal': 'create a small demo plan'}
    if action is not None:
        data['action'] = action
    result = dispatch.run('call', 'task_plan', input=data,
                          context=ctx(tmp_path, task_ownership_factory=owner))
    assert result['ok'] is False and result['status'] == 'invalid_input'
    assert result['error']['required'] == ['action']
    if action is None:
        assert result['error']['missing'] == ['action']
    assert 'task_id' not in result
    scheduler.assert_not_called()
    owner.assert_not_called()
    assert not TaskLedger(tmp_path, 'alice').path.exists()


def test_task_plan_schema_is_discoverable_in_dispatch_list(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    plan = next(item for item in dispatch._public(tmp_path, 'alice') if item.name == 'task_plan')
    monkeypatch.setattr(dispatch, '_public', lambda *_: [plan])
    result = dispatch.run('list', context=ctx(tmp_path))
    plan = next(item for item in result['agents'] if item['name'] == 'task_plan')
    schema = plan['input_schema']
    assert schema['required'] == ['action']
    assert schema['properties']['action']['enum'] == ['create', 'edit']
    assert plan['default_wait'] is True


def test_task_plan_schema_is_checked_after_existing_host_preparation(tmp_path, monkeypatch):
    setup_agents(tmp_path)
    from run.agents import AgentRunResult, PreparedSubagentInvocation
    definition = next(item for item in dispatch._public(tmp_path, "alice") if item.name == "task_plan")
    assert definition.input_schema.get("additionalProperties") is True
    # Preserve the synchronous branch; isolate plan-generation business setup.
    monkeypatch.setattr(dispatch, "prepare_main_agent_invocation",
                        lambda **kwargs: PreparedSubagentInvocation(kwargs["input_data"], synchronous_only=True))
    scheduler = SimpleNamespace(submit=Mock(return_value="offline-plan"))
    monkeypatch.setattr(dispatch, "get_agent_scheduler", lambda *_args, **_kwargs: scheduler)
    monkeypatch.setattr(dispatch, "_wait_for_task",
                        lambda *_args, **_kwargs: (True, AgentRunResult("task_plan", {}, "", {}, "offline")))
    payload = {"action": "create", "custom_field": "accepted"}
    result = dispatch.run("call", "task_plan", input=payload, wait=True, context=ctx(tmp_path))
    assert result["ok"] is True and result["status"] == "completed"
    assert scheduler.submit.call_args.args[1] == payload


def test_task_plan_corrected_input_persists_once_in_the_same_main_run(tmp_path, monkeypatch):
    from run.tasks import PlanStore

    config = setup_agents(tmp_path)
    output = {'action': 'create', 'title': 'Demo plan', 'description': 'Try the planner',
              'steps': [{'step_id': 'step_1', 'title': 'First step', 'description': 'Review the demo',
                         'tool_name': None, 'tool_arguments': {}, 'depends_on': [], 'critical': True}]}
    child_provider = MediaProvider([ChatResponse(text=json.dumps(output))])
    runner = AgentRunner(tmp_path, 'alice', config=config, provider_factory=lambda _: child_provider)
    scheduler = AgentScheduler(runner)
    monkeypatch.setattr(dispatch, 'get_agent_scheduler', lambda *_args, **_kwargs: scheduler)
    payload = {'goal': 'Try the planner', 'context': 'A small example for the user'}
    main_provider = MediaProvider([
        ChatResponse(text='', tool_calls=[ToolCall(id=f'callid_plan_{index}', name='subagent_dispatch',
            arguments={'action': 'call', 'agent': 'task_plan', 'input': data})])
        for index, data in enumerate([payload, {**payload, 'action': 'create'}])
    ])
    definition = ToolDefinition(
        name='subagent_dispatch', description='production dispatch', input_schema={'type': 'object'},
        version='1', enabled=True, directory=tmp_path, entrypoint='tool.py:run',
        source='test', execution_mode='thread', _callable=dispatch.run,
    )
    try:
        result = handle_request(
            {'user': 'alice', 'source': 'web', 'session_id': 'input-preflight', 'prompt': 'Create a demo plan'},
            root=tmp_path, provider_factory=lambda _: main_provider,
            tool_registry_factory=lambda *_: ToolRegistry({definition.name: definition}),
        )
        assert result['status'] == 'limited'
        assert result['requested_stop_reason'] == 'task_plan_approval_required'
        assert len(main_provider.requests) == 2 and len(child_provider.requests) == 1
        assert len(scheduler._tasks) == 1
        task = next(iter(scheduler._tasks.values()))
        record = TaskLedger(tmp_path, 'alice').get(task.id, 'web', 'input-preflight')
        assert record['status'] == 'completed'
        plans = PlanStore(tmp_path, 'alice').list_plans(source='web', session_id='input-preflight')
        assert len(plans) == 1 and plans[0]['title'] == 'Demo plan'
        assert plans[0]['status'] == 'pending' and plans[0]['auto_accept'] is False
    finally:
        scheduler.close(wait=True, cancel_pending=True)
