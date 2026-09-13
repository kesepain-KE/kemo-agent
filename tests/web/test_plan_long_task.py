from __future__ import annotations

import tempfile
import threading
import unittest
import json
import os
from pathlib import Path
from unittest.mock import patch

from events import RunEvent
from plugins.task_plan.tool import run as run_task_plan_tool
from run.history import reserve_session
from run.long_task import get_long_task_state, set_long_task_enabled
from run.tasks import PlanStore, normalize_plan
from web.service import WebRunService


class PlanLongTaskTests(unittest.TestCase):
    def make_plan(self, *, source="web", enabled=True, auto_accept=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        reserve_session(root, "alice", source, "plan-session")
        set_long_task_enabled(root, "alice", source, "plan-session", enabled)
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

    def terminal(self, status="limited", stop_reason="max_tool_iterations"):
        return RunEvent(type="done", usage={"total_tokens": 3, "provider_request_count": 1}, metadata={
            "committed": True, "status": status, "stop_reason": stop_reason,
            "tool_calls": 2, "elapsed_ms": 10,
        })

    def test_plan_continues_across_multiple_tool_limits_without_replaying_steps(self):
        for channel in ("web", "app"):
            for auto_accept in (False, True):
                with self.subTest(source=channel, auto_accept=auto_accept):
                    root, store, plan = self.make_plan(source=channel, auto_accept=auto_accept)
                    requests = []

                    def source(request, **_kwargs):
                        requests.append(dict(request))
                        self.assertEqual(request["_task_plan_id"], plan["plan_id"])
                        self.assertEqual(request["_task_plan_mode"], "agent_managed")
                        current = store.read(plan["plan_id"])
                        self.assertEqual(current["status"], "running")
                        if len(requests) == 1:
                            self.finish_step(root, plan, request, "step_1")
                        else:
                            self.assertEqual(current["steps"][0]["status"], "completed")
                            self.assertEqual(current["steps"][0]["result"], "step_1 已完成")
                            self.assertIn("step_done", request["prompt"])
                            self.assertIn("step_fail", request["prompt"])
                            self.assertIn(plan["plan_id"], request["prompt"])
                            self.assertTrue(request["_user_metadata"]["synthetic"])
                        if len(requests) == 3:
                            self.finish_step(root, plan, request, "step_2")
                            yield self.terminal("completed", "completed")
                        else:
                            yield self.terminal()

                    service = WebRunService(root, event_source=source)
                    events = list(service.stream_plan(
                        "alice", "plan-session", plan["plan_id"], source=channel,
                        run_id="run_plan_first", cancel_event=threading.Event(),
                    ))
                    self.assertEqual([e.type for e in events], ["long_task_update", "long_task_update", "done"])
                    self.assertEqual(len(requests), 3)
                    self.assertEqual(len({r["run_id"] for r in requests}), 3)
                    for index, event in enumerate(events[:-1]):
                        self.assertEqual(event.metadata["next_run_id"], requests[index + 1]["run_id"])
                    self.assertEqual(store.read(plan["plan_id"])["status"], "completed")
                    state = events[-1].metadata["long_task_state"]
                    self.assertEqual(state["status"], "completed")
                    self.assertEqual(state["run_count"], 3)
                    self.assertEqual(state["continuation_count"], 2)
                    self.assertEqual(state["total_tool_calls"], 6)
                    self.assertEqual(state["usage"]["total_tokens"], 9)

    def test_disabled_plan_and_non_tool_boundaries_do_not_continue(self):
        for enabled, status, reason in (
            (False, "limited", "max_tool_iterations"),
            (True, "limited", "tool_context_limit"),
            (True, "completed", "task_plan_approval_required"),
            (True, "completed", "task_plan_created"),
            (True, "cancelled", "user_emergency_stop"),
        ):
            with self.subTest(enabled=enabled, reason=reason):
                root, store, plan = self.make_plan(enabled=enabled)
                requests = []

                def source(request, **_kwargs):
                    requests.append(request)
                    yield self.terminal(status, reason)

                events = list(WebRunService(root, event_source=source).stream_plan(
                    "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
                ))
                self.assertEqual(len(requests), 1)
                self.assertEqual([e.type for e in events], ["done"])
                self.assertEqual(store.read(plan["plan_id"])["status"], "paused")

    def test_stopped_plan_is_not_resurrected_at_a_tool_limit(self):
        for plan_status in ("paused", "cancelled", "failed", "completed"):
            with self.subTest(plan_status=plan_status):
                root, store, plan = self.make_plan()
                requests = []

                def source(request, **_kwargs):
                    requests.append(request)
                    store.update(plan["plan_id"], lambda value: {**value, "status": plan_status})
                    yield self.terminal()

                events = list(WebRunService(root, event_source=source).stream_plan(
                    "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
                ))
                self.assertEqual(len(requests), 1)
                self.assertEqual([e.type for e in events], ["done"])
                self.assertEqual(store.read(plan["plan_id"])["status"], plan_status)

    def test_disabling_long_task_during_plan_continuation_pauses_unfinished_plan(self):
        root, store, plan = self.make_plan()
        requests = []

        def source(request, **_kwargs):
            requests.append(request)
            if len(requests) == 2:
                set_long_task_enabled(root, "alice", "web", "plan-session", False)
            yield self.terminal()

        events = list(WebRunService(root, event_source=source).stream_plan(
            "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
        ))
        self.assertEqual(len(requests), 2)
        self.assertEqual([e.type for e in events], ["long_task_update", "done"])
        self.assertEqual(store.read(plan["plan_id"])["status"], "paused")
        self.assertEqual(get_long_task_state(root, "alice", "web", "plan-session")["status"], "paused")

    def test_plan_stopped_during_handoff_does_not_start_another_run(self):
        for plan_status in ("paused", "cancelled", "failed", "completed"):
            with self.subTest(plan_status=plan_status):
                root, store, plan = self.make_plan()
                requests = []

                def source(request, **_kwargs):
                    requests.append(request)
                    yield self.terminal()

                from web import service as service_module
                original = service_module.set_long_task_current_run

                def stop_during_handoff(*args, **kwargs):
                    state = original(*args, **kwargs)
                    store.update(plan["plan_id"], lambda value: {**value, "status": plan_status})
                    return state

                with patch.object(service_module, "set_long_task_current_run", side_effect=stop_during_handoff):
                    events = list(WebRunService(root, event_source=source).stream_plan(
                        "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
                    ))
                self.assertEqual(len(requests), 1)
                self.assertEqual([e.type for e in events], ["long_task_update", "done"])
                self.assertEqual(events[-1].metadata["long_task_state"]["status"], plan_status)
                self.assertEqual(store.read(plan["plan_id"])["status"], plan_status)

    def test_plan_completion_on_a_limited_continuation_finishes_long_task(self):
        root, store, plan = self.make_plan()
        requests = []

        def source(request, **_kwargs):
            requests.append(request)
            self.finish_step(root, plan, request, f"step_{len(requests)}")
            yield self.terminal()

        events = list(WebRunService(root, event_source=source).stream_plan(
            "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
        ))
        self.assertEqual(len(requests), 2)
        self.assertEqual([e.type for e in events], ["long_task_update", "done"])
        self.assertEqual(events[-1].metadata["long_task_state"]["status"], "completed")
        self.assertEqual(store.read(plan["plan_id"])["status"], "completed")

    def test_plan_continuation_respects_cancel_provider_error_and_run_cap(self):
        for boundary in ("cancel", "provider_error", "run_cap"):
            with self.subTest(boundary=boundary):
                root, store, plan = self.make_plan()
                requests = []
                cancel_event = threading.Event()

                def source(request, **_kwargs):
                    requests.append(request)
                    if len(requests) == 2:
                        if boundary == "cancel":
                            service.cancel_long_task("alice", "plan-session")
                            yield self.terminal("cancelled", "user_emergency_stop")
                            return
                        if boundary == "provider_error":
                            yield RunEvent(type="error", error={"message": "provider failed"}, metadata={"status": "failed"})
                            return
                    yield self.terminal()

                service = WebRunService(root, event_source=source)
                with patch("web.service.MAX_LONG_TASK_RUNS", 2):
                    events = list(service.stream_plan(
                        "alice", "plan-session", plan["plan_id"], cancel_event=cancel_event,
                    ))
                self.assertEqual(len(requests), 2)
                expected = {"cancel": "cancelled", "provider_error": "failed", "run_cap": "paused"}[boundary]
                self.assertEqual(events[-1].metadata["long_task_state"]["status"], expected)
                self.assertEqual(store.read(plan["plan_id"])["status"], "paused")
                self.assertEqual(sum(e.type in {"done", "error"} for e in events), 1)

    def test_background_or_unowned_plan_cannot_gain_continuation(self):
        for mode, session in (("step", "plan-session"), ("agent_managed", "other-session")):
            with self.subTest(mode=mode, session=session):
                root, store, plan = self.make_plan()
                store.update(plan["plan_id"], lambda value: {**value, "status": "running"})
                if session != "plan-session":
                    reserve_session(root, "alice", "web", session)
                    set_long_task_enabled(root, "alice", "web", session, True)
                requests = []

                def source(request, **_kwargs):
                    requests.append(request)
                    yield self.terminal()

                events = list(WebRunService(root, event_source=source).stream_chat(
                    "alice", session, "执行", task_plan_id=plan["plan_id"],
                    task_plan_mode=mode, cancel_event=threading.Event(),
                ))
                self.assertEqual(len(requests), 1)
                self.assertEqual([e.type for e in events], ["done"])

    def test_cancel_race_converts_the_next_nonterminal_event_into_done(self):
        root, _store, _plan = self.make_plan(enabled=False)
        source_started = threading.Event()
        release_source = threading.Event()

        def source(_request, **_kwargs):
            source_started.set()
            release_source.wait(1)
            yield RunEvent(type="text_delta", content="late progress")

        service = WebRunService(root, event_source=source)
        events = service.stream_chat(
            "alice",
            "plan-session",
            "取消竞态测试",
            run_id="run_cancel_race",
            cancel_event=threading.Event(),
        )
        collected = []
        consumer = threading.Thread(target=lambda: collected.extend(list(events)))
        consumer.start()
        try:
            self.assertTrue(source_started.wait(1))
            response = service.cancel_run(
                "alice", "run_cancel_race", session_id="plan-session"
            )
            self.assertEqual(response["status"], "stopping")
        finally:
            release_source.set()
            consumer.join(2)

        self.assertFalse(consumer.is_alive())
        self.assertEqual([event.type for event in collected], ["done"])
        self.assertEqual(collected[0].metadata["status"], "cancelled")
        self.assertTrue(collected[0].metadata["cancelled"])
        self.assertFalse(collected[0].metadata["committed"])

    def test_real_engine_tool_limit_commits_history_and_continues_the_same_plan(self):
        from provider.adapters.compat import chat_response_to_kemo
        from provider.schema import ChatResponse, ToolCall, Usage
        from run.engine import iter_request_events
        from run.history import find_window, load_window
        from run.tools import ToolDefinition, ToolRegistry

        root, store, plan = self.make_plan()
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(json.dumps({
            "tools": {"enabled": True, "timeout": 5, "max_iterations": 1},
            "memory": {"extraction_mode": "disabled"},
        }), "utf-8")
        (root / "users" / "alice" / "user_config.json").write_text(json.dumps({
            "schema_version": 1,
            "provider": {"type": "kemo", "base_url": "http://127.0.0.1:1/v1",
                         "api_key_env": "TEST_PLAN_KEY", "model": "mock", "stream": False},
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
            directory=root / "plugins" / "task_plan", _callable=step_done,
        )})
        responses = [
            ChatResponse(text="", tool_calls=[
                ToolCall("first", "task_plan", {"step_id": "step_1"}),
                ToolCall("not-executed", "task_plan", {"step_id": "step_2"}),
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

        with patch.dict(os.environ, {"TEST_PLAN_KEY": "test-only-placeholder"}):
            events = list(WebRunService(root, event_source=source).stream_plan(
                "alice", "plan-session", plan["plan_id"], cancel_event=threading.Event(),
            ))
        self.assertEqual(executed, ["step_1", "step_2"])
        self.assertEqual(store.read(plan["plan_id"])["status"], "completed")
        self.assertEqual(sum(e.type == "long_task_update" for e in events), 1)
        self.assertEqual(sum(e.type in {"done", "error"} for e in events), 1)
        self.assertEqual(events[-1].type, "done")
        self.assertEqual(events[-1].metadata["long_task_state"]["status"], "completed")
        self.assertIn("[x] 执行第一步", provider_requests[1].system_prompt)
        self.assertIn("[ ] 执行第二步", provider_requests[1].system_prompt)
        history = load_window(find_window(root, "alice", "web", "plan-session"))
        self.assertEqual(history["data"]["rounds"], 2)
        self.assertEqual(history["data"]["round_metrics"][0]["stop_reason"], "max_tool_iterations")
        self.assertEqual(history["data"]["round_metrics"][0]["status"], "limited")
        user_messages = [m for m in history["text"]["messages"] if m["role"] == "user"]
        self.assertTrue(user_messages[1]["metadata"]["synthetic"])
        self.assertEqual(user_messages[1]["metadata"]["origin"], "long_task_continuation")
