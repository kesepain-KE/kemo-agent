from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from events import RunEvent
from message.identity import (
    IdentityBinding,
    IdentityError,
    IdentityResolver,
    filter_tool_registry,
)
from message.router import MessageRouteError, MessageRouter
from message.schema import MessageContractError, MessageEnvelope, OutboundMessage
from message.state import ProcessedMessageStore
from message.transport import (
    MockTransport,
    TransportPolicy,
    TransportRegistrationError,
    TransportRegistry,
)
from run.runtime_host import RuntimeHost
from run.cron_store import CronStore
from run.tools import ToolDefinition, ToolRegistry


def _root(*users: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "config").mkdir()
    for user in users:
        (root / "users" / user).mkdir(parents=True)
        (root / "users" / user / "user_config.json").write_text("{}", "utf-8")
    return temporary, root


def _config(bindings: list[dict] | None = None, *, cron: bool = False) -> dict:
    return {
        "schema_version": 1,
        "message": {"max_workers": 4},
        "runtime_host": {"enable_background_scheduler": cron},
        "cron": {"enabled": cron, "poll_interval": 1},
    }


def _envelope(
    message_id: str = "m1",
    *,
    platform: str = "mock",
    user_id: str = "ext-1",
    chat_type: str = "private",
    chat_id: str = "chat-1",
    text: str = "hello",
) -> MessageEnvelope:
    return MessageEnvelope(
        message_id=message_id,
        platform=platform,
        chat_type=chat_type,
        external_user_id=user_id,
        external_chat_id=chat_id,
        text=text,
        timestamp="2026-07-18T00:00:00+08:00",
    )


def _done_events(request, **kwargs):
    yield RunEvent(type="text_delta", content=f"reply:{request['prompt']}")
    yield RunEvent(type="done", metadata={"text": f"reply:{request['prompt']}"})


class SchemaTests(unittest.TestCase):
    def test_envelope_normalizes_and_serializes(self) -> None:
        item = _envelope()
        self.assertEqual(item.platform, "mock")
        self.assertEqual(item.dedupe_key, "mock:m1")
        self.assertEqual(MessageEnvelope.from_dict(item.to_dict()), item)

    def test_invalid_contract_rejected(self) -> None:
        with self.assertRaises(MessageContractError):
            _envelope(text="")
        with self.assertRaises(MessageContractError):
            _envelope(chat_type="unknown")
        with self.assertRaises(MessageContractError):
            MessageEnvelope(
                message_id="m", platform="mock", chat_type="private",
                external_user_id="u", external_chat_id="c", text="x",
                timestamp="2026-07-18T00:00:00",
            )

    def test_outbound_reply(self) -> None:
        inbound = _envelope()
        outbound = OutboundMessage.reply(inbound, "ok")
        self.assertEqual(outbound.platform, "mock")
        self.assertEqual(outbound.external_chat_id, "chat-1")
        self.assertEqual(outbound.reply_to, "m1")


class RegistryAndPermissionTests(unittest.TestCase):
    def test_duplicate_registration_rejected(self) -> None:
        registry = TransportRegistry()
        registry.register(MockTransport())
        with self.assertRaises(TransportRegistrationError):
            registry.register(MockTransport())

    def test_unsupported_capability_rejected(self) -> None:
        registry = TransportRegistry()
        with self.assertRaises(TransportRegistrationError):
            registry.register(
                MockTransport(),
                TransportPolicy(capabilities=frozenset({"receive_file"})),
            )

    def test_tool_permission_intersection(self) -> None:
        def tool(name: str, enabled: bool = True) -> ToolDefinition:
            return ToolDefinition(
                name=name, description=name, input_schema={"type": "object"},
                version="1", enabled=enabled, entrypoint="x.py:x",
                source="test", directory=Path("."),
            )

        registry = ToolRegistry({"a": tool("a"), "b": tool("b"), "c": tool("c", False)})
        self.assertIs(filter_tool_registry(registry, None), registry)
        self.assertEqual(set(filter_tool_registry(registry, frozenset()).tools), set())
        self.assertEqual(
            set(filter_tool_registry(registry, frozenset({"a", "c"})).tools),
            {"a"},
        )


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = _root("alice", "bob")
        self.addCleanup(self.temporary.cleanup)

    def test_resolve_and_specific_binding_wins(self) -> None:
        resolver = IdentityResolver(
            self.root,
            [
                IdentityBinding("mock", "ext-1", "alice"),
                IdentityBinding("mock", "ext-1", "bob", "group", "group-1"),
            ],
        )
        self.assertEqual(resolver.resolve(_envelope()), "alice")
        self.assertEqual(
            resolver.resolve(_envelope(chat_type="group", chat_id="group-1")),
            "bob",
        )

    def test_unbound_and_conflict_rejected(self) -> None:
        resolver = IdentityResolver(self.root, [])
        with self.assertRaises(IdentityError):
            resolver.resolve(_envelope())
        conflict = IdentityResolver(
            self.root,
            [
                IdentityBinding("mock", "ext-1", "alice"),
                IdentityBinding("mock", "ext-1", "bob"),
            ],
        )
        with self.assertRaises(IdentityError):
            conflict.resolve(_envelope())


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = _root("alice")
        self.addCleanup(self.temporary.cleanup)

    def test_claim_complete_and_duplicate(self) -> None:
        store = ProcessedMessageStore(self.root, "alice")
        self.assertTrue(store.claim("mock:m1"))
        self.assertFalse(store.claim("mock:m1"))
        store.complete("mock:m1", status="completed")
        self.assertEqual(store.get("mock:m1")["status"], "completed")

    def test_recover_processing_without_replay(self) -> None:
        store = ProcessedMessageStore(self.root, "alice")
        store.claim("mock:m1")
        self.assertEqual(store.recover_interrupted(), ["mock:m1"])
        record = store.get("mock:m1")
        self.assertEqual(record["status"], "failed")
        self.assertFalse(store.claim("mock:m1"))

    def test_trim(self) -> None:
        store = ProcessedMessageStore(self.root, "alice", max_entries=2)
        for index in range(3):
            key = f"mock:m{index}"
            store.claim(key)
            store.complete(key, status="completed")
        data = json.loads(store.path.read_text("utf-8"))
        self.assertEqual(len(data["messages"]), 2)


class RouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = _root("alice", "bob")
        self.addCleanup(self.temporary.cleanup)
        self.registry = TransportRegistry()
        self.transport = MockTransport()
        self.registry.register(self.transport, TransportPolicy(allowed_tools=frozenset()))
        self.transport.start(lambda envelope: None, lambda component, exc: None)
        self.resolver = IdentityResolver(
            self.root,
            [
                IdentityBinding("mock", "ext-1", "alice"),
                IdentityBinding("mock", "ext-2", "bob"),
            ],
        )

    def _router(self, event_source=_done_events, **kwargs) -> MessageRouter:
        return MessageRouter(
            self.root, self.resolver, self.registry,
            event_source=event_source,
            tool_registry_factory=lambda root, user: ToolRegistry({}),
            **kwargs,
        )

    def test_route_success_and_duplicate(self) -> None:
        router = self._router()
        first = router.route(_envelope())
        self.assertEqual(first.status, "completed")
        self.assertEqual(first.text, "reply:hello")
        self.assertEqual(first.source, "message:mock")
        self.assertEqual(first.session_id, "private:chat-1")
        self.assertEqual(len(self.transport.sent), 1)
        duplicate = router.route(_envelope())
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(len(self.transport.sent), 1)

    def test_session_isolation(self) -> None:
        seen: list[tuple[str, str, str]] = []

        def source(request, **kwargs):
            seen.append((request["user"], request["source"], request["session_id"]))
            yield RunEvent(type="text_delta", content="ok")
            yield RunEvent(type="done", metadata={"text": "ok"})

        router = self._router(source)
        router.route(_envelope("m1", chat_type="private", chat_id="u1"))
        router.route(_envelope("m2", chat_type="group", chat_id="g1"))
        self.assertEqual(
            seen,
            [
                ("alice", "message:mock", "private:u1"),
                ("alice", "message:mock", "group:g1"),
            ],
        )

    def test_run_error_isolated(self) -> None:
        def failed(request, **kwargs):
            yield RunEvent(type="error", error={"message": "boom"})

        router = self._router(failed)
        result = router.route(_envelope())
        self.assertEqual(result.status, "failed")
        self.assertIn("boom", result.error["message"])
        self.assertEqual(len(self.transport.sent), 0)

    def test_missing_terminal_is_failure(self) -> None:
        def incomplete(request, **kwargs):
            yield RunEvent(type="text_delta", content="partial")

        result = self._router(incomplete).route(_envelope())
        self.assertEqual(result.status, "failed")

    def test_submit_requires_start(self) -> None:
        router = self._router()
        with self.assertRaises(MessageRouteError):
            router.submit(_envelope())

    def test_same_session_serial_cross_session_parallel(self) -> None:
        active = 0
        max_active = 0
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def source(request, **kwargs):
            nonlocal active, max_active
            if request["session_id"].endswith("parallel-1"):
                barrier.wait(timeout=2)
            elif request["session_id"].endswith("parallel-2"):
                barrier.wait(timeout=2)
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with lock:
                active -= 1
            yield RunEvent(type="text_delta", content="ok")
            yield RunEvent(type="done", metadata={"text": "ok"})

        router = self._router(source, max_workers=4)
        router.start()
        try:
            futures = [
                router.submit(_envelope("m1", chat_id="parallel-1")),
                router.submit(_envelope("m2", chat_id="parallel-2")),
            ]
            for future in futures:
                self.assertEqual(future.result(timeout=3).status, "completed")
            self.assertGreaterEqual(max_active, 2)
        finally:
            router.stop()

        # Same session: use a fresh router/state IDs and verify max concurrency 1.
        active = 0
        max_active = 0
        same_router = self._router(source, max_workers=4)
        same_router.start()
        try:
            futures = [
                same_router.submit(_envelope("m3", chat_id="same")),
                same_router.submit(_envelope("m4", chat_id="same")),
            ]
            for future in futures:
                self.assertEqual(future.result(timeout=3).status, "completed")
            self.assertEqual(max_active, 1)
        finally:
            same_router.stop()


class FakeCron:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.running = False
        self.fail_start = fail_start
        self.started = 0
        self.stopped = 0

    def start(self) -> None:
        self.started += 1
        if self.fail_start:
            raise RuntimeError("cron start failed")
        self.running = True

    def stop(self, *, timeout: float = 10) -> None:
        self.stopped += 1
        self.running = False


class FakeMaintenance(FakeCron):
    pass


class HostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = _root("alice")
        self.addCleanup(self.temporary.cleanup)

    def _host(self, transport=None, *, cron=True, fake_cron=None) -> RuntimeHost:
        registry = TransportRegistry()
        registry.register(transport or MockTransport())
        config = _config(
            [{"platform": "mock", "external_user_id": "ext-1", "internal_user": "alice"}],
            cron=cron,
        )
        (self.root / "config" / "global_config.json").write_text(
            json.dumps(config),
            "utf-8",
        )
        (self.root / "users" / "alice" / "user_config.json").write_text(
            json.dumps({"schema_version": 1}),
            "utf-8",
        )
        return RuntimeHost(
            self.root,
            config=config,
            message_config={
                "bindings": [
                    {
                        "platform": "mock",
                        "external_user_id": "ext-1",
                        "internal_user": "alice",
                    }
                ],
                "transports": {},
            },
            registry=registry,
            cron_scheduler=fake_cron or FakeCron(),
            maintenance_scheduler=FakeMaintenance(),
            provider_factory=lambda cfg: None,
            tool_registry_factory=lambda root, user: ToolRegistry({}),
            router=MessageRouter(
                self.root,
                IdentityResolver.from_config(
                    self.root,
                    {
                        "bindings": [
                            {
                                "platform": "mock",
                                "external_user_id": "ext-1",
                                "internal_user": "alice",
                            }
                        ]
                    },
                ),
                registry,
                event_source=_done_events,
                tool_registry_factory=lambda root, user: ToolRegistry({}),
            ),
        )

    def test_start_stop_idempotent_and_cron_managed(self) -> None:
        cron = FakeCron()
        host = self._host(fake_cron=cron)
        host.start()
        host.start()
        self.assertTrue(host.running)
        self.assertEqual(cron.started, 1)
        self.assertEqual(host.maintenance.started, 1)
        self.assertEqual(
            len(CronStore(self.root, "alice").list_tasks()),
            3,
        )
        host.stop()
        host.stop()
        self.assertEqual(host.state, "stopped")
        self.assertEqual(cron.stopped, 1)
        self.assertEqual(host.maintenance.stopped, 1)

    def test_background_switch_disables_cron_and_maintenance(self) -> None:
        cron = FakeCron()
        host = self._host(cron=False, fake_cron=cron)
        host.start()
        try:
            self.assertEqual(cron.started, 0)
            self.assertEqual(host.maintenance.started, 0)
            self.assertEqual(host.status()["components"]["background"]["state"], "stopped")
        finally:
            host.stop()

    def test_transport_start_failure_isolated(self) -> None:
        transport = MockTransport(fail_start=True)
        cron = FakeCron()
        host = self._host(transport, fake_cron=cron)
        host.start()
        self.assertTrue(host.running)
        self.assertTrue(cron.running)
        self.assertEqual(
            host.status()["components"]["transport:mock"]["state"], "failed"
        )
        host.stop()

    def test_transport_message_round_trip(self) -> None:
        transport = MockTransport()
        host = self._host(transport, cron=False, fake_cron=FakeCron())
        host.start()
        try:
            transport.emit(_envelope())
            deadline = time.time() + 2
            while not transport.sent and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(transport.sent[0].text, "reply:hello")
        finally:
            host.stop()

    def test_cron_start_failure_fails_host_and_stops_router(self) -> None:
        host = self._host(fake_cron=FakeCron(fail_start=True))
        with self.assertRaises(RuntimeError):
            host.start()
        self.assertEqual(host.state, "failed")
        self.assertFalse(host.router.running)


if __name__ == "__main__":
    unittest.main()
