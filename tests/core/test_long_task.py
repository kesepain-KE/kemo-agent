from __future__ import annotations

import tempfile
import threading
import unittest
import queue
from pathlib import Path
from unittest.mock import patch

from events import RunEvent
from run.history import reserve_session
from run.long_task import (
    activate_long_task,
    finish_long_task,
    get_long_task_state,
    reconcile_orphaned_long_task,
    set_long_task_enabled,
)
from run.long_task import semantic_user_text
from run.scheduler import _summary_rounds
from run.memory import memory_round_payload
from run.conversation import GuidanceMailbox
from web.service import WebRunService


class LongTaskTests(unittest.TestCase):
    def make_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice" / "history").mkdir(parents=True)
        return temporary, root

    def reserve(self, root: Path, session_id: str, source: str = "web") -> None:
        reserve_session(root, "alice", source, session_id)

    def test_preference_is_isolated_by_session_and_source(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "a")
        self.reserve(root, "b")
        self.reserve(root, "a", "app")

        self.assertFalse(get_long_task_state(root, "alice", "web", "a")["enabled"])
        enabled = set_long_task_enabled(root, "alice", "web", "a", True)
        self.assertTrue(enabled["enabled"])
        self.assertEqual(enabled["status"], "enabled")
        self.assertFalse(get_long_task_state(root, "alice", "web", "b")["enabled"])
        self.assertFalse(get_long_task_state(root, "alice", "app", "a")["enabled"])

    def test_idempotent_enable_preserves_active_and_finished_statistics(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "active")
        set_long_task_enabled(root, "alice", "web", "active", True)
        active = activate_long_task(
            root, "alice", "web", "active", original_prompt="检查十小时任务"
        )
        self.assertIsNotNone(active)
        active = set_long_task_enabled(root, "alice", "web", "active", True)
        self.assertEqual(active["status"], "running")
        task_id = active["task_id"]

        finished = finish_long_task(
            root,
            "alice",
            "web",
            "active",
            status="completed",
            stop_reason="completed",
        )
        repeated = set_long_task_enabled(root, "alice", "web", "active", True)
        self.assertEqual(repeated["status"], "completed")
        self.assertEqual(repeated["task_id"], task_id)
        self.assertEqual(repeated["finished_at"], finished["finished_at"])

    def test_disabling_does_not_override_cancelling(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "cancel")
        set_long_task_enabled(root, "alice", "web", "cancel", True)
        activate_long_task(root, "alice", "web", "cancel", original_prompt="执行")
        from run.long_task import request_long_task_cancel

        request_long_task_cancel(root, "alice", "web", "cancel")
        state = set_long_task_enabled(root, "alice", "web", "cancel", False)
        self.assertEqual(state["status"], "cancelling")
        self.assertFalse(state["enabled"])

    def test_orphaned_active_state_is_reconciled_without_overwriting_terminal_state(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "orphaned")
        set_long_task_enabled(root, "alice", "web", "orphaned", True)
        active = activate_long_task(
            root,
            "alice",
            "web",
            "orphaned",
            original_prompt="执行长任务",
        )
        self.assertIsNotNone(active)
        active_updated_at = active["updated_at"]

        grace_protected = reconcile_orphaned_long_task(
            root,
            "alice",
            "web",
            "orphaned",
            has_live_run=False,
            grace_seconds=60,
        )
        self.assertEqual(grace_protected["status"], "running")
        self.assertEqual(grace_protected["updated_at"], active_updated_at)

        preserved = reconcile_orphaned_long_task(
            root,
            "alice",
            "web",
            "orphaned",
            has_live_run=True,
            grace_seconds=0,
        )
        self.assertEqual(preserved["status"], "running")

        interrupted = reconcile_orphaned_long_task(
            root,
            "alice",
            "web",
            "orphaned",
            has_live_run=False,
            grace_seconds=0,
            stop_reason="process_restarted",
        )
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertEqual(interrupted["last_stop_reason"], "process_restarted")
        self.assertEqual(interrupted["current_run_id"], "")
        self.assertEqual(
            interrupted["last_error"]["code"], "orphaned_long_task"
        )
        interrupted_updated_at = interrupted["updated_at"]

        unchanged = reconcile_orphaned_long_task(
            root,
            "alice",
            "web",
            "orphaned",
            has_live_run=False,
            grace_seconds=0,
            stop_reason="must_not_replace_terminal",
        )
        self.assertEqual(unchanged["status"], "interrupted")
        self.assertEqual(unchanged["last_stop_reason"], "process_restarted")
        self.assertEqual(unchanged["updated_at"], interrupted_updated_at)

    def test_orphaned_cancelling_state_finishes_as_cancelled(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "orphaned-cancel")
        set_long_task_enabled(root, "alice", "web", "orphaned-cancel", True)
        activate_long_task(
            root,
            "alice",
            "web",
            "orphaned-cancel",
            original_prompt="执行长任务",
        )
        from run.long_task import request_long_task_cancel

        request_long_task_cancel(root, "alice", "web", "orphaned-cancel")
        cancelled = reconcile_orphaned_long_task(
            root,
            "alice",
            "web",
            "orphaned-cancel",
            has_live_run=False,
            grace_seconds=0,
            stop_reason="orphaned_user_cancel",
        )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["last_stop_reason"], "orphaned_user_cancel")
        self.assertIsNone(cancelled["last_error"])

    def test_only_max_tool_iteration_terminal_continues_and_emits_one_done(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "continued")
        set_long_task_enabled(root, "alice", "web", "continued", True)
        requests: list[dict[str, object]] = []

        def source(request, **_kwargs):
            requests.append(dict(request))
            if len(requests) == 1:
                yield RunEvent(
                    type="done",
                    usage={"total_tokens": 10, "provider_request_count": 2},
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                        "elapsed_ms": 20,
                        "tool_calls": 4,
                    },
                )
            else:
                yield RunEvent(type="text_delta", content="完成")
                yield RunEvent(
                    type="done",
                    usage={"total_tokens": 5, "provider_request_count": 1},
                    metadata={
                        "committed": True,
                        "status": "completed",
                        "stop_reason": "completed",
                        "elapsed_ms": 10,
                        "tool_calls": 1,
                    },
                )

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "continued",
                "完成一项长任务",
                cancel_event=threading.Event(),
                run_id="run_initial",
            )
        )

        self.assertEqual([event.type for event in events], ["long_task_update", "text_delta", "done"])
        self.assertEqual(sum(event.type == "done" for event in events), 1)
        update = events[0]
        self.assertEqual(update.metadata["previous_run_id"], "run_initial")
        self.assertNotEqual(update.metadata["next_run_id"], "run_initial")
        self.assertEqual(requests[1]["run_id"], update.metadata["next_run_id"])
        self.assertTrue(requests[1]["_user_metadata"]["synthetic"])
        final_state = events[-1].metadata["long_task_state"]
        self.assertEqual(final_state["status"], "completed")
        self.assertEqual(final_state["run_count"], 2)
        self.assertEqual(final_state["continuation_count"], 1)
        self.assertEqual(final_state["total_tool_calls"], 5)
        self.assertEqual(final_state["total_provider_requests"], 3)
        self.assertEqual(final_state["usage"]["total_tokens"], 15)

    def test_continuation_handoff_never_looks_orphaned_to_state_polling(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "handoff")
        set_long_task_enabled(root, "alice", "web", "handoff", True)
        requests: list[str] = []

        def source(request, **_kwargs):
            requests.append(str(request["run_id"]))
            if len(requests) == 1:
                yield RunEvent(
                    type="done",
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                    },
                )
            else:
                yield RunEvent(
                    type="done",
                    metadata={"committed": True, "status": "completed"},
                )

        service = WebRunService(root, event_source=source)
        import web.service as web_service_module

        original = web_service_module.set_long_task_current_run
        observed_statuses: list[str] = []

        def observe_handoff(*args, **kwargs):
            observed_statuses.append(
                service.long_task_state("alice", "handoff")["long_task"]["status"]
            )
            return original(*args, **kwargs)

        with patch.object(
            web_service_module,
            "set_long_task_current_run",
            side_effect=observe_handoff,
        ):
            events = list(
                service.stream_chat(
                    "alice",
                    "handoff",
                    "继续执行",
                    cancel_event=threading.Event(),
                    run_id="run_handoff_initial",
                )
            )

        self.assertEqual(observed_statuses, ["running"])
        self.assertEqual(events[-1].metadata["long_task_state"]["status"], "completed")

    def test_continuation_alias_guidance_is_delivered_to_replacement_mailbox(self) -> None:
        _, root = self.make_root()
        session_id = "handoff-guidance"
        self.reserve(root, session_id)
        set_long_task_enabled(root, "alice", "web", session_id, True)
        requests: list[dict[str, object]] = []
        received: list[object] = []
        close_started = threading.Event()
        allow_close = threading.Event()

        def source(request, **_kwargs):
            requests.append(dict(request))
            if len(requests) == 1:
                yield RunEvent(
                    type="done",
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                    },
                )
                return
            try:
                received.append(request["_guidance_queue"].get(timeout=2))
            except queue.Empty:
                received.append(None)
            yield RunEvent(
                type="done",
                metadata={"committed": True, "status": "completed"},
            )

        service = WebRunService(root, event_source=source)
        stream = service.stream_chat(
            "alice",
            session_id,
            "执行长任务",
            cancel_event=threading.Event(),
            run_id="run_handoff_guidance",
        )
        with service._active_runs_lock:
            initial_mailbox = service._active_runs["run_handoff_guidance"].guidance

        original_close = GuidanceMailbox.close

        def blocking_close(mailbox: GuidanceMailbox) -> None:
            if mailbox is initial_mailbox and not close_started.is_set():
                close_started.set()
                if not allow_close.wait(timeout=2):
                    raise AssertionError("长任务续跑 mailbox 关闭未完成")
            original_close(mailbox)

        captured: list[RunEvent] = []
        worker = threading.Thread(target=lambda: captured.extend(stream))
        with patch.object(GuidanceMailbox, "close", new=blocking_close):
            worker.start()
            self.assertTrue(close_started.wait(timeout=2))

            submitted: dict[str, object] = {}
            submit_called = threading.Event()
            submit_done = threading.Event()
            original_submit = service.submit_guidance

            def submit_guidance(*args, **kwargs):
                submit_called.set()
                try:
                    submitted["value"] = original_submit(*args, **kwargs)
                finally:
                    submit_done.set()

            with patch.object(service, "submit_guidance", side_effect=submit_guidance):
                submitter = threading.Thread(
                    target=lambda: service.submit_guidance(
                        "alice",
                        "run_handoff_guidance",
                        "交给续跑",
                        source="web",
                        session_id=session_id,
                    )
                )
                submitter.start()
                self.assertTrue(submit_called.wait(timeout=2))
                # The hand-off owns the service lock while closing/replacing
                # the mailbox; submission through the old alias must wait.
                self.assertFalse(submit_done.wait(timeout=0.2))
                allow_close.set()
                submitter.join(timeout=3)
                self.assertFalse(submitter.is_alive())

            worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(submitted["value"]["status"], "accepted_current_run")
        self.assertEqual(received, ["交给续跑"])
        self.assertEqual([event.type for event in captured], ["long_task_update", "done"])

    def test_interleaved_long_tasks_keep_continuations_in_their_own_sessions(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "long-a")
        self.reserve(root, "long-b")
        set_long_task_enabled(root, "alice", "web", "long-a", True)
        requests: list[tuple[str, str]] = []
        request_counts: dict[str, int] = {}
        lock = threading.Lock()

        def source(request, **_kwargs):
            session_id = str(request["session_id"])
            with lock:
                request_counts[session_id] = request_counts.get(session_id, 0) + 1
                ordinal = request_counts[session_id]
                requests.append((str(request["source"]), session_id))
            if session_id == "long-a" and ordinal == 1:
                yield RunEvent(
                    type="done",
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                    },
                )
                return
            yield RunEvent(
                type="done",
                metadata={"committed": True, "status": "completed"},
            )

        service = WebRunService(root, event_source=source)
        captured: dict[str, list[RunEvent]] = {"long-a": [], "long-b": []}
        workers = [
            threading.Thread(
                target=lambda session=session: captured[session].extend(
                    service.stream_chat(
                        "alice",
                        session,
                        "执行",
                        cancel_event=threading.Event(),
                        run_id=f"run_{session.replace('-', '_')}_123",
                    )
                )
            )
            for session in captured
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())

        self.assertEqual(
            request_counts,
            {"long-a": 2, "long-b": 1},
        )
        self.assertTrue(all(source_name == "web" for source_name, _ in requests))
        self.assertEqual(
            {session for _, session in requests},
            {"long-a", "long-b"},
        )
        self.assertEqual(
            [event.type for event in captured["long-a"]],
            ["long_task_update", "done"],
        )
        self.assertEqual([event.type for event in captured["long-b"]], ["done"])
        self.assertTrue(
            all(
                event.metadata["session_id"] == "long-a"
                for event in captured["long-a"]
            )
        )
        self.assertTrue(
            all(
                event.metadata["session_id"] == "long-b"
                for event in captured["long-b"]
            )
        )
        self.assertEqual(
            get_long_task_state(root, "alice", "web", "long-a")["run_count"],
            2,
        )
        self.assertEqual(
            get_long_task_state(root, "alice", "web", "long-b")["run_count"],
            0,
        )

    def test_disabled_and_non_tool_limit_runs_do_not_continue(self) -> None:
        for session_id, enabled, stop_reason in (
            ("disabled", False, "max_tool_iterations"),
            ("context", True, "tool_context_limit"),
        ):
            with self.subTest(session_id=session_id):
                _, root = self.make_root()
                self.reserve(root, session_id)
                if enabled:
                    set_long_task_enabled(root, "alice", "web", session_id, True)
                seen: list[str] = []

                def source(request, **_kwargs):
                    seen.append(str(request["run_id"]))
                    yield RunEvent(
                        type="done",
                        metadata={"status": "limited", "stop_reason": stop_reason},
                    )

                events = list(
                    WebRunService(root, event_source=source).stream_chat(
                        "alice", session_id, "执行", cancel_event=threading.Event()
                    )
                )
                self.assertEqual([event.type for event in events], ["done"])
                self.assertEqual(len(seen), 1)

    def test_continuation_stream_without_terminal_is_interrupted(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "missing-terminal")
        set_long_task_enabled(root, "alice", "web", "missing-terminal", True)
        requests: list[str] = []

        def source(request, **_kwargs):
            requests.append(str(request["run_id"]))
            if len(requests) == 1:
                yield RunEvent(
                    type="done",
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                    },
                )
            else:
                yield RunEvent(type="text_delta", content="未完成")

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "missing-terminal",
                "执行长任务",
                cancel_event=threading.Event(),
                run_id="run_missing_terminal",
            )
        )

        self.assertEqual(
            [event.type for event in events],
            ["long_task_update", "text_delta", "error"],
        )
        self.assertEqual(events[-1].error["code"], "LONG_TASK_MISSING_TERMINAL")
        self.assertEqual(events[-1].metadata["status"], "interrupted")
        self.assertFalse(events[-1].metadata["committed"])
        self.assertEqual(
            get_long_task_state(root, "alice", "web", "missing-terminal")[
                "status"
            ],
            "interrupted",
        )
        self.assertFalse(service.has_active_runs())

    def test_continuation_engine_exception_is_interrupted_without_raw_message(self) -> None:
        _, root = self.make_root()
        self.reserve(root, "engine-exception")
        set_long_task_enabled(root, "alice", "web", "engine-exception", True)
        requests: list[str] = []
        secret = "PROVIDER_EXCEPTION_SECRET"

        def source(request, **_kwargs):
            requests.append(str(request["run_id"]))
            if len(requests) == 1:
                yield RunEvent(
                    type="done",
                    metadata={
                        "committed": True,
                        "status": "limited",
                        "stop_reason": "max_tool_iterations",
                    },
                )
                return
            raise RuntimeError(secret)
            yield  # pragma: no cover

        service = WebRunService(root, event_source=source)
        events = list(
            service.stream_chat(
                "alice",
                "engine-exception",
                "执行长任务",
                cancel_event=threading.Event(),
                run_id="run_engine_exception",
            )
        )

        self.assertEqual([event.type for event in events], ["long_task_update", "error"])
        serialized = str(events[-1].to_dict())
        self.assertNotIn(secret, serialized)
        self.assertEqual(events[-1].error["code"], "LONG_TASK_ENGINE_EXCEPTION")
        self.assertEqual(events[-1].metadata["status"], "interrupted")
        self.assertEqual(
            get_long_task_state(root, "alice", "web", "engine-exception")[
                "status"
            ],
            "interrupted",
        )
        self.assertFalse(service.has_active_runs())

    def test_synthetic_control_text_does_not_pollute_memory_or_summary(self) -> None:
        original = "检查整个项目并完成修复"
        message = {
            "role": "user",
            "content": "【长任务自动续跑】继续",
            "metadata": {
                "synthetic": True,
                "origin": "long_task_continuation",
                "long_task_original_prompt": original,
            },
        }
        window = {
            "text": {"messages": [message, {"role": "assistant", "content": "已完成下一阶段"}]},
            "think": {"rounds": []},
            "tool": {"rounds": []},
        }
        self.assertEqual(semantic_user_text(message, message["content"]), original)
        self.assertEqual(memory_round_payload(window, 1)["prompt"], original)
        self.assertEqual(_summary_rounds(window, 1)[0]["user"], original)


if __name__ == "__main__":
    unittest.main()
