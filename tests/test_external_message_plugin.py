from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from events import RunEvent
from message.router import MessageRouter
from message.schema import MessageEnvelope, OutboundMessage
from message.transport import (
    TransportPolicy,
    TransportRegistrationError,
    TransportRegistry,
)
from plugins.external_message.tool import run as external_message
from plugins.manifest import parse_plugin_manifest
from run.tools import ToolRegistry
from web.service import WebRunService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class CapturingTransport:
    name = "demo"
    capabilities = frozenset(
        {"receive_text", "send_text", "receive_file", "send_file"}
    )

    def __init__(self) -> None:
        self.sent: list[OutboundMessage] = []
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, on_message, on_error) -> None:
        del on_message, on_error
        self._running = True

    def send(self, message: OutboundMessage) -> None:
        if not self._running:
            raise RuntimeError("transport stopped")
        self.sent.append(message)

    def stop(self) -> None:
        self._running = False


class ExternalMessageToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = TransportRegistry()
        self.transport = CapturingTransport()
        self.registered = self.registry.register(
            self.transport,
            TransportPolicy(
                capabilities=self.transport.capabilities,
                bound_user="alice",
            ),
        )
        self.transport.start(lambda envelope: None, lambda component, exc: None)
        self.registered.state = "running"
        self.context = {
            "root": str(self.root),
            "user": "alice",
            "transport_registry": self.registry,
        }

    def test_send_message_uses_running_transport(self) -> None:
        result = external_message(
            "send_message",
            "DEMO",
            "target-1",
            "private",
            message="你好",
            context=self.context,
        )

        self.assertTrue(result["ok"])
        self.assertRegex(result["message_id"], r"^out_[0-9a-f]{12}$")
        self.assertEqual(len(self.transport.sent), 1)
        outbound = self.transport.sent[0]
        self.assertEqual(outbound.platform, "demo")
        self.assertEqual(outbound.external_chat_id, "target-1")
        self.assertEqual(outbound.text, "你好")
        self.assertEqual(outbound.reply_to, "")

    def test_send_file_validates_and_resolves_path(self) -> None:
        source = self.root / "report.txt"
        source.write_text("payload", "utf-8")

        result = external_message(
            "send_file",
            "demo",
            "group-1",
            "group",
            file_path=str(source),
            context=self.context,
        )

        self.assertEqual(result["file"], str(source.resolve()))
        self.assertEqual(self.transport.sent[0].file_path, str(source.resolve()))
        self.assertEqual(self.transport.sent[0].text, "")

    def test_missing_file_is_rejected(self) -> None:
        with self.assertRaises(FileNotFoundError):
            external_message(
                "send_file",
                "demo",
                "target",
                "private",
                file_path=str(self.root / "missing.txt"),
                context=self.context,
            )

    def test_missing_registry_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "TransportRegistry"):
            external_message(
                "send_message",
                "demo",
                "target",
                "private",
                message="hello",
                context={"user": "alice"},
            )

    def test_unknown_platform_is_rejected(self) -> None:
        with self.assertRaises(TransportRegistrationError):
            external_message(
                "send_message",
                "unknown",
                "target",
                "private",
                message="hello",
                context=self.context,
            )

    def test_stopped_transport_is_rejected(self) -> None:
        self.transport.stop()
        self.registered.state = "stopped"
        with self.assertRaisesRegex(RuntimeError, "未运行"):
            external_message(
                "send_message",
                "demo",
                "target",
                "private",
                message="hello",
                context=self.context,
            )

    def test_bound_user_mismatch_is_rejected(self) -> None:
        context = {**self.context, "user": "bob"}
        with self.assertRaises(PermissionError):
            external_message(
                "send_message",
                "demo",
                "target",
                "private",
                message="hello",
                context=context,
            )

    def test_missing_send_file_capability_is_rejected(self) -> None:
        limited_registry = TransportRegistry()
        limited_transport = CapturingTransport()
        limited = limited_registry.register(
            limited_transport,
            TransportPolicy(
                capabilities=frozenset({"receive_text", "send_text"}),
                bound_user="alice",
            ),
        )
        limited_transport.start(lambda envelope: None, lambda component, exc: None)
        limited.state = "running"
        source = self.root / "report.txt"
        source.write_text("payload", "utf-8")
        with self.assertRaisesRegex(PermissionError, "send_file"):
            external_message(
                "send_file",
                "demo",
                "target",
                "private",
                file_path=str(source),
                context={**self.context, "transport_registry": limited_registry},
            )


class ExternalMessageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "users" / "alice" / "history").mkdir(parents=True)

    def test_manifest_is_discoverable(self) -> None:
        manifest = parse_plugin_manifest(
            PROJECT_ROOT / "plugins" / "external_message" / "SKILL.md",
            root=PROJECT_ROOT,
        )
        self.assertEqual(manifest.descriptor.title, "external_message")
        self.assertEqual(manifest.tool["name"], "external_message")
        self.assertEqual(
            manifest.tool["input_schema"]["properties"]["action"]["enum"],
            ["send_message", "send_file"],
        )

    def test_message_router_injects_its_registry(self) -> None:
        registry = TransportRegistry()
        transport = CapturingTransport()
        registered = registry.register(
            transport,
            TransportPolicy(
                capabilities=transport.capabilities,
                bound_user="alice",
            ),
        )
        transport.start(lambda envelope: None, lambda component, exc: None)
        registered.state = "running"
        seen: dict = {}

        def source(request, **kwargs):
            del kwargs
            seen.update(request)
            yield RunEvent(type="done", metadata={"text": "ok"})

        router = MessageRouter(
            self.root,
            None,
            registry,
            event_source=source,
            tool_registry_factory=lambda root, user: ToolRegistry({}),
        )
        envelope = MessageEnvelope(
            message_id="in-1",
            platform="demo",
            chat_type="private",
            external_user_id="external-1",
            external_chat_id="chat-1",
            text="hello",
            timestamp="2026-07-22T00:00:00+08:00",
        )

        result = router.route(envelope)

        self.assertEqual(result.status, "completed")
        self.assertIs(seen["_transport_registry"], registry)

    def test_web_chat_injects_router_registry(self) -> None:
        registry = TransportRegistry()
        seen: dict = {}

        def source(request, **kwargs):
            del kwargs
            seen.update(request)
            yield RunEvent(type="done", metadata={"text": "ok"})

        service = WebRunService(
            self.root,
            event_source=source,
            router_ref=SimpleNamespace(transports=registry),
        )
        events = service.stream_chat(
            "alice",
            "session-1",
            "hello",
            cancel_event=threading.Event(),
        )
        list(events)

        self.assertIs(seen["_transport_registry"], registry)


if __name__ == "__main__":
    unittest.main()
