from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from events import RunEvent
from message.identity import (
    IdentityBinding,
    IdentityError,
    IdentityResolver,
    filter_tool_registry,
)
from message.plugin import (
    FileMessageTransport,
    MessagePluginConfig,
    MessagePluginError,
    discover_message_plugins,
    parse_message_buffer,
)
from message.router import MessageRouteError, MessageRouter, RouteResult
from message.schema import MessageContractError, MessageEnvelope, OutboundMessage
from message.state import ProcessedMessageStore
from message.transport import (
    MockTransport,
    TransportPolicy,
    TransportRegistrationError,
    TransportRegistry,
)
from run.history import commit_window, empty_window, session_messages
from run.history_index import find_record, get_active, get_or_reserve_active
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


def _write_file_plugin(
    root: Path,
    *,
    name: str = "filedemo",
    platform: str = "filedemo",
    bound_user: str = "alice",
) -> Path:
    directory = root / "message" / "out" / name
    (directory / "files").mkdir(parents=True)
    (directory / "log").mkdir()
    (directory / "message.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "machine_id": f"msg_{name}",
                "platform": platform,
                "display_name": name,
                "bound_user": bound_user,
                "modules": {
                    "input": "input.py",
                    "output": "output.py",
                    "detect": "detect.py",
                },
                "capabilities": [
                    "receive_text",
                    "send_text",
                    "receive_file",
                    "send_file",
                ],
                "allowed_tools": [],
                "message_buffer": "message.md",
                "files_dir": "files/",
                "log_dir": "log/",
            }
        ),
        "utf-8",
    )
    (directory / "state.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "health": "unknown",
                "last_check": None,
                "last_message_at": None,
                "error": None,
                "latency_ms": None,
                "messages_received_today": 0,
                "messages_sent_today": 0,
            }
        ),
        "utf-8",
    )
    (directory / "message.md").write_text("", "utf-8")
    (directory / "input.py").write_text(
        "import threading\n"
        "STOP = threading.Event()\n"
        "def start(config, message_buffer, files_dir, state_path):\n"
        "    STOP.clear()\n"
        "    STOP.wait()\n"
        "def stop():\n"
        "    STOP.set()\n",
        "utf-8",
    )
    (directory / "output.py").write_text(
        "SENT = []\n"
        "def send(message):\n"
        "    SENT.append(dict(message))\n"
        "    return True\n",
        "utf-8",
    )
    (directory / "detect.py").write_text(
        "def check(config, state):\n"
        "    return {**state, 'health': 'healthy', 'error': None}\n",
        "utf-8",
    )
    return directory


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

    def test_attachment_only_message_is_valid(self) -> None:
        item = MessageEnvelope(
            message_id="m",
            platform="mock",
            chat_type="private",
            external_user_id="u",
            external_chat_id="c",
            text="",
            timestamp="2026-07-18T00:00:00+08:00",
            attachments=({"path": "files/m.png"},),
        )
        self.assertEqual(item.text, "")

    def test_outbound_reply(self) -> None:
        inbound = _envelope()
        outbound = OutboundMessage.reply(inbound, "ok")
        self.assertEqual(outbound.platform, "mock")
        self.assertEqual(outbound.external_chat_id, "chat-1")
        self.assertEqual(outbound.reply_to, "m1")

    def test_outbound_file_can_be_sent_without_text(self) -> None:
        outbound = OutboundMessage(
            message_id="out",
            platform="mock",
            chat_type="private",
            external_chat_id="chat-1",
            text="",
            file_path="files/result.png",
        )
        self.assertEqual(outbound.text, "")
        self.assertEqual(outbound.file_path, "files/result.png")


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


class FilePluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary, self.root = _root("alice")
        self.addCleanup(self.temporary.cleanup)
        self.directory = _write_file_plugin(self.root)

    def test_parse_repeated_front_matter_and_preserve_markdown_rule(self) -> None:
        messages = parse_message_buffer(
            """---
machine_id: msg_filedemo
message_id: first
chat_type: private
external_user_id: "u1"
external_chat_id: "c1"
timestamp: 2026-07-18T14:30:25+08:00
---

第一段

---

仍是第一条正文

---
machine_id: msg_filedemo
message_id: second
chat_type: group
external_user_id: "u2"
external_chat_id: "g1"
timestamp: 2026-07-18T14:31:25+08:00
---

第二段
"""
        )
        self.assertEqual([item.message_id for item in messages], ["first", "second"])
        self.assertIn("仍是第一条正文", messages[0].text)
        self.assertEqual(messages[1].chat_type, "group")

    def test_config_rejects_paths_outside_plugin(self) -> None:
        config_path = self.directory / "message.json"
        value = json.loads(config_path.read_text("utf-8"))
        value["files_dir"] = "../outside"
        config_path.write_text(json.dumps(value), "utf-8")
        with self.assertRaisesRegex(MessagePluginError, "相对路径"):
            MessagePluginConfig.load(self.root, self.directory)

    def test_file_queue_batch_attachment_route_send_log_and_cleanup(self) -> None:
        image = self.directory / "files" / "p1_0.png"
        image.write_bytes(b"png-data")
        text_file = self.directory / "files" / "p1_1.txt"
        text_file.write_text("TEXT_ATTACHMENT", "utf-8")
        config = MessagePluginConfig.load(self.root, self.directory)
        transport = FileMessageTransport(
            config,
            poll_interval=60,
            health_interval=60,
            settle_interval=0.05,
        )
        received: list[MessageEnvelope] = []
        errors: list[BaseException] = []
        transport.start(received.append, lambda _name, exc: errors.append(exc))
        self.addCleanup(transport.stop)
        time.sleep(0.05)
        (self.directory / "message.md").write_text(
            f"""---
machine_id: msg_filedemo
message_id: p1
chat_type: private
external_user_id: "u1"
external_chat_id: "c1"
timestamp: 2026-07-18T14:30:25+08:00
attachments:
  - path: files/p1_0.png
    name: screenshot.png
    mime: image/png
    size: {image.stat().st_size}
  - path: files/p1_1.txt
    name: note.txt
    mime: text/plain
    size: {text_file.stat().st_size}
---

请分析附件

---
machine_id: msg_filedemo
message_id: g1
chat_type: group
external_user_id: "u2"
external_chat_id: "group"
timestamp: 2026-07-18T14:31:25+08:00
---

第一条群消息

---
machine_id: msg_filedemo
message_id: g2
chat_type: group
external_user_id: "u3"
external_chat_id: "group"
timestamp: 2026-07-18T14:32:25+08:00
---

第二条群消息
""",
            "utf-8",
        )

        time.sleep(0.06)
        self.assertEqual(transport.poll_once(), 2)
        self.assertEqual(len(received), 2)
        self.assertEqual((self.directory / "message.md").read_text("utf-8"), "")
        group = next(item for item in received if item.chat_type == "group")
        self.assertTrue(group.message_id.startswith("batch_"))
        self.assertIn("第一条群消息", group.text)
        self.assertIn("第二条群消息", group.text)
        private = next(item for item in received if item.chat_type == "private")
        payload = transport.request_payload(private)
        self.assertEqual(payload["content"][0]["type"], "image")
        self.assertEqual(payload["content"][0]["source"]["kind"], "inline_base64")
        self.assertIn("TEXT_ATTACHMENT", payload["prompt"])

        for envelope in received:
            outbound = OutboundMessage.reply(
                envelope,
                "reply",
                metadata={
                    "message_queue_token": envelope.metadata["message_queue_token"]
                },
            )
            transport.send(outbound)
            transport.finalize(
                RouteResult(
                    envelope=envelope,
                    user="alice",
                    source=f"message:{envelope.platform}",
                    session_id=f"{envelope.chat_type}:{envelope.external_chat_id}",
                    status="completed",
                    text="reply",
                    outbound=outbound,
                )
            )

        self.assertEqual(len(transport._output.SENT), 2)
        group_output = next(
            item for item in transport._output.SENT if item["chat_type"] == "group"
        )
        self.assertEqual(group_output["reply_to"], "g2")
        self.assertFalse(image.exists())
        self.assertFalse(text_file.exists())
        self.assertFalse(list(self.directory.glob("*.processing.md")))
        log = (self.directory / "log" / "2026-07-18.md").read_text("utf-8")
        self.assertIn("请分析附件", log)
        self.assertIn("screenshot.png", log)
        state = json.loads((self.directory / "state.json").read_text("utf-8"))
        self.assertEqual(state["messages_received_today"], 3)
        self.assertEqual(state["messages_sent_today"], 2)
        self.assertFalse(errors)

    def test_discovery_reports_invalid_folder_without_hiding_valid_plugin(self) -> None:
        (self.root / "message" / "out" / "broken").mkdir()
        transports, issues = discover_message_plugins(self.root)
        self.assertEqual([item.name for item in transports], ["filedemo"])
        self.assertEqual([item.name for item in issues], ["broken"])

    def test_bound_transport_routes_without_identity_binding(self) -> None:
        registry = TransportRegistry()
        transport = MockTransport()
        registry.register(
            transport,
            TransportPolicy(bound_user="alice"),
        )
        transport.start(lambda envelope: None, lambda component, exc: None)
        router = MessageRouter(
            self.root,
            IdentityResolver(self.root, []),
            registry,
            event_source=_done_events,
            tool_registry_factory=lambda root, user: ToolRegistry({}),
        )
        result = router.route(_envelope(platform="mock"))
        self.assertEqual(result.user, "alice")
        self.assertEqual(result.status, "completed")


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

    def test_claim_many_is_atomic(self) -> None:
        store = ProcessedMessageStore(self.root, "alice")
        self.assertTrue(store.claim_many(("mock:m1", "mock:m2")))
        store.complete_many(("mock:m1", "mock:m2"), status="completed")
        self.assertFalse(store.claim_many(("mock:m2", "mock:m3")))
        self.assertIsNone(store.get("mock:m3"))

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
        self.assertTrue(first.session_id.startswith("conv_"))
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
        self.assertEqual([item[:2] for item in seen], [("alice", "message:mock")] * 2)
        self.assertTrue(all(item[2].startswith("conv_") for item in seen))
        self.assertNotEqual(seen[0][2], seen[1][2])

    def test_new_command_saves_current_session_and_queues_memory_without_model(self) -> None:
        (self.root / "config" / "global_config.json").write_text(
            json.dumps({"memory": {"extraction_mode": "compression_only"}}),
            "utf-8",
        )
        source = "message:mock"
        active_key = "message:mock:private:chat-1"
        active, _ = get_or_reserve_active(
            self.root,
            "alice",
            source,
            active_key,
            title="mock · private",
        )
        previous_session_id = str(active["session_id"])
        archive = self.root / "users" / "alice" / "history" / previous_session_id
        window = empty_window("alice", source, previous_session_id)
        window["text"]["messages"] = [
            {"role": "user", "content": "保存这轮"},
            {"role": "assistant", "content": "好的"},
        ]
        window["data"].update(
            {
                "rounds": 1,
                "memory_processed_round": 0,
                "memory_status": "deferred",
            }
        )
        commit_window(archive, window)
        model_calls = 0

        def source_events(request, **kwargs):
            nonlocal model_calls
            model_calls += 1
            yield from _done_events(request, **kwargs)

        result = self._router(source_events).route(
            _envelope("m-new", text="/new")
        )

        self.assertEqual(result.status, "completed")
        self.assertNotEqual(result.session_id, previous_session_id)
        self.assertEqual(model_calls, 0)
        self.assertIn("已创建并切换到新对话", result.text)
        self.assertIn("后台继续提取", result.text)
        self.assertEqual(
            find_record(self.root, "alice", source, previous_session_id)["lifecycle"],
            "closed",
        )
        self.assertEqual(
            find_record(self.root, "alice", source, previous_session_id)["memory_status"],
            "queued",
        )
        self.assertEqual(get_active(self.root, "alice", active_key)["session_id"], result.session_id)
        self.assertEqual(len(self.transport.sent), 1)

    def test_new_command_accepts_telegram_bot_mention(self) -> None:
        model_calls = 0

        def source_events(request, **kwargs):
            nonlocal model_calls
            model_calls += 1
            yield from _done_events(request, **kwargs)

        result = self._router(source_events).route(
            _envelope("m-new-mention", text="/new@kesepain_bot 新标题")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(model_calls, 0)
        self.assertIn("已创建并切换到新对话", result.text)

    def test_clear_command_clears_only_current_external_chat_without_model(self) -> None:
        source = "message:mock"
        first_key = "message:mock:private:chat-1"
        second_key = "message:mock:private:chat-2"
        first, _ = get_or_reserve_active(
            self.root, "alice", source, first_key, title="first"
        )
        second, _ = get_or_reserve_active(
            self.root, "alice", source, second_key, title="second"
        )
        for record, content in ((first, "first message"), (second, "second message")):
            session_id = str(record["session_id"])
            archive = self.root / "users" / "alice" / "history" / session_id
            window = empty_window("alice", source, session_id)
            window["text"]["messages"] = [{"role": "user", "content": content}]
            window["data"]["rounds"] = 1
            commit_window(archive, window)
        model_calls = 0

        def source_events(request, **kwargs):
            nonlocal model_calls
            model_calls += 1
            yield from _done_events(request, **kwargs)

        result = self._router(source_events).route(
            _envelope("m-clear", chat_id="chat-1", text="/clear@kesepain_bot")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.session_id, first["session_id"])
        self.assertEqual(result.text, "当前对话已清空。")
        self.assertEqual(model_calls, 0)
        self.assertEqual(session_messages(self.root, "alice", source, first["session_id"]), [])
        self.assertEqual(
            session_messages(self.root, "alice", source, second["session_id"])[0]["content"],
            "second message",
        )
        self.assertEqual(get_active(self.root, "alice", first_key)["session_id"], first["session_id"])

    def test_unknown_slash_command_returns_help_without_model(self) -> None:
        model_calls = 0

        def source_events(request, **kwargs):
            nonlocal model_calls
            model_calls += 1
            yield from _done_events(request, **kwargs)

        result = self._router(source_events).route(
            _envelope("m-unknown-command", text="/unknown@kesepain_bot")
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(model_calls, 0)
        self.assertIn("未知指令：/unknown", result.text)
        self.assertIn("/new、/clear", result.text)
        self.assertEqual(len(self.transport.sent), 1)

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
            binding = str(request.get("_history_active_key") or "")
            if binding.endswith("parallel-1"):
                barrier.wait(timeout=2)
            elif binding.endswith("parallel-2"):
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
        self.assertEqual(len(CronStore(self.root, "alice").list_tasks()), 0)
        self.assertEqual(
            len(CronStore(self.root, "__system__", system=True).list_tasks()),
            5,
        )
        host.stop()
        host.stop()
        self.assertEqual(host.state, "stopped")
        self.assertEqual(cron.stopped, 1)
        self.assertEqual(host.maintenance.stopped, 1)

    def test_runtime_host_cron_poll_honors_shortest_system_update_rate(self) -> None:
        host = RuntimeHost(
            self.root,
            config={
                "cron": {"enabled": True, "poll_interval": 30},
                "task_cron_system": {
                    "sense_update_rate": 5,
                    "expand_update_rate": 8,
                },
            },
            message_config={},
            registry=TransportRegistry(),
        )
        self.assertEqual(host.cron.poll_interval, 5)
        self.assertIs(host.cron._transport_registry, host.router.transports)
        self.assertEqual(host.maintenance.poll_interval, 30)

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

    def test_folder_plugin_is_auto_discovered_started_and_stopped(self) -> None:
        _write_file_plugin(self.root)
        (self.root / "message" / "out" / "broken").mkdir()
        registry = TransportRegistry()
        config = _config(cron=False)
        router = MessageRouter(
            self.root,
            IdentityResolver(self.root, []),
            registry,
            event_source=_done_events,
            tool_registry_factory=lambda root, user: ToolRegistry({}),
        )
        host = RuntimeHost(
            self.root,
            config=config,
            message_config={},
            registry=registry,
            cron_scheduler=FakeCron(),
            maintenance_scheduler=FakeMaintenance(),
            router=router,
        )
        self.assertIn("filedemo", registry.names())
        self.assertEqual(
            host.status()["components"]["message_plugin:broken"]["state"],
            "failed",
        )
        host.start()
        try:
            component = host.status()["components"]["transport:filedemo"]
            self.assertEqual(component["kind"], "message_plugin")
            self.assertEqual(component["state"], "running")
            transport = registry.get("filedemo").transport
            self.assertTrue(transport.running)
            (self.root / "message" / "out" / "filedemo" / "message.md").write_text(
                """---
machine_id: msg_filedemo
message_id: integrated
chat_type: private
external_user_id: "external"
external_chat_id: "chat"
timestamp: 2026-07-18T14:30:25+08:00
---

hello plugin
""",
                "utf-8",
            )
            deadline = time.time() + 4
            while not transport._output.SENT and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(transport._output.SENT[0]["text"], "reply:hello plugin")
            log = self.root / "message" / "out" / "filedemo" / "log" / "2026-07-18.md"
            deadline = time.time() + 2
            while not log.is_file() and time.time() < deadline:
                time.sleep(0.05)
            self.assertIn("hello plugin", log.read_text("utf-8"))
            (self.root / "message" / "out" / "filedemo" / "message.md").write_text(
                """---
machine_id: msg_filedemo
message_id: integrated
chat_type: group
external_user_id: "external"
external_chat_id: "group"
timestamp: 2026-07-18T14:31:25+08:00
---

duplicate body

---
machine_id: msg_filedemo
message_id: fresh
chat_type: group
external_user_id: "external"
external_chat_id: "group"
timestamp: 2026-07-18T14:32:25+08:00
---

fresh body
""",
                "utf-8",
            )
            deadline = time.time() + 4
            while len(transport._output.SENT) < 2 and time.time() < deadline:
                time.sleep(0.05)
            self.assertEqual(len(transport._output.SENT), 2)
            self.assertEqual(transport._output.SENT[1]["text"], "reply:fresh body")
        finally:
            host.stop()
        self.assertFalse(registry.get("filedemo").transport.running)


if __name__ == "__main__":
    unittest.main()
