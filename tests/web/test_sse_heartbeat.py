"""Transport timing, cancellation and ownership tests (not buffered-body checks)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest

import httpx

from events import RunEvent
from web.app import create_app
from web.auth import WebAuthConfig
from web.service import WebRunService
from web.services.event_stream import QueueEventStream
from web.streaming import HEARTBEAT_FRAME, StreamCleanup, heartbeat_seconds


class BlockingIterator:
    def __init__(self, values=None, *, blocked=True):
        self.values = iter(values if values is not None else [RunEvent(type="done")])
        self.release = threading.Event()
        self.entered = threading.Event()
        self.reading = False
        self.calls = 0
        self.close_calls = 0
        if not blocked:
            self.release.set()

    def __iter__(self):
        return self

    def __next__(self):
        if self.reading:
            raise AssertionError("concurrent next")
        self.reading = True
        self.calls += 1
        self.entered.set()
        try:
            if not self.release.wait(3):
                raise AssertionError("test source not released")
            return next(self.values)
        finally:
            self.reading = False

    def close(self):
        if self.reading:
            raise AssertionError("close raced with next")
        self.close_calls += 1


class Backend:
    def __init__(self, iterator):
        self.iterator = iterator
        self.cancel_event = None

    def sse_heartbeat_seconds(self, _user):
        # Fast transport tests; production configuration is bounded separately.
        return 0.02

    def stream_chat(self, *_args, cancel_event, **_kwargs):
        self.cancel_event = cancel_event
        return self.iterator

    stream_plan = stream_chat


async def wait_until(predicate, timeout: float = 2):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition did not become true")
        await asyncio.sleep(0.005)


async def invoke_chat(app, send, *, disconnected=None, spec="2.4", payload=None, headers=()):
    disconnected = disconnected or asyncio.Event()
    body = json.dumps(payload or {
        "user": "alice", "session_id": "heartbeat", "prompt": "hello",
        "run_id": "run_heartbeat",
    }).encode()
    request_sent = False

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        await disconnected.wait()
        return {"type": "http.disconnect"}

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec},
        "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/chat", "raw_path": b"/api/chat", "query_string": b"",
        "root_path": "", "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 80),
    }
    await app(scope, receive, send)


class HeartbeatConfigTests(unittest.TestCase):
    def test_config_has_finite_bounds_and_rejects_booleans(self):
        for value in (None, "bad", {}, [], True, False, float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                self.assertEqual(heartbeat_seconds(value), 15)
        for value in (-1, 0, 0.000001):
            self.assertEqual(heartbeat_seconds(value), 1)
        self.assertEqual(heartbeat_seconds("2.5"), 2.5)
        self.assertEqual(heartbeat_seconds(10**1000), 15)
        self.assertEqual(heartbeat_seconds(100), 30)

    def test_close_is_idempotent_and_works_before_first_next(self):
        released = []
        output = queue.Queue()
        cancelled = threading.Event()
        stream = QueueEventStream(
            output, object(), threading.Event(), cancelled,
            lambda: released.append(True),
        )
        stream.close()
        stream.close()
        self.assertEqual(list(stream), [])
        self.assertTrue(cancelled.is_set())
        self.assertEqual(released, [True])


class SSEHeartbeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.releases = []
        self.apps = []
        self.services = []
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice" / "history").mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "config" / "global_config.json").write_text(json.dumps({
            "web": {"max_concurrent_chats": 1, "max_pending_chats": 2,
                    "pending_chat_timeout": 2, "sse_heartbeat_seconds": 1},
            "memory": {"extraction_mode": "disabled"},
        }), encoding="utf-8")

    async def asyncTearDown(self):
        for release in self.releases:
            release.set()
        for app in self.apps:
            await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
        for service in self.services:
            await wait_until(lambda: not service._active_runs)
        self.temporary.cleanup()

    def app(self, backend):
        app = create_app(root=self.root, service=backend)
        self.apps.append(app)
        return app

    def blocked_iterator(self, *args, **kwargs):
        iterator = BlockingIterator(*args, **kwargs)
        self.releases.append(iterator.release)
        return iterator

    def service(self, source):
        service = WebRunService(self.root, event_source=source)
        service.sse_heartbeat_seconds = lambda user: 0.02
        self.services.append(service)
        return service

    async def test_heartbeats_are_sent_before_unblock_with_one_next_and_exact_payloads(self):
        events = [
            RunEvent(type="text_delta", content="hello", run_sequence=1),
            RunEvent(type="usage", usage={"total_tokens": 3}, run_sequence=2),
            RunEvent(type="done", metadata={"committed": True}, run_sequence=3),
        ]
        iterator = self.blocked_iterator(events)
        app = self.app(Backend(iterator))
        chunks = []
        starts = []
        heartbeat_times = []

        async def send(message):
            if message["type"] == "http.response.start":
                starts.append(message)
            chunk = message.get("body", b"")
            chunks.append(chunk)
            if chunk == HEARTBEAT_FRAME:
                self.assertFalse(iterator.release.is_set())
                self.assertEqual(iterator.calls, 1)
                heartbeat_times.append(time.monotonic())
                if len(heartbeat_times) == 2:
                    iterator.release.set()

        await asyncio.wait_for(invoke_chat(app, send), 3)
        self.assertEqual(len(heartbeat_times), 2)
        self.assertGreaterEqual(heartbeat_times[1] - heartbeat_times[0], 0.01)
        payloads = [
            json.loads(chunk.split(b"\ndata: ", 1)[1])
            for chunk in chunks if chunk.startswith(b"event:")
        ]
        self.assertEqual(payloads, [event.to_dict() for event in events])
        self.assertEqual(iterator.calls, len(events))  # no extra next after done
        self.assertEqual(iterator.close_calls, 1)
        self.assertEqual(starts[0]["status"], 200)
        headers = dict(starts[0]["headers"])
        self.assertEqual(headers[b"x-accel-buffering"], b"no")
        self.assertEqual(headers[b"x-kemo-heartbeat-seconds"], b"0.02")
        self.assertEqual(chunks[-2], next(c for c in chunks if c.startswith(b"event: done")))
        self.assertEqual(chunks[-1], b"")

    async def test_dense_events_stop_at_terminal_without_heartbeat_or_extra_next(self):
        iterator = self.blocked_iterator(
            [RunEvent(type="done"), RunEvent(type="text_delta", content="too late")],
            blocked=False,
        )
        app = self.app(Backend(iterator))
        chunks = []

        async def send(message):
            chunks.append(message.get("body", b""))

        await invoke_chat(app, send)
        self.assertNotIn(HEARTBEAT_FRAME, chunks)
        self.assertEqual(iterator.calls, 1)
        self.assertNotIn(b"too late", b"".join(chunks))

    async def test_invalid_missing_and_exceptional_events_still_have_sanitized_errors(self):
        cases = [
            ([], b"MissingTerminalEvent"),
            (["invalid"], b"InvalidRunEvent"),
        ]
        for values, expected in cases:
            with self.subTest(expected=expected):
                iterator = self.blocked_iterator(values, blocked=False)
                app = self.app(Backend(iterator))
                chunks = []

                async def send(message):
                    chunks.append(message.get("body", b""))

                await invoke_chat(app, send)
                self.assertIn(expected, b"".join(chunks))
                self.assertEqual(iterator.close_calls, 1)

        def failed_source():
            raise RuntimeError("secret-must-not-leak")
            yield

        app = self.app(Backend(failed_source()))
        chunks = []

        async def send_failure(message):
            chunks.append(message.get("body", b""))

        await invoke_chat(app, send_failure)
        self.assertIn(b"WebStreamError", b"".join(chunks))
        self.assertNotIn(b"secret-must-not-leak", b"".join(chunks))

    async def test_disconnect_unblocks_builtin_reader_but_preserves_slow_worker_scope(self):
        for spec in ("2.3", "2.4"):
            with self.subTest(spec=spec):
                release = threading.Event()
                self.releases.append(release)

                def source(*_args, **_kwargs):
                    release.wait(3)  # deliberately non-cooperative provider/tool
                    yield RunEvent(type="done")

                service = self.service(source)
                app = self.app(service)
                disconnected = asyncio.Event()

                async def send(message):
                    if message.get("body") == HEARTBEAT_FRAME:
                        disconnected.set()

                await asyncio.wait_for(
                    invoke_chat(app, send, disconnected=disconnected, spec=spec), 1,
                )
                await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
                self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
                self.assertFalse(release.is_set())
                self.assertIn("run_heartbeat", service._active_runs)
                self.assertTrue(service._active_runs["run_heartbeat"].cancel_event.is_set())
                stopping = service.cancel_run(
                    "alice", "run_heartbeat", session_id="heartbeat",
                )
                self.assertEqual(stopping["status"], "stopping")
                release.set()
                await wait_until(lambda: not service._active_runs)

    async def test_repeated_disconnects_release_slots_and_finish_cooperative_workers(self):
        def source(_request, *, cancel_event, **_kwargs):
            cancel_event.wait(3)
            yield RunEvent(type="done", metadata={"cancelled": True})

        service = self.service(source)
        app = self.app(service)
        for _ in range(5):
            disconnected = asyncio.Event()

            async def send(message):
                if message.get("body") == HEARTBEAT_FRAME:
                    disconnected.set()

            await invoke_chat(app, send, disconnected=disconnected)
            await wait_until(lambda: not service._active_runs)
            self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
            await wait_until(lambda: not app.state.web_stream_cleanup.tasks)

    async def test_explicit_user_cancel_keeps_terminal_event_visible_over_sse(self):
        def source(_request, *, cancel_event, **_kwargs):
            cancel_event.wait(3)
            yield RunEvent(type="done", metadata={"status": "cancelled", "committed": False})

        service = self.service(source)
        app = self.app(service)
        chunks = []
        cancel_sent = False

        async def send(message):
            nonlocal cancel_sent
            chunk = message.get("body", b"")
            chunks.append(chunk)
            if chunk == HEARTBEAT_FRAME and not cancel_sent:
                cancel_sent = True
                service.cancel_run("alice", "run_heartbeat", session_id="heartbeat")

        await asyncio.wait_for(invoke_chat(app, send), 2)
        self.assertTrue(cancel_sent)
        payload = next(chunk for chunk in chunks if chunk.startswith(b"event: done"))
        self.assertEqual(json.loads(payload.split(b"\ndata: ")[1])["metadata"]["status"], "cancelled")

    async def test_header_or_body_send_failure_always_releases_consumer(self):
        for failure_type in ("http.response.start", "http.response.body"):
            with self.subTest(failure_type=failure_type):
                release = threading.Event()
                self.releases.append(release)

                def source(*_args, **_kwargs):
                    release.wait(3)
                    yield RunEvent(type="done")

                service = self.service(source)
                app = self.app(service)

                async def send(message):
                    if message["type"] == failure_type:
                        raise OSError("client socket closed")

                with self.assertRaises(Exception):
                    await invoke_chat(app, send)
                await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
                self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
                release.set()
                await wait_until(lambda: not service._active_runs)

    async def test_direct_task_cancellation_does_not_cancel_pending_thread_or_race_close(self):
        iterator = self.blocked_iterator()
        app = self.app(Backend(iterator))

        async def send(_message):
            pass

        task = asyncio.create_task(invoke_chat(app, send))
        await wait_until(iterator.entered.is_set)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(iterator.reading)
        self.assertEqual(iterator.close_calls, 0)
        self.assertEqual(len(app.state.web_stream_cleanup.tasks), 1)
        iterator.release.set()
        await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
        self.assertEqual(iterator.close_calls, 1)

    async def test_disconnect_during_gate_wait_releases_pending_without_starting_sse(self):
        service = self.service(lambda *_args, **_kwargs: iter([RunEvent(type="done")]))
        app = self.app(service)
        gate = service._get_chat_gate("alice")
        self.assertTrue(gate.acquire())
        disconnected = asyncio.Event()
        sent = []

        async def send(message):
            sent.append(message)

        task = asyncio.create_task(invoke_chat(app, send, disconnected=disconnected))
        try:
            await wait_until(lambda: gate.status()["pending_chats"] == 1)
            disconnected.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
            await wait_until(lambda: gate.status()["pending_chats"] == 0)
            self.assertEqual(gate.status()["active_chats"], 1)  # the slot we own
            self.assertEqual(sent, [])
        finally:
            gate.release()

    async def test_gate_congestion_keeps_http_503_instead_of_early_200(self):
        config_path = self.root / "config" / "global_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["web"]["max_pending_chats"] = 0
        config_path.write_text(json.dumps(config), encoding="utf-8")
        service = self.service(lambda *_args, **_kwargs: iter([RunEvent(type="done")]))
        app = self.app(service)
        gate = service._get_chat_gate("alice")
        self.assertTrue(gate.acquire())
        messages = []

        async def send(message):
            messages.append(message)

        try:
            await invoke_chat(app, send)
            self.assertEqual(messages[0]["status"], 503)
            self.assertNotIn(HEARTBEAT_FRAME, [m.get("body") for m in messages])
        finally:
            gate.release()

    async def test_effective_config_and_provenance_follow_global_then_user(self):
        service = WebRunService(self.root, event_source=lambda *_a, **_kw: iter([RunEvent(type="done")]))
        self.services.append(service)
        self.assertEqual(service.sse_heartbeat_seconds("alice"), 1)
        service.patch_user_config("alice", {"web": {"sse_heartbeat_seconds": 2.5}})
        self.assertEqual(service.sse_heartbeat_seconds("alice"), 2.5)
        self.assertEqual(service._config_provenance("alice")["web.sse_heartbeat_seconds"], "user")
        messages = []

        async def send(message):
            messages.append(message)

        await invoke_chat(self.app(service), send)
        self.assertEqual(dict(messages[0]["headers"])[b"x-kemo-heartbeat-seconds"], b"2.5")
        for value, expected in ((True, 15), ("bad", 15), (0.00001, 1), (999, 30)):
            # Exercise the actual atomic settings write/cache invalidation,
            # not rapid same-size direct rewrites with Windows timestamp ties.
            service.patch_user_config("alice", {"web": {"sse_heartbeat_seconds": value}})
            self.assertEqual(service.sse_heartbeat_seconds("alice"), expected)

    async def test_authenticated_stream_preserves_heartbeats_and_disconnect_signal(self):
        release = threading.Event()
        self.releases.append(release)

        def source(*_args, **_kwargs):
            release.wait(3)
            yield RunEvent(type="done")

        service = self.service(source)
        app = create_app(
            root=self.root, service=service,
            auth_config=WebAuthConfig(access_token="local-test-only"),
        )
        self.apps.append(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post("/api/auth/token", json={"token": "local-test-only"})
            self.assertEqual(response.status_code, 200)
            cookie = "; ".join(f"{key}={value}" for key, value in client.cookies.items())
        disconnected = asyncio.Event()
        starts = []

        async def send(message):
            if message["type"] == "http.response.start":
                starts.append(message["status"])
            if message.get("body") == HEARTBEAT_FRAME:
                disconnected.set()

        await asyncio.wait_for(invoke_chat(
            app, send, disconnected=disconnected, headers=[(b"cookie", cookie.encode())],
        ), 1)
        self.assertEqual(starts, [200])
        await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
        self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)

    async def test_late_preparation_result_is_closed_without_ever_calling_next(self):
        iterator = self.blocked_iterator(blocked=False)
        prepare_release = threading.Event()
        prepare_entered = threading.Event()
        self.releases.append(prepare_release)

        class SlowBackend(Backend):
            def stream_chat(self, *args, **kwargs):
                prepare_entered.set()
                prepare_release.wait(3)
                return super().stream_chat(*args, **kwargs)

        app = self.app(SlowBackend(iterator))

        async def send(_message):
            pass

        task = asyncio.create_task(invoke_chat(app, send))
        await wait_until(prepare_entered.is_set)
        task.cancel()
        if sys.version_info >= (3, 11):
            with self.assertRaises(asyncio.CancelledError):
                await task
        else:
            # CPython 3.10 没有 Task.uncancel()，anyio 的 asyncio 后端无法把外部
            # 取消与本 scope 的取消区分开，取消在中间件的 task group 处被忽略，
            # 请求会继续跑到结束。真实客户端断开走的是 is_disconnected() 探测，
            # 该路径有独立用例覆盖；这里不把「取消抛回调用方」作为 3.10 的断言。
            try:
                await task
            except asyncio.CancelledError:
                pass
        prepare_release.set()
        await wait_until(lambda: not app.state.web_stream_cleanup.tasks)
        if sys.version_info >= (3, 11):
            self.assertEqual(iterator.calls, 0)
        self.assertEqual(iterator.close_calls, 1)

class CleanupOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_works_when_default_executor_is_full_of_blocked_reads(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        entered = threading.Event()
        released = []

        class ObservedStream(QueueEventStream):
            def __next__(self):
                entered.set()
                return super().__next__()

        stream = ObservedStream(
            queue.Queue(), object(), threading.Event(), threading.Event(),
            lambda: released.append(True),
        )
        pending = asyncio.create_task(asyncio.to_thread(next, stream, None))
        cleanup = StreamCleanup()
        try:
            await wait_until(entered.is_set)
            await asyncio.wait_for(cleanup.close(stream, pending), 0.8)
            await wait_until(lambda: not cleanup.tasks, timeout=0.5)
            self.assertEqual(released, [True])
            self.assertTrue(pending.done())
        finally:
            stream.close()
            await asyncio.gather(pending, return_exceptions=True)
            await wait_until(lambda: not cleanup.tasks)

    async def test_closed_consumer_cancels_queued_read_without_needing_executor_capacity(self):
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        entered = threading.Event()
        release = threading.Event()
        calls = []
        released = []

        def occupy_executor():
            entered.set()
            release.wait(3)

        class ObservedStream(QueueEventStream):
            def __next__(self):
                calls.append(True)
                return super().__next__()

        stream = ObservedStream(
            queue.Queue(), object(), threading.Event(), threading.Event(),
            lambda: released.append(True),
        )
        blocker = asyncio.create_task(asyncio.to_thread(occupy_executor))
        pending = None
        cleanup = StreamCleanup()
        try:
            await wait_until(entered.is_set)
            pending = asyncio.create_task(asyncio.to_thread(next, stream, None))
            await asyncio.wait_for(cleanup.close(stream, pending), 0.8)
            await wait_until(lambda: not cleanup.tasks, timeout=0.5)
            self.assertEqual(released, [True])
            self.assertEqual(calls, [])
            self.assertTrue(pending.cancelled())
        finally:
            stream.close()
            release.set()
            await blocker
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await wait_until(lambda: not cleanup.tasks)

    async def test_scope_cancellation_does_not_abandon_close(self):
        import anyio

        iterator = BlockingIterator(blocked=False)
        cleanup = StreamCleanup()
        with anyio.CancelScope() as scope:
            scope.cancel()
            await cleanup.close(iterator)
        await wait_until(lambda: not cleanup.tasks)
        self.assertEqual(iterator.close_calls, 1)
