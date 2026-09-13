from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from events import RunEvent
from run.conversation.tool_batch import _subagent_callback
from web.service import WebRunService


class SubagentProgressTests(unittest.TestCase):
    def test_progress_reaches_client_before_tool_finishes_and_is_ui_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "users" / "alice" / "history").mkdir(parents=True)
            release = threading.Event()
            callback = []

            def source(request, **_kwargs):
                yield RunEvent(type="tool_call_start", tool_name="subagent_dispatch", tool_call_id="call-a")
                publish = _subagent_callback(request, "call-a")
                callback.append(publish)
                publish(RunEvent(type="subagent_progress", content="private reasoning", arguments={"secret": "private"}, metadata={
                    "phase": "subagent", "agent": "task_plan", "task_id": "task-a",
                    "status": "tool_running", "iteration": 2, "tool_name": "file",
                    "source": "wrong-source", "session_id": "wrong-session",
                    "error": "private error", "prompt": "private prompt",
                }))
                self.assertTrue(release.wait(2), "client did not receive progress while tool was running")
                publish(RunEvent(type="reasoning_delta", metadata={"phase": "subagent", "agent": "task_plan", "status": "completed"}))
                yield RunEvent(type="tool_call_result", tool_name="subagent_dispatch", tool_call_id="call-a", result={"ok": True})
                yield RunEvent(type="done")

            stream = WebRunService(root, event_source=source).stream_chat(
                "alice", "session-a", "执行", run_id="run_test_a", cancel_event=threading.Event(),
            )
            try:
                self.assertEqual(next(stream).type, "tool_call_start")
                progress = next(stream)
                self.assertEqual(progress.type, "subagent_progress")
                self.assertEqual(progress.tool_call_id, "call-a")
                self.assertEqual(progress.metadata, {
                    "phase": "subagent", "agent": "task_plan", "task_id": "task-a",
                    "status": "tool_running", "tool_name": "file", "iteration": 2,
                    "source": "web", "session_id": "session-a", "run_id": "run_test_a",
                })
                self.assertNotIn("private", str(progress.to_dict()))
                self.assertEqual(RunEvent.from_dict(progress.to_dict()).type, "subagent_progress")
                release.set()
                self.assertEqual([event.type for event in stream], ["subagent_progress", "tool_call_result", "done"])
                # A detached task may finish after its parent stream: no stale
                # progress is enqueued after the terminal/consumer boundary.
                callback[0](RunEvent(type="reasoning_delta", metadata={"phase": "subagent", "status": "started"}))
            finally:
                release.set()
                stream.close()

    def test_dispatch_passes_per_call_callback_to_scheduler(self):
        from plugins.subagent_dispatch import tool

        for wait in (True, False):
            with self.subTest(wait=wait), tempfile.TemporaryDirectory() as temporary:
                received = []
                submitted = []
                callback = received.append

                class Scheduler:
                    def submit(self, agent, payload, **kwargs):
                        submitted.append(kwargs)
                        kwargs["event_callback"](RunEvent(type="reasoning_delta", metadata={"phase": "subagent", "agent": agent, "status": "queued"}))
                        return "task-a"

                result = SimpleNamespace(agent="custom", data={}, usage={}, model="mock", metadata={})
                with (
                    patch.object(tool, "_public", return_value=[SimpleNamespace(name="custom", timeout=1)]),
                    patch.object(tool, "load_config", return_value={}),
                    patch.object(tool, "prepare_main_agent_invocation", return_value=SimpleNamespace(synchronous_only=False, payload={})),
                    patch.object(tool, "get_agent_scheduler", return_value=Scheduler()),
                    patch.object(tool, "_wait_for_task", return_value=(True, result)),
                ):
                    tool.run(action="call", agent="custom", wait=wait, context={
                        "root": temporary, "user": "alice", "source": "web", "session_id": "session-a",
                        "event_callback": callback,
                    })
                self.assertEqual(len(received), 1)
                self.assertIs(submitted[0]["event_callback"], callback)
                self.assertEqual(submitted[0]["session_id"], "session-a")
