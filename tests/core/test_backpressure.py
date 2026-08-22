from __future__ import annotations

import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from cron.scheduler import CronScheduler
from events import RunEvent
from message.identity import IdentityBinding, IdentityResolver
from message.router import MessageQueueFullError, MessageRouter
from message.schema import MessageEnvelope
from message.transport import MockTransport, TransportPolicy, TransportRegistry
import provider.factory as provider_factory_module
from provider.factory import (
    ProviderCongestionError,
    get_provider_semaphore,
    provider_request_slot,
    provider_semaphore_status,
)
from run.agents import AgentQueueError, AgentScheduler
from run.agents import AgentRunResult
from run.scheduler import CronStore, normalize_task
from run.tools import ToolRegistry
from web.app import create_app
from web.service import TooManyChatsError, WebRunService, _UserChatGate


def _wait_for(predicate, *, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class ProviderBackpressureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._reset_provider_gate()
        self.addCleanup(self._reset_provider_gate)

    @staticmethod
    def _reset_provider_gate() -> None:
        with provider_factory_module._provider_semaphore_lock:
            provider_factory_module._provider_semaphore = None
            provider_factory_module._provider_semaphore_limit = 0
            provider_factory_module._provider_waiting = 0

    def test_provider_gate_caps_concurrency_reports_waiters_and_reconfigures_idle(self) -> None:
        config = {
            "provider_runtime": {
                "max_concurrent_requests": 2,
                "request_semaphore_timeout": 2,
            }
        }
        release = threading.Event()
        lock = threading.Lock()
        active = 0
        max_active = 0
        errors: list[BaseException] = []

        def work() -> None:
            nonlocal active, max_active
            try:
                with provider_request_slot(config):
                    with lock:
                        active += 1
                        max_active = max(max_active, active)
                    release.wait(2)
                    with lock:
                        active -= 1
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        workers = [threading.Thread(target=work) for _ in range(3)]
        for worker in workers:
            worker.start()
        try:
            self.assertTrue(
                _wait_for(
                    lambda: provider_semaphore_status()["active_requests"] == 2
                    and provider_semaphore_status()["waiting_estimate"] == 1
                )
            )
            status = provider_semaphore_status()
            self.assertEqual(status["available_requests"], 0)
            self.assertEqual(status["max_requests"], 2)
            self.assertEqual(max_active, 2)
        finally:
            release.set()
            for worker in workers:
                worker.join(timeout=3)

        self.assertFalse(errors)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(provider_semaphore_status()["active_requests"], 0)
        get_provider_semaphore(3)
        self.assertEqual(provider_semaphore_status()["max_requests"], 3)
        self.assertEqual(provider_semaphore_status()["available_requests"], 3)

    def test_provider_gate_timeout_and_cancellation_do_not_leak_waiters(self) -> None:
        held = {
            "provider_runtime": {
                "max_concurrent_requests": 1,
                "request_semaphore_timeout": 1,
            }
        }
        timeout_errors: list[BaseException] = []
        with provider_request_slot(held):
            timeout_worker = threading.Thread(
                target=lambda: self._capture_slot_error(held, None, timeout_errors)
            )
            timeout_worker.start()
            timeout_worker.join(timeout=2)
            self.assertFalse(timeout_worker.is_alive())
            self.assertIsInstance(timeout_errors[0], ProviderCongestionError)
            self.assertIn("等待 1s 超时", str(timeout_errors[0]))

            cancelled = threading.Event()
            cancel_errors: list[BaseException] = []
            cancel_worker = threading.Thread(
                target=lambda: self._capture_slot_error(held, cancelled, cancel_errors)
            )
            cancel_worker.start()
            self.assertTrue(
                _wait_for(
                    lambda: provider_semaphore_status()["waiting_estimate"] == 1
                )
            )
            cancelled.set()
            cancel_worker.join(timeout=1)
            self.assertFalse(cancel_worker.is_alive())
            self.assertIsInstance(cancel_errors[0], ProviderCongestionError)
            self.assertIn("已取消", str(cancel_errors[0]))

        self.assertEqual(provider_semaphore_status()["waiting_estimate"], 0)
        self.assertEqual(provider_semaphore_status()["active_requests"], 0)

    def test_status_with_config_initializes_the_idle_limit(self) -> None:
        status = provider_semaphore_status(
            {"provider_runtime": {"max_concurrent_requests": 7}}
        )
        self.assertEqual(status["active_requests"], 0)
        self.assertEqual(status["max_requests"], 7)
        self.assertEqual(status["available_requests"], 7)

    @staticmethod
    def _capture_slot_error(
        config: dict,
        cancel_event: threading.Event | None,
        errors: list[BaseException],
    ) -> None:
        try:
            with provider_request_slot(config, cancel_event=cancel_event):
                pass
        except BaseException as exc:
            errors.append(exc)


class WebBackpressureTests(unittest.TestCase):
    def make_root(
        self,
        *,
        max_concurrent: int = 1,
        max_pending: int = 0,
        pending_timeout: float = 1,
    ) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir(parents=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "web": {
                        "max_concurrent_chats": max_concurrent,
                        "max_pending_chats": max_pending,
                        "pending_chat_timeout": pending_timeout,
                    },
                    "provider_runtime": {"max_concurrent_requests": 7},
                    "message": {"max_queued_messages": 11},
                    "agent_runtime": {"queue_maxsize": 13},
                }
            ),
            "utf-8",
        )
        for user in ("alice", "bob"):
            directory = root / "users" / user
            (directory / "history").mkdir(parents=True)
            (directory / "user_config.json").write_text("{}", "utf-8")
        return root

    def test_user_gate_has_bounded_waiting_and_releases_all_counts(self) -> None:
        gate = _UserChatGate(1, 1, 1)
        self.assertTrue(gate.acquire())
        acquired = threading.Event()
        release_second = threading.Event()

        def wait_for_gate() -> None:
            if gate.acquire():
                acquired.set()
                release_second.wait(1)
                gate.release()

        worker = threading.Thread(target=wait_for_gate)
        worker.start()
        self.assertTrue(_wait_for(lambda: gate.status()["pending_chats"] == 1))
        started = time.monotonic()
        self.assertFalse(gate.acquire())
        self.assertLess(time.monotonic() - started, 0.2)
        gate.release()
        self.assertTrue(acquired.wait(1))
        release_second.set()
        worker.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(
            gate.status(),
            {"active_chats": 0, "max_chats": 1, "pending_chats": 0, "max_pending": 1},
        )

    def test_chat_limits_are_user_scoped_and_503_has_retry_after(self) -> None:
        root = self.make_root(max_concurrent=1, max_pending=0, pending_timeout=2)
        releases = {"alice": threading.Event(), "bob": threading.Event()}
        started = {"alice": threading.Event(), "bob": threading.Event()}

        def source(request, **_kwargs):
            user = request["user"]
            started[user].set()
            releases[user].wait(3)
            yield RunEvent(type="done")

        service = WebRunService(root, event_source=source)
        consumers: list[threading.Thread] = []
        try:
            for user in ("alice", "bob"):
                iterator = service.stream_chat(
                    user,
                    f"{user}-session",
                    "hello",
                    cancel_event=threading.Event(),
                )
                consumer = threading.Thread(target=lambda stream=iterator: list(stream))
                consumer.start()
                consumers.append(consumer)
                self.assertTrue(started[user].wait(1))

            self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 1)
            self.assertEqual(service.chat_gate_status()["bob"]["active_chats"], 1)
            with self.assertRaises(TooManyChatsError):
                service.stream_chat(
                    "alice",
                    "second-direct",
                    "busy",
                    cancel_event=threading.Event(),
                )

            async def request_busy_chat() -> httpx.Response:
                transport = httpx.ASGITransport(
                    app=create_app(service=service),
                    raise_app_exceptions=False,
                )
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                ) as client:
                    return await client.post(
                        "/api/chat",
                        json={
                            "user": "alice",
                            "session_id": "second-http",
                            "prompt": "busy",
                        },
                    )

            response = asyncio.run(request_busy_chat())
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(response.json()["error"]["code"], "too_many_chats")
            self.assertEqual(response.headers["retry-after"], "2")

            limits = service.settings("alice")["limits"]
            self.assertEqual(limits["provider_max_concurrent"], 7)
            self.assertEqual(limits["web_max_chats"], 1)
            self.assertEqual(limits["message_max_queued"], 11)
            self.assertEqual(limits["agent_queue_maxsize"], 13)
        finally:
            for release in releases.values():
                release.set()
            for consumer in consumers:
                consumer.join(timeout=2)

        self.assertTrue(all(not consumer.is_alive() for consumer in consumers))
        self.assertEqual(service.chat_gate_status()["alice"]["active_chats"], 0)
        self.assertEqual(service.chat_gate_status()["bob"]["active_chats"], 0)

    def test_cancelled_gate_wait_does_not_consume_a_slot(self) -> None:
        gate = _UserChatGate(1, 1, 2)
        self.assertTrue(gate.acquire())
        cancel = threading.Event()
        acquired: list[bool] = []
        worker = threading.Thread(
            target=lambda: acquired.append(gate.acquire(cancel_event=cancel))
        )
        worker.start()
        self.assertTrue(_wait_for(lambda: gate.status()["pending_chats"] == 1))
        cancel.set()
        worker.join(timeout=1)
        gate.release()
        self.assertEqual(acquired, [False])
        self.assertEqual(gate.status()["active_chats"], 0)
        self.assertEqual(gate.status()["pending_chats"], 0)

    def test_idle_web_gate_picks_up_saved_global_limits(self) -> None:
        root = self.make_root(max_concurrent=1, max_pending=0, pending_timeout=2)
        service = WebRunService(root)
        self.assertEqual(service._get_chat_gate("alice").status()["max_chats"], 1)

        service.patch_global_config(
            {
                "web": {
                    "max_concurrent_chats": 4,
                    "max_pending_chats": 6,
                    "pending_chat_timeout": 8,
                }
            }
        )
        status = service._get_chat_gate("alice").status()
        self.assertEqual(status["max_chats"], 4)
        self.assertEqual(status["max_pending"], 6)


class MessageRouterBackpressureTests(unittest.TestCase):
    def test_worker_plus_queue_capacity_rejects_third_and_stop_recovers_capacity(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice").mkdir(parents=True)
        (root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        registry = TransportRegistry()
        transport = MockTransport()
        registry.register(transport, TransportPolicy(allowed_tools=frozenset()))
        transport.start(lambda _envelope: None, lambda _component, _exc: None)
        resolver = IdentityResolver(
            root,
            [IdentityBinding("mock", "external", "alice")],
        )
        started = threading.Event()
        release = threading.Event()

        def source(_request, **_kwargs):
            started.set()
            release.wait(2)
            yield RunEvent(type="done", metadata={"text": "ok"})

        router = MessageRouter(
            root,
            resolver,
            registry,
            max_workers=1,
            max_queued_messages=1,
            event_source=source,
            tool_registry_factory=lambda _root, _user: ToolRegistry({}),
        )
        router.start()
        first = router.submit(self._envelope("first"))
        try:
            self.assertTrue(
                started.wait(5),
                "message router worker did not start within 5 seconds",
            )
            second = router.submit(self._envelope("second"))
            self.assertEqual(router.queue_status()["queued_messages"], 1)
            with self.assertRaises(MessageQueueFullError):
                router.submit(self._envelope("third"))

            router.stop(wait=False)
            self.assertTrue(_wait_for(lambda: second.cancelled()))
            self.assertEqual(router.queue_status()["queued_messages"], 0)
        finally:
            release.set()
            try:
                first.result(timeout=5)
            finally:
                router.stop()
        self.assertTrue(
            _wait_for(lambda: router.queue_status()["active_workers"] == 0)
        )
        self.assertEqual(router.queue_status()["max_queued"], 1)

    @staticmethod
    def _envelope(message_id: str) -> MessageEnvelope:
        return MessageEnvelope(
            message_id=message_id,
            platform="mock",
            chat_type="private",
            external_user_id="external",
            external_chat_id=f"chat-{message_id}",
            text="hello",
            timestamp="2026-07-22T00:00:00+08:00",
        )


class _FakeAgentRegistry:
    def get(self, _name: str) -> SimpleNamespace:
        return SimpleNamespace(execution="background_serial")


class _FakeAgentRunner:
    def __init__(
        self,
        *,
        barrier: threading.Barrier | None = None,
        started: threading.Event | None = None,
        release: threading.Event | None = None,
    ) -> None:
        self.registry = _FakeAgentRegistry()
        self.config = {"agent_runtime": {"queue_maxsize": 1}}
        self.barrier = barrier
        self.started = started
        self.release = release

    def refresh_registry(self) -> _FakeAgentRegistry:
        return self.registry

    def run(self, name: str, input_data: dict, **_kwargs) -> AgentRunResult:
        if self.started is not None:
            self.started.set()
        if self.barrier is not None:
            self.barrier.wait(timeout=1)
        if self.release is not None:
            self.release.wait(2)
        return AgentRunResult(
            agent=name,
            data=dict(input_data),
            raw_text="{}",
            usage={},
            model="fake",
        )


class AgentSchedulerBackpressureTests(unittest.TestCase):
    def test_scheduler_lock_is_instance_scoped(self) -> None:
        barrier = threading.Barrier(2)
        first = AgentScheduler(_FakeAgentRunner(barrier=barrier), maxsize=1)
        second = AgentScheduler(_FakeAgentRunner(barrier=barrier), maxsize=1)
        try:
            first_id = first.submit("agent", {"owner": "alice"})
            second_id = second.submit("agent", {"owner": "bob"})
            self.assertEqual(first.wait(first_id, 2).data["owner"], "alice")
            self.assertEqual(second.wait(second_id, 2).data["owner"], "bob")
        finally:
            first.close(wait=True, cancel_pending=True)
            second.close(wait=True, cancel_pending=True)

    def test_queue_full_is_immediate_and_nonwaiting_close_does_not_block(self) -> None:
        started = threading.Event()
        release = threading.Event()
        scheduler = AgentScheduler(
            _FakeAgentRunner(started=started, release=release),
            maxsize=1,
        )
        try:
            scheduler.submit("agent", {"value": 1})
            self.assertTrue(started.wait(1))
            scheduler.submit("agent", {"value": 2})
            before_reject = time.monotonic()
            with self.assertRaises(AgentQueueError):
                scheduler.submit("agent", {"value": 3})
            self.assertLess(time.monotonic() - before_reject, 0.2)

            before_close = time.monotonic()
            scheduler.close(wait=False, cancel_pending=True)
            self.assertLess(time.monotonic() - before_close, 0.2)
        finally:
            release.set()
            scheduler.close(wait=True, cancel_pending=True)


class CronBackpressureTests(unittest.TestCase):
    def test_congestion_threshold_and_disable_switch(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        scheduler = CronScheduler(
            root,
            config={
                "cron": {
                    "avoid_congestion": True,
                    "congestion_threshold_ratio": 0.2,
                }
            },
        )
        with patch(
            "cron.scheduler.provider_semaphore_status",
            return_value={"max_requests": 10, "available_requests": 1},
        ):
            self.assertTrue(scheduler._should_backoff())
        with patch(
            "cron.scheduler.provider_semaphore_status",
            return_value={"max_requests": 10, "available_requests": 2},
        ):
            self.assertFalse(scheduler._should_backoff())

        disabled = CronScheduler(
            root,
            config={"cron": {"avoid_congestion": False}},
        )
        with patch(
            "cron.scheduler.provider_semaphore_status",
            return_value={"max_requests": 10, "available_requests": 0},
        ):
            self.assertFalse(disabled._should_backoff())

    def test_busy_provider_skips_user_cron_but_not_global_collection(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "users" / "alice").mkdir(parents=True)
        past = "2026-01-01T00:00:00+08:00"
        user_store = CronStore(root, "alice")
        user_task = user_store.create(
            normalize_task(
                title="user task",
                prompt="run",
                user="alice",
                type="recurring",
                interval_seconds=60,
                next_run_at=past,
            )
        )
        system_store = CronStore(root, "__system__", system=True)
        system_store.create(
            normalize_task(
                task_id="perception_update",
                title="sense",
                prompt="",
                user="",
                type="recurring",
                interval_seconds=5,
                next_run_at=past,
                exec_mode="system",
                action="perception_update",
            )
        )
        scheduler = CronScheduler(
            root,
            config={
                "cron": {
                    "avoid_congestion": True,
                    "congestion_threshold_ratio": 0.2,
                }
            },
        )
        with (
            patch(
                "cron.scheduler.provider_semaphore_status",
                return_value={"max_requests": 10, "available_requests": 0},
            ),
            patch(
                "cron.scheduler.execute_cron_task",
                return_value={"status": "completed"},
            ) as execute,
        ):
            self.assertEqual(scheduler.scan_once(), 1)

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(execute.call_args.kwargs["user"], "__system__")
        self.assertEqual(execute.call_args.kwargs["task_id"], "perception_update")
        self.assertEqual(user_store.read(user_task["task_id"])["next_run_at"], past)


if __name__ == "__main__":
    unittest.main()
