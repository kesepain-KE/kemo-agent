from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from events import RunEvent
from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.schema import ChatResponse, Usage
from run.agent_runner import AgentRunResult
from agents.task_plan.executor import execute as execute_task_plan_agent
from run.task_plan_store import (
    PlanConflictError,
    PlanError,
    PlanNotFoundError,
    PlanStore,
    PlanValidationError,
    normalize_plan,
)
from run.task_plan_executor import (
    approve_plan,
    cancel_plan,
    execute_plan,
    get_plan,
    list_plans,
    pause_plan,
    resume_plan,
    PlanExecutionError,
)
from run.task_plan_service import (
    PlanGenerationError,
    PlanSkipped,
    edit_plan,
    generate_plan,
    persist_agent_result,
)
from run.task_plan_scheduler import TaskPlanScheduler
from run.tools import ToolRegistry


CONFIG = {
    "provider": {
        "type": "kemo",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key_env": "TEST_PLAN_KEY",
        "model": "mock",
    },
    "tools": {"enabled": True, "timeout": 5},
    "task_plan": {"auto_accept": False, "max_steps": 10},
}


class MockTool:
    """A simple tool that can be discovered by the test registry."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict] = []

    def execute(self, **kwargs: object) -> str:
        self.calls.append(dict(kwargs))
        if self.fail:
            raise RuntimeError("tool failed")
        return "ok"


class MockProvider:
    """Returns structured plan JSON for task_plan sub-agent."""

    def __init__(self, response_text: str) -> None:
        self.response_text = response_text

    def chat(self, request):
        return ChatResponse(
            text=self.response_text,
            usage=Usage(1, 1, 2, source="mock"),
            model=request.model,
        )

    def chat_stream(self, request):
        raise AssertionError("stream not expected")

    def create(self, request):
        return chat_response_to_kemo(self.chat(kemo_request_to_chat(request)), request)


def _make_root(users: list[str]) -> tuple[tempfile.TemporaryDirectory, Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "config").mkdir()
    (root / "config" / "global_config.json").write_text(
        json.dumps(CONFIG), "utf-8"
    )
    (root / "config" / "global_soul.md").write_text("SOUL", "utf-8")
    (root / "agents.md").write_text("AGENTS", "utf-8")
    for user in users:
        (root / "users" / user / "task_plan").mkdir(parents=True)
        (root / "users" / user / "user_config.json").write_text("{}", "utf-8")
    return temporary, root


def _make_plan(steps: list[dict], **kwargs) -> dict:
    return normalize_plan(
        title=kwargs.get("title", "Test Plan"),
        description=kwargs.get("description", "Test"),
        user=kwargs.get("user", "alice"),
        steps=steps,
    )


class PlanStoreTests(unittest.TestCase):
    def test_create_read_list_update_delete(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "Step A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        created = store.create(plan)
        self.assertEqual(created["status"], "pending")
        self.assertEqual(created["revision"], 1)

        read = store.read(created["plan_id"])
        self.assertEqual(read["plan_id"], created["plan_id"])

        plans = store.list_plans()
        self.assertEqual(len(plans), 1)

        updated = store.update(created["plan_id"], lambda p: {**p, "status": "approved"})
        self.assertEqual(updated["status"], "approved")
        self.assertEqual(updated["revision"], 2)

        self.assertTrue(store.delete(created["plan_id"]))
        with self.assertRaises(PlanNotFoundError):
            store.read(created["plan_id"])

    def test_duplicate_plan_id_conflict(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store.create(plan)
        with self.assertRaises(PlanConflictError):
            store.create(plan)

    def test_cycle_dependency_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            _make_plan([
                {"step_id": "step_1", "title": "A", "description": "A",
                 "depends_on": ["step_2"], "tool_name": None,
                 "tool_arguments": {}, "critical": True},
                {"step_id": "step_2", "title": "B", "description": "B",
                 "depends_on": ["step_1"], "tool_name": None,
                 "tool_arguments": {}, "critical": True},
            ])

    def test_blocked_tool_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            _make_plan([
                {"step_id": "step_1", "title": "A", "description": "A",
                 "tool_name": "task_plan_create", "tool_arguments": {},
                 "critical": True},
            ])

    def test_unknown_tool_rejected(self) -> None:
        with self.assertRaises(PlanValidationError):
            normalize_plan(
                title="T", description="D", user="alice",
                steps=[{"step_id": "step_1", "title": "A", "description": "A",
                        "tool_name": "nonexistent_tool", "tool_arguments": {},
                        "critical": True}],
                tool_names={"get_current_time"},
            )

    def test_multi_user_isolation(self) -> None:
        _, root = _make_root(["alice", "bob"])
        alice_store = PlanStore(root, "alice")
        bob_store = PlanStore(root, "bob")
        alice_plan = _make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ], user="alice")
        bob_plan = _make_plan([
            {"step_id": "step_1", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ], user="bob")
        alice_store.create(alice_plan)
        bob_store.create(bob_plan)
        self.assertEqual(len(alice_store.list_plans()), 1)
        self.assertEqual(len(bob_store.list_plans()), 1)
        self.assertEqual(alice_store.list_plans()[0]["title"], "Test Plan")
        self.assertEqual(bob_store.list_plans()[0]["title"], "Test Plan")

    def test_recover_interrupted(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = store.create(_make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ]))
        # Simulate a crash mid-execution
        store.update(plan["plan_id"], lambda p: {
            **p, "status": "running",
            "steps": [{**p["steps"][0], "status": "running"}],
        })
        recovered = store.recover_interrupted()
        self.assertEqual(len(recovered), 1)
        plan = store.read(plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        self.assertEqual(plan["steps"][0]["status"], "pending")

    def test_corrupt_file_skipped_in_list(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = store.create(_make_plan([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ]))
        # Corrupt the file
        (store._dir / f"{plan['plan_id']}.json").write_text("not json", "utf-8")
        self.assertEqual(len(store.list_plans()), 0)
        with self.assertRaises(PlanError):
            store.read(plan["plan_id"])

    def test_reminder_is_persisted_and_legacy_plan_defaults_to_empty(self) -> None:
        _, root = _make_root(["alice"])
        store = PlanStore(root, "alice")
        plan = normalize_plan(
            title="Reminder",
            description="Reminder test",
            user="alice",
            reminder="当前任务计划已创建，请让用户点击批准后执行",
            steps=[{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        )
        store.create(plan)
        self.assertEqual(store.read(plan["plan_id"])["reminder"], plan["reminder"])

        path = store._path(plan["plan_id"])
        raw = json.loads(path.read_text("utf-8"))
        raw.pop("reminder")
        path.write_text(json.dumps(raw), "utf-8")
        self.assertEqual(store.read(plan["plan_id"])["reminder"], "")
        updated = store.update(plan["plan_id"], lambda item: item)
        self.assertEqual(updated["reminder"], "")


class PlanExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp, self.root = _make_root(["alice"])
        self.addCleanup(self._tmp.cleanup)

    def _plan_with_steps(self, steps: list[dict]) -> dict:
        store = PlanStore(self.root, "alice")
        plan = _make_plan(steps)
        return store.create(plan)

    def test_approve_and_execute_simple_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        types = [e.type for e in events]
        self.assertIn("done", types)
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")

    def test_pending_plan_cannot_execute(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        self.assertTrue(any(e.type == "error" for e in events))

    def test_auto_accept_off_still_creates_pending(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        self.assertEqual(plan["status"], "pending")

    def test_dependency_order(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "depends_on": ["step_1"], "tool_name": None,
             "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(plan["steps"][1]["status"], "completed")

    def test_scheduler_executes_approved_steps_through_main_agent(self) -> None:
        plan = self._plan_with_steps([
            {
                "step_id": "step_1",
                "title": "Collect",
                "description": "Collect data",
                "tool_name": "shell",
                "tool_arguments": {"command": "first"},
                "critical": True,
            },
            {
                "step_id": "step_2",
                "title": "Report",
                "description": "Write report",
                "depends_on": ["step_1"],
                "tool_name": "shell",
                "tool_arguments": {"command": "second"},
                "critical": True,
            },
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        requests: list[dict] = []

        def event_source(request, **kwargs):
            requests.append(dict(request))
            yield RunEvent(
                type="tool_call_result",
                tool_name="shell",
                result={"ok": True, "result": "done"},
                metadata={"status": "completed"},
            )
            yield RunEvent(type="text_delta", content="done")
            yield RunEvent(type="done", metadata={"committed": True})

        scheduler = TaskPlanScheduler(self.root, event_source=event_source)
        result = scheduler.scan_once()

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(requests), 2)
        self.assertIn("只执行当前步骤", requests[0]["prompt"])
        self.assertIn("step_1", requests[0]["prompt"])
        self.assertIn("step_2", requests[1]["prompt"])
        stored = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(
            [step["status"] for step in stored["steps"]],
            ["completed", "completed"],
        )

    def test_critical_failure_pauses_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": "get_current_time", "tool_arguments": {},
             "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {**p, "status": "approved"})
        # Use a registry where the tool fails
        from run.tools import ToolDefinition, ToolRegistry
        def fail_tool(**kwargs):
            raise RuntimeError("fail")
        fail_def = ToolDefinition(
            name="get_current_time", description="fail",
            input_schema={"type": "object", "properties": {}},
            version="1", enabled=True, entrypoint="tool.py:fail_tool",
            source="test", directory=self.root / "plugins" / "get_current_time",
            _callable=fail_tool,
        )
        registry = ToolRegistry({"get_current_time": fail_def})
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG, tool_registry=registry,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "paused")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        self.assertEqual(plan["steps"][0]["status"], "failed")

    def test_non_critical_failure_continues(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "Fail", "description": "F",
             "tool_name": "get_current_time", "tool_arguments": {},
             "critical": False},
            {"step_id": "step_2", "title": "OK", "description": "O",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {**p, "status": "approved"})
        from run.tools import ToolDefinition, ToolRegistry
        def fail_tool(**kwargs):
            raise RuntimeError("fail")
        fail_def = ToolDefinition(
            name="get_current_time", description="fail",
            input_schema={"type": "object", "properties": {}},
            version="1", enabled=True, entrypoint="tool.py:fail_tool",
            source="test", directory=self.root / "plugins" / "get_current_time",
            _callable=fail_tool,
        )
        registry = ToolRegistry({"get_current_time": fail_def})
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG, tool_registry=registry,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["steps"][0]["status"], "failed")
        self.assertEqual(plan["steps"][1]["status"], "completed")

    def test_cancel_plan(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        cancel_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "cancelled")
        self.assertEqual(plan["steps"][0]["status"], "cancelled")
        self.assertEqual(plan["steps"][1]["status"], "cancelled")

    def test_pause_and_resume(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        approve_plan(self.root, "alice", plan["plan_id"])
        pause_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "paused")
        resume_plan(self.root, "alice", plan["plan_id"])
        plan = get_plan(self.root, "alice", plan["plan_id"])
        self.assertEqual(plan["status"], "running")

    def test_completed_step_not_replayed(self) -> None:
        plan = self._plan_with_steps([
            {"step_id": "step_1", "title": "A", "description": "A",
             "tool_name": None, "tool_arguments": {}, "critical": True},
            {"step_id": "step_2", "title": "B", "description": "B",
             "tool_name": None, "tool_arguments": {}, "critical": True},
        ])
        store = PlanStore(self.root, "alice")
        store.update(plan["plan_id"], lambda p: {
            **p, "status": "approved",
            "steps": [
                {**p["steps"][0], "status": "completed"},
                p["steps"][1],
            ],
        })
        events = list(execute_plan(
            root=self.root, user="alice", plan_id=plan["plan_id"],
            config=CONFIG,
        ))
        done = next(e for e in events if e.type == "done")
        self.assertEqual(done.metadata["status"], "completed")
        plan = get_plan(self.root, "alice", plan["plan_id"])
        # step_1 was already completed, should not have been re-run
        self.assertEqual(plan["steps"][0]["status"], "completed")
        self.assertEqual(plan["steps"][1]["status"], "completed")


class PlanGenerationTests(unittest.TestCase):
    def _capturing_runner(self, response: dict, calls: list[dict]):
        class Runner:
            def __init__(self, *args, **kwargs):
                pass

            def run(self, name, input_data, **kwargs):
                calls.append(input_data)
                return AgentRunResult(
                    agent="task_plan",
                    data=response,
                    raw_text="",
                    usage={"total_tokens": 1},
                    model="mock",
                )

        return Runner

    def test_generate_plan_with_mock_provider(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        plan_json = json.dumps({
            "action": "create",
            "title": "Test Plan",
            "description": "Test goal",
            "steps": [
                {"step_id": "step_1", "title": "A", "description": "A",
                 "tool_name": None, "tool_arguments": {}, "critical": True},
            ],
        })
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            plan = generate_plan(
                root=root, user="alice", goal="test goal",
                provider_factory=lambda _: MockProvider(plan_json),
                config=CONFIG,
            )
        self.assertEqual(plan["title"], "Test Plan")
        self.assertEqual(len(plan["steps"]), 1)
        self.assertEqual(plan["status"], "pending")

    def test_subagent_result_is_persisted_for_web_plan_bubble(self) -> None:
        _, root = _make_root(["alice"])
        plan = persist_agent_result(
            root=root,
            user="alice",
            input_data={"action": "create", "goal": "测试任务", "auto_accept": False},
            result_data={
                "action": "create",
                "title": "测试任务计划",
                "description": "测试任务",
                "steps": [{
                    "step_id": "step_1",
                    "title": "检查结果",
                    "description": "确认计划已落盘",
                    "depends_on": [],
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                }],
            },
            source="web",
            session_id="web-session",
            config=CONFIG,
        )
        self.assertIsNotNone(plan)
        stored = PlanStore(root, "alice").read(plan["plan_id"])
        self.assertEqual(stored["session_id"], "web-session")
        self.assertEqual(stored["status"], "pending")
        self.assertEqual(stored["reminder"], "当前任务计划已创建，请让用户点击批准后执行")

    def test_generate_plan_skip(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        skip_json = json.dumps({"action": "skip", "message": "too simple"})
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            with self.assertRaises(PlanSkipped):
                generate_plan(
                    root=root, user="alice", goal="hello",
                    provider_factory=lambda _: MockProvider(skip_json),
                    config=CONFIG,
                )

    def test_generate_plan_invalid_json(self) -> None:
        _, root = _make_root(["alice"])
        (root / "agents").mkdir()
        (root / "agents" / "task_plan").mkdir()
        (root / "agents" / "task_plan" / "agent.json").write_text(
            json.dumps({
                "schema_version": 1, "name": "task_plan", "version": "2.0.0",
                "description": "plan", "enabled": True, "instruction": "任务计划.txt",
                "model_profile": "default", "timeout": 10, "execution": "sync",
                "write_policy": "none",
                "input_schema": {"type": "object", "additionalProperties": True},
                "output_schema": {"type": "object", "additionalProperties": True},
            }), "utf-8",
        )
        (root / "agents" / "task_plan" / "任务计划.txt").write_text(
            "test instruction", "utf-8",
        )
        with patch.dict(os.environ, {"TEST_PLAN_KEY": "x"}, clear=False):
            with self.assertRaises(PlanGenerationError):
                generate_plan(
                    root=root, user="alice", goal="test",
                    provider_factory=lambda _: MockProvider("not json at all"),
                    config=CONFIG,
                )

    def test_generation_injects_all_skills_and_knowledge_in_fixed_order(self) -> None:
        _, root = _make_root(["alice"])
        files = {
            root / "plugins" / "zeta" / "SKILL.md": "# zeta\n" + "Z" * 12000,
            root / "plugins" / "alpha" / "SKILL.md": "# alpha",
            root / "shared_skills" / "development" / "python" / "SKILL.md": "# shared python",
            root / "users" / "alice" / "user_skills" / "agent_create" / "deploy" / "SKILL.md": "# user deploy",
            root / "global_knowledge" / "data_structure.md": "GLOBAL INDEX",
            root / "shared_knowledge" / "data_structure.md": "SHARED INDEX",
            root / "users" / "alice" / "knowledge" / "data_structure.md": "USER INDEX",
        }
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, "utf-8")
        calls: list[dict] = []
        response = {
            "action": "create",
            "title": "Injected",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        with patch(
            "run.task_plan_service.AgentRunner",
            self._capturing_runner(response, calls),
        ):
            plan = generate_plan(
                root=root,
                user="alice",
                goal="goal",
                config=CONFIG,
                tool_registry=ToolRegistry({}),
            )

        input_data = calls[0]
        ordered = list(input_data)
        fields = [
            "available_tools",
            "plugin_skills",
            "shared_skills_text",
            "user_skills_text",
            "global_knowledge_index",
            "shared_knowledge_index",
            "user_knowledge_index",
        ]
        self.assertEqual(sorted(ordered.index(field) for field in fields), [ordered.index(field) for field in fields])
        self.assertLess(
            input_data["plugin_skills"].index("plugins/alpha/SKILL.md"),
            input_data["plugin_skills"].index("plugins/zeta/SKILL.md"),
        )
        self.assertIn("Z" * 12000, input_data["plugin_skills"])
        self.assertIn("shared python", input_data["shared_skills_text"])
        self.assertIn("user deploy", input_data["user_skills_text"])
        self.assertEqual(input_data["global_knowledge_index"], "GLOBAL INDEX")
        self.assertEqual(input_data["shared_knowledge_index"], "SHARED INDEX")
        self.assertEqual(input_data["user_knowledge_index"], "USER INDEX")
        self.assertFalse(input_data["auto_accept"])
        self.assertEqual(
            plan["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )

    def test_edit_rejects_terminal_and_running_statuses_before_model_call(self) -> None:
        _, root = _make_root(["alice"])
        base = _make_plan([{
            "step_id": "step_1",
            "title": "A",
            "description": "A",
            "tool_name": None,
            "tool_arguments": {},
            "critical": True,
        }])
        for status in ("running", "completed", "failed", "cancelled"):
            with self.subTest(status=status):
                plan = {**base, "status": status}
                with self.assertRaisesRegex(PlanGenerationError, "只能编辑"):
                    edit_plan(
                        root=root,
                        user="alice",
                        plan=plan,
                        edit_request="change",
                        config=CONFIG,
                    )

    def test_edit_preserves_completed_steps_and_passes_protection_summary(self) -> None:
        _, root = _make_root(["alice"])
        existing = normalize_plan(
            title="Existing",
            description="goal",
            user="alice",
            status="paused",
            reminder="old",
            steps=[
                {
                    "step_id": "step_1",
                    "title": "Done",
                    "description": "Finished work",
                    "status": "completed",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                    "result": {"ok": True},
                    "finished_at": "2026-07-19T00:00:00+00:00",
                },
                {
                    "step_id": "step_2",
                    "title": "Pending",
                    "description": "Old work",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                },
            ],
        )
        calls: list[dict] = []
        response = {
            "action": "edit",
            "title": "Existing",
            "description": "goal",
            "steps": [
                {"step_id": "step_1"},
                {
                    "step_id": "step_2",
                    "title": "Changed",
                    "description": "New work",
                    "tool_name": None,
                    "tool_arguments": {},
                    "critical": True,
                },
            ],
        }
        with patch(
            "run.task_plan_service.AgentRunner",
            self._capturing_runner(response, calls),
        ):
            edited = edit_plan(
                root=root,
                user="alice",
                plan=existing,
                edit_request="change step two",
                config=CONFIG,
            )

        self.assertEqual(
            calls[0]["completed_steps"],
            [{"step_id": "step_1", "title": "Done"}],
        )
        self.assertEqual(edited["status"], "paused")
        self.assertEqual(edited["steps"][0], existing["steps"][0])
        self.assertEqual(edited["steps"][1]["title"], "Changed")
        self.assertEqual(
            edited["reminder"],
            "当前任务计划已修改，请让用户点击批准后执行",
        )

    def test_edit_rejects_completed_step_mutation(self) -> None:
        _, root = _make_root(["alice"])
        existing = normalize_plan(
            title="Existing",
            description="goal",
            user="alice",
            status="approved",
            steps=[{
                "step_id": "step_1",
                "title": "Done",
                "description": "Finished",
                "status": "completed",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        )
        response = {
            "action": "edit",
            "title": "Existing",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "Tampered",
                "description": "Finished",
                "status": "completed",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        with patch(
            "run.task_plan_service.AgentRunner",
            self._capturing_runner(response, []),
        ):
            with self.assertRaisesRegex(PlanGenerationError, "不得修改"):
                edit_plan(
                    root=root,
                    user="alice",
                    plan=existing,
                    edit_request="tamper",
                    config=CONFIG,
                )

    def test_auto_accept_true_has_no_reminder(self) -> None:
        _, root = _make_root(["alice"])
        response = {
            "action": "create",
            "title": "Auto",
            "description": "goal",
            "steps": [{
                "step_id": "step_1",
                "title": "A",
                "description": "A",
                "tool_name": None,
                "tool_arguments": {},
                "critical": True,
            }],
        }
        config = {**CONFIG, "task_plan": {"auto_accept": True, "max_steps": 10}}
        with patch(
            "run.task_plan_service.AgentRunner",
            self._capturing_runner(response, []),
        ):
            plan = generate_plan(root=root, user="alice", goal="goal", config=config)
        self.assertEqual(plan["reminder"], "")

    def test_task_plan_executor_normalizes_reminder(self) -> None:
        class Context:
            def run_model(self, input_data):
                return AgentRunResult(
                    agent="task_plan",
                    data={"action": input_data["action"], "steps": []},
                    raw_text="",
                    usage={},
                    model="mock",
                )

        created = execute_task_plan_agent(
            Context(),
            {"action": "create", "auto_accept": False},
        )
        self.assertEqual(
            created.data["reminder"],
            "当前任务计划已创建，请让用户点击批准后执行",
        )
        edited = execute_task_plan_agent(
            Context(),
            {"action": "edit", "auto_accept": True},
        )
        self.assertEqual(edited.data["reminder"], "")


if __name__ == "__main__":
    unittest.main()
