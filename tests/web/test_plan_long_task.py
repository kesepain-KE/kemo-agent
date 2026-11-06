from __future__ import annotations

from tests.type_assertions import require_present

import tempfile
import threading
import unittest
import json
import os
import time
from pathlib import Path
from unittest.mock import patch

from events import RunEvent
from plugins.task_plan.tool import run as run_task_plan_tool
from run.history import reserve_session
from run.tasks import PlanStore, normalize_plan
from web.service import WebRunService


class PlanLongTaskTests(unittest.TestCase):
    def test_plan_close_before_first_next_releases_slot_and_pauses_plan(self):
        root, store, plan = self.make_plan()
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        thread_ids = []

        def source(*_args, **_kwargs):
            thread_ids.append(threading.get_ident())
            entered.set()
            try:
                release.wait(3)
                yield RunEvent(type="done")
            finally:
                thread_ids.append(threading.get_ident())
                finished.set()

        service = WebRunService(root, event_source=source)
        events = service.stream_plan(
            "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
        )
        try:
            self.assertTrue(entered.wait(1))
            events.close()
            events.close()
            self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
            self.assertEqual(store.read(plan["plan_id"])["status"], "paused")
            self.assertFalse(finished.is_set())
            self.assertEqual(list(events), [])
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
            deadline = time.monotonic() + 2
            while service._active_runs and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(service._active_runs)
        self.assertEqual(len(thread_ids), 2)
        self.assertEqual(thread_ids[0], thread_ids[1])
        self.assertNotEqual(thread_ids[0], threading.get_ident())

    def test_plan_close_interrupts_concurrent_next_without_waiting_for_tool(self):
        root, store, plan = self.make_plan()
        release = threading.Event()
        entered = threading.Event()
        result = []

        def source(*_args, **_kwargs):
            entered.set()
            release.wait(3)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        events = service.stream_plan(
            "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
        )
        reader = threading.Thread(target=lambda: result.extend(list(events)))
        reader.start()
        try:
            self.assertTrue(entered.wait(1))
            events.close()
            reader.join(1)
            self.assertFalse(reader.is_alive())
            self.assertFalse(release.is_set())
            self.assertEqual(result, [])
            self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
            self.assertEqual(store.read(plan["plan_id"])["status"], "paused")
        finally:
            release.set()
            reader.join(2)
            deadline = time.monotonic() + 2
            while service._active_runs and time.monotonic() < deadline:
                time.sleep(0.005)
            self.assertFalse(service._active_runs)

    def make_plan(self, *, source="web", auto_accept=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        reserve_session(root, "alice", source, "plan-session")
        store = PlanStore(root, "alice")
        plan = store.create(normalize_plan(
            title="跨 Run 执行计划",
            description="完成两步，不重复已完成的操作",
            user="alice", source=source, session_id="plan-session",
            auto_accept=auto_accept,
            steps=[
                {"step_id": "step_1", "title": "第一步", "description": "执行第一步", "critical": True},
                {"step_id": "step_2", "title": "第二步", "description": "执行第二步", "critical": True, "depends_on": ["step_1"]},
            ],
        ))
        return root, store, plan

    def finish_step(self, root, plan, request, step_id):
        result = run_task_plan_tool(
            action="step_done", plan_id=plan["plan_id"], step_id=step_id,
            result=f"{step_id} 已完成",
            context={
                "root": str(root), "user": "alice", "source": request["source"],
                "session_id": request["session_id"],
                "task_plan_id": request["_task_plan_id"],
                "task_plan_mode": request["_task_plan_mode"],
            },
        )
        self.assertTrue(result["ok"], result)












    def test_real_engine_finishes_plan_in_one_run(self):
        from provider.adapters.compat import chat_response_to_kemo
        from provider.schema import ChatResponse, ToolCall, Usage
        from run.engine import iter_request_events
        from tests.card_history_support import session_history
        from run.tools import ToolDefinition, ToolRegistry

        root, store, plan = self.make_plan()
        (root / "config").mkdir()
        (root / "config" / "global_config.yaml").write_text(json.dumps({
            "tools": {"enabled": True, "timeout": 5},
            "memory": {"extraction_mode": "disabled"},
        }), "utf-8")
        (root / "users" / "alice" / "user_config.yaml").write_text(json.dumps({
            "schema_version": 1,
            "provider": {"type": "kemo", "base_url": "http://127.0.0.1:1/v1",
                         "api_key_env": "KEMO_API_KEY", "model": "mock", "stream": False},
        }), "utf-8")
        executed = []

        def step_done(*, step_id, context):
            executed.append(step_id)
            return run_task_plan_tool(
                action="step_done", plan_id=plan["plan_id"], step_id=step_id,
                result=f"{step_id} 完成", context=context,
            )

        registry = ToolRegistry({"task_plan": ToolDefinition(
            name="task_plan", description="记录已完成步骤",
            input_schema={"type": "object", "properties": {"step_id": {"type": "string"}},
                          "required": ["step_id"], "additionalProperties": False},
            version="1", enabled=True, entrypoint="tool.py:run", source="test",
            directory=root / "plugins" / "task_plan", _callable=step_done, execution_mode="thread",
        )})
        responses = [
            ChatResponse(text="", tool_calls=[
                ToolCall("first", "task_plan", {"step_id": "step_1"}),
            ], finish_reason="tool_calls", usage=Usage(2, 1, 3)),
            ChatResponse(text="", tool_calls=[ToolCall("second", "task_plan", {"step_id": "step_2"})],
                         finish_reason="tool_calls", usage=Usage(2, 1, 3)),
            ChatResponse(text="计划完成", usage=Usage(2, 1, 3)),
        ]
        provider_requests = []

        class Provider:
            def create(self, request):
                provider_requests.append(request)
                return chat_response_to_kemo(responses.pop(0), request)

        provider = Provider()

        def source(request, **kwargs):
            yield from iter_request_events(
                {**request, "stream": False}, provider_factory=lambda _: provider,
                tool_registry_factory=lambda *_: registry, **kwargs,
            )

        with patch.dict(os.environ, {"KEMO_API_KEY": "test-only-placeholder"}):
            events = list(WebRunService(root, event_source=source).stream_plan(
                "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
            ))
        self.assertEqual(executed, ["step_1", "step_2"])
        self.assertEqual(store.read(plan["plan_id"])["status"], "completed")
        self.assertEqual(sum(e.type in {"done", "error"} for e in events), 1)
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].metadata["status"], "completed")
        self.assertIn("[x] 执行第一步", provider_requests[1].model_dump_json())
        self.assertIn("[ ] 执行第二步", provider_requests[1].model_dump_json())
        history = require_present(session_history(root, "alice", "web", "plan-session"))
        self.assertEqual(len(history.integrations), 1)
        self.assertEqual(history.integrations[0]["status"], "completed")
        user_messages = [m for m in history.messages if m["role"] == "user"]
        self.assertEqual(len(user_messages), 1)
