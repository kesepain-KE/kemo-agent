from __future__ import annotations

from typing import Any

from tests.type_assertions import require_present

import asyncio
import importlib
import yaml
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
from contextlib import ExitStack, contextmanager


ROOT = Path(__file__).resolve().parents[2]
MODULE_ROOT = ROOT / "expand" / "global" / "kemo_bridge"


@contextmanager
def _bridge_imports():
    # The expansion entry points import siblings as top-level scripts. Do not
    # leave those names or their directory on the import path of other tests.
    names = {path.stem for path in MODULE_ROOT.glob("*.py")}
    saved = {name: sys.modules.pop(name) for name in names if name in sys.modules}
    sys.path.insert(0, str(MODULE_ROOT))
    try:
        yield
    finally:
        sys.path.remove(str(MODULE_ROOT))
        for name in names:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


with _bridge_imports():
    bridge_auth = importlib.import_module("auth")
    bridge_config = importlib.import_module("bridge_config")
    bridge_upstream = importlib.import_module("upstream")
    bridge_run_broker = importlib.import_module("run_broker")
    bridge_events = importlib.import_module("bridge_events")
    bridge_device_commands = importlib.import_module("device_commands")
    bridge_lifecycle = importlib.import_module("lifecycle")
    bridge_initialize = importlib.import_module("initialize_config")
    bridge_start = importlib.import_module("start_expand")
    bridge_update = importlib.import_module("data_update")
    bridge_peer_store = importlib.import_module("peer_store")

from run.modules import read_manifest, ModuleSpecError  # noqa: E402
from web.services.module_panels import load_module_panel  # noqa: E402


class KemoBridgeExpandTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        stack.enter_context(mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(scratch / "_lifecycle.lock")))
        stack.enter_context(mock.patch.object(bridge_update, "write_status"))

    def test_lifecycle_is_native_panel_only(self) -> None:
        panel, error = load_module_panel(MODULE_ROOT, "expand")
        self.assertEqual(error, "")
        containers = require_present(panel)["containers"]
        self.assertEqual([container["kind"] for container in containers], ["status", "action"])
        self.assertEqual(
            {control["command"] for container in containers[1:] for control in container["controls"]},
            {"initialize", "start", "deactivate", "restart"},
        )
        workspace = json.loads((MODULE_ROOT / "module/workspace.json").read_text("utf-8"))
        self.assertEqual(
            {action["id"] for action in workspace["actions"]},
            {"bridge_admin"},
        )

    def test_bridge_follows_runtime_local_web_endpoint(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"KEMO_AGENT_WEB_BASE_URL": "http://127.0.0.1:24680"},
        ):
            self.assertEqual(
                bridge_upstream.resolve_upstream_base_url({}),
                "http://127.0.0.1:24680",
            )
            self.assertEqual(
                bridge_upstream.resolve_upstream_base_url(
                    {"upstream": "http://127.0.0.1:1357"}
                ),
                "http://127.0.0.1:24680",
            )

    def test_bridge_preserves_explicit_non_local_upstream(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"KEMO_AGENT_WEB_BASE_URL": "http://127.0.0.1:24680"},
        ):
            configured = "https://gateway.example.invalid:1457"
            self.assertEqual(
                bridge_upstream.resolve_upstream_base_url({"upstream": configured}),
                configured,
            )

    def test_bridge_accepts_remote_runtime_endpoint_without_a_host_list(self) -> None:
        for endpoint in ("https://remote.example.invalid:24680", "http://10.66.0.2:1357",
                         "http://[2001:db8::1]:1357/agent"):
            with self.subTest(endpoint=endpoint), mock.patch.dict(
                os.environ, {"KEMO_AGENT_WEB_BASE_URL": endpoint},
            ):
                self.assertEqual(bridge_upstream.resolve_upstream_base_url({}), endpoint)
                self.assertEqual(
                    bridge_upstream.resolve_upstream_base_url({"upstream": "http://127.0.0.1:1357"}),
                    endpoint,
                )
        for endpoint in ("ftp://remote.example.invalid", "http://user:secret@remote.example.invalid",
                         "http://remote.example.invalid:0", "http://remote.example.invalid:70000",
                         "https://remote.example.invalid?token=secret"):
            with self.subTest(endpoint=endpoint), mock.patch.dict(
                os.environ, {"KEMO_AGENT_WEB_BASE_URL": endpoint},
            ):
                self.assertEqual(bridge_upstream.resolve_upstream_base_url({}), "http://127.0.0.1:1357")

    def test_test_loader_does_not_leak_generic_expand_module_aliases(self) -> None:
        self.assertIsNot(sys.modules.get("start_expand"), bridge_start)
        self.assertIsNot(sys.modules.get("lifecycle"), bridge_lifecycle)

    def test_bridge_declares_current_version(self) -> None:
        manifest = yaml.safe_load((MODULE_ROOT / "module.yaml").read_text("utf-8"))
        self.assertEqual(bridge_config.VERSION, "2.1.0")
        self.assertEqual(manifest["name"], "kemo_bridge")
        self.assertIn(f"v{bridge_config.VERSION}", manifest["explain"])
        self.assertIn(bridge_config.VERSION, (MODULE_ROOT / "README.md").read_text("utf-8"))

    def test_bridge_keeps_android_conversations_in_app_partition(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('APP_SOURCE = "app"', source)
        self.assertIn('"source": APP_SOURCE', source)
        self.assertIn('params={"source": APP_SOURCE}', source)
        self.assertIn('@routes.route("GET", "/conversations/active", "chat")', source)
        self.assertIn('params={"source": APP_SOURCE, "client_id": client_id}', source)
        self.assertNotIn('"web" if source == "app" else source', source)

    def test_bridge_exposes_detached_run_snapshot_and_resume_routes(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('@routes.route("GET", "/runs/active", "run_status")', source)
        self.assertIn('@routes.route("GET", "/runs/{run_id}/snapshot", "run_status")', source)
        self.assertIn('@routes.route("GET", "/runs/{run_id}/stream", "run_status")', source)
        self.assertIn("record = await RUNS.start(session.username, payload)", source)
        self.assertNotIn('UPSTREAM.open_stream("POST", "/api/chat"', source)

    def test_active_runs_allows_device_only_recovery_scope(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('if not requested_session and not requested_client:', source)
        self.assertIn('raise HTTPException(400, "client_id_or_session_id_required")', source)
        self.assertIn('client_id=requested_client,', source)
        self.assertIn('session_id=requested_session,', source)

    def test_bridge_controls_forward_the_durable_run_session_scope(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        broker_source = (MODULE_ROOT / "run_broker.py").read_text(encoding="utf-8")
        self.assertIn("scope = RUNS.scope(session.username, body.run_id)", source)
        self.assertIn('"source": APP_SOURCE', source)
        self.assertIn('"session_id": requested_session', source)
        self.assertIn("def scope(self, username: str, run_id: str)", broker_source)

    def test_bridge_uses_core_session_id_length_for_all_boundaries(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn("SESSION_ID_MAX_LENGTH = 128", source)
        self.assertNotIn("session_id: str = Field(min_length=1, max_length=256)", source)
        self.assertNotIn('session_id: str = Query("", max_length=256)', source)
        self.assertNotIn("len(subscribed_session_id) > 256", source)
        self.assertIn("FastAPIPath(..., min_length=1, max_length=SESSION_ID_MAX_LENGTH)", source)

    def test_bridge_run_scope_lookup_is_user_scoped_without_replay_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_run_broker.RunStore(Path(directory) / "runs.sqlite3")
            store.create(
                "alice",
                {"run_id": "run_scope", "session_id": "app-session", "prompt": "hello"},
            )
            self.assertEqual(
                store.get("alice", "run_scope")["session_id"],
                "app-session",
            )
            with self.assertRaises(KeyError):
                store.get("bob", "run_scope")
            store.close()


    def test_chat_streams_the_broker_issued_run_id_for_legacy_clients(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn(
            'run_id = str(body.run_id or "").strip() or f"run_{uuid.uuid4().hex}"',
            source,
        )
        self.assertIn('"run_id": run_id', source)
        self.assertIn(
            'session_id=requested_session',
            source,
        )

    def test_detached_run_continues_after_mobile_subscriber_closes(self) -> None:
        async def scenario() -> None:
            release = asyncio.Event()

            class Response:
                async def aiter_lines(self):
                    yield 'data: {"type":"text_delta","text":"hello"}'
                    yield ""
                    await release.wait()
                    yield 'data: {"type":"done"}'
                    yield ""

                async def aclose(self) -> None:
                    return None

            class Upstream:
                async def open_stream(self, *_args, **_kwargs):
                    return Response()

            with tempfile.TemporaryDirectory() as directory:
                store = bridge_run_broker.RunStore(Path(directory) / "runs.sqlite3")
                broker = bridge_run_broker.RunBroker(Upstream(), store)
                payload = {
                    "run_id": "run-detached",
                    "session_id": "app-session",
                    "client_id": "phone",
                    "user": "mobile-user",
                    "source": "app",
                    "prompt": "hello",
                }
                await broker.start("mobile-user", payload)
                subscriber = broker.stream("mobile-user", "run-detached")
                first = await anext(subscriber)
                self.assertEqual(first.event_id, 1)
                await subscriber.aclose()

                # Closing the Android subscriber must not cancel the broker's
                # upstream task. It remains active until the upstream terminal.
                self.assertIn(
                    broker.snapshot("mobile-user", "run-detached")["status"],
                    {"starting", "running"},
                )
                release.set()
                snapshot = {}
                for _ in range(100):
                    snapshot = broker.snapshot("mobile-user", "run-detached")
                    if snapshot["terminal"]:
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(snapshot["terminal"])
                self.assertEqual(snapshot["status"], "completed")
                self.assertEqual(snapshot["last_event_id"], 2)
                self.assertEqual(len(snapshot["events"]), 2)
                await broker.stop()

        asyncio.run(scenario())

    def test_explicit_cancel_is_not_overwritten_when_upstream_connects_late(self) -> None:
        async def scenario() -> None:
            allow_connect = asyncio.Event()
            stream_entered = asyncio.Event()
            allow_finish = asyncio.Event()

            class Response:
                async def aiter_lines(self):
                    stream_entered.set()
                    await allow_finish.wait()
                    yield 'data: {"type":"done","metadata":{"status":"cancelled"}}'
                    yield ""

                async def aclose(self) -> None:
                    return None

            class Upstream:
                async def open_stream(self, *_args, **_kwargs):
                    await allow_connect.wait()
                    return Response()

            with tempfile.TemporaryDirectory() as directory:
                store = bridge_run_broker.RunStore(Path(directory) / "runs.sqlite3")
                broker = bridge_run_broker.RunBroker(Upstream(), store)
                payload = {
                    "run_id": "run-cancel-connect-race",
                    "session_id": "app-session",
                    "client_id": "phone",
                    "user": "mobile-user",
                    "source": "app",
                    "prompt": "hello",
                }
                await broker.start("mobile-user", payload)
                broker.mark_cancelling("mobile-user", payload["run_id"])
                allow_connect.set()
                await asyncio.wait_for(stream_entered.wait(), timeout=1.0)
                self.assertEqual(
                    broker.snapshot("mobile-user", payload["run_id"])["status"],
                    "cancelling",
                )
                allow_finish.set()
                snapshot = {}
                for _ in range(100):
                    snapshot = broker.snapshot("mobile-user", payload["run_id"])
                    if snapshot["terminal"]:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(snapshot["status"], "cancelled")
                await broker.stop()

        asyncio.run(scenario())

    def test_run_snapshots_are_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_run_broker.RunStore(Path(directory) / "runs.sqlite3")
            store.create(
                "alice",
                {"run_id": "run-a", "session_id": "app-a", "client_id": "phone"},
            )
            with self.assertRaises(KeyError):
                store.get("bob", "run-a")
            with self.assertRaises(PermissionError):
                store.create(
                    "bob",
                    {"run_id": "run-a", "session_id": "app-a", "client_id": "phone"},
                )
            store.close()

    def test_terminal_run_replay_is_bounded_per_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_run_broker.RunStore(
                Path(directory) / "runs.sqlite3",
                retention_seconds=3600,
                max_terminal_runs_per_user=2,
            )
            for index in range(1, 4):
                run_id = f"run-{index}"
                store.create(
                    "alice",
                    {
                        "run_id": run_id,
                        "session_id": f"app-{index}",
                        "client_id": "phone",
                        "prompt": f"secret-{index}",
                    },
                )
                store.append("alice", run_id, '{"type":"done"}')

            deleted = store.prune()

            self.assertEqual(deleted, ["run-1"])
            with self.assertRaises(KeyError):
                store.get("alice", "run-1")
            self.assertEqual(store.get("alice", "run-3")["status"], "completed")
            store.close()

    def test_conversation_delete_removes_only_terminal_replay_copies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_run_broker.RunStore(Path(directory) / "runs.sqlite3")
            common = {"session_id": "app-a", "client_id": "phone"}
            store.create("alice", {**common, "run_id": "run-active"})
            store.create("alice", {**common, "run_id": "run-terminal"})
            store.append("alice", "run-terminal", '{"type":"done"}')
            store.create(
                "bob",
                {"run_id": "run-bob", "session_id": "app-a", "client_id": "phone"},
            )
            store.append("bob", "run-bob", '{"type":"done"}')

            self.assertEqual(store.delete_session("alice", "app-a"), ["run-terminal"])

            with self.assertRaises(KeyError):
                store.get("alice", "run-terminal")
            self.assertEqual(store.get("alice", "run-active")["status"], "starting")
            self.assertEqual(store.get("bob", "run-bob")["status"], "completed")
            store.set_status("alice", "run-active", "completed")
            self.assertTrue(store.delete_if_requested("alice", "run-active"))
            with self.assertRaises(KeyError):
                store.get("alice", "run-active")
            store.close()

    def test_deferred_conversation_delete_survives_bridge_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runs.sqlite3"
            store = bridge_run_broker.RunStore(path)
            store.create(
                "alice",
                {"run_id": "run-active", "session_id": "app-a", "client_id": "phone"},
            )
            self.assertEqual(store.delete_session("alice", "app-a"), [])
            store.close()

            restarted = bridge_run_broker.RunStore(path)
            with self.assertRaises(KeyError):
                restarted.get("alice", "run-active")
            restarted.close()

    def test_bridge_status_keeps_android_context_in_app_partition(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('params = {"source": APP_SOURCE}', source)
        self.assertIn('f"/api/users/{user}/sessions/active"', source)
        self.assertIn('params={"source": APP_SOURCE, "client_id": client_id}', source)
        self.assertIn('f"/api/users/{user}/overview", params=params', source)
        self.assertIn('f"/api/users/{user}/runtime/status", params=params', source)

    def test_bridge_task_and_status_routes_keep_explicit_session_scope(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        events = (MODULE_ROOT / "bridge_events.py").read_text(encoding="utf-8")
        self.assertIn(
            'data = await _tasks(session.username, session_id=requested_session)',
            source,
        )
        self.assertIn('if client_id and not resolved_session_id:', source)
        self.assertIn(
            'params = {"source": APP_SOURCE, "session_id": resolved_session_id}',
            source,
        )
        self.assertIn(
            'if target_session and connection_session != target_session:',
            events,
        )

    def test_event_hub_never_delivers_scoped_events_to_other_or_unbound_connections(self) -> None:
        async def scenario() -> None:
            hub = bridge_events.EventHub(object())
            session_a = hub.subscribe("alice", "phone-a", "session-a")
            session_b = hub.subscribe("alice", "phone-b", "session-b")
            unbound = hub.subscribe("alice", "legacy-phone")
            try:
                await hub.publish(
                    "alice",
                    "conversation.completed",
                    {"session_id": "session-a"},
                    session_id="session-a",
                )
                self.assertEqual((await session_a.get())["session_id"], "session-a")
                self.assertTrue(session_b.empty())
                self.assertTrue(unbound.empty())

                # A missing scope is never a wildcard for conversation or
                # task-plan events.  This protects future callers that forget
                # to pass session_id from turning private events into a
                # user-wide broadcast.
                await hub.publish(
                    "alice",
                    "conversation.completed",
                    {"session_id": "session-a"},
                )
                await hub.publish(
                    "alice",
                    "task_plan.completed",
                    {"session_id": "session-a"},
                )
                self.assertTrue(session_a.empty())
                self.assertTrue(session_b.empty())
                self.assertTrue(unbound.empty())

                # The envelope scope and payload scope must agree; otherwise
                # a misrouted plan snapshot is dropped instead of delivered to
                # the wrong conversation.
                await hub.publish(
                    "alice",
                    "task_plan.completed",
                    {"session_id": "session-b"},
                    session_id="session-a",
                )
                self.assertTrue(session_a.empty())
                self.assertTrue(session_b.empty())
                self.assertTrue(unbound.empty())

                await hub.publish("alice", "system.warning", {"message": "health"})
                self.assertEqual((await session_a.get())["type"], "system.warning")
                self.assertEqual((await session_b.get())["type"], "system.warning")
                self.assertEqual((await unbound.get())["type"], "system.warning")

                self.assertFalse(
                    await hub.publish_to_device(
                        "alice",
                        "phone-a",
                        "task_plan.completed",
                        {},
                    )
                )
                self.assertTrue(session_a.empty())
                self.assertTrue(session_b.empty())
                self.assertTrue(unbound.empty())

                self.assertTrue(
                    await hub.publish_to_device(
                        "alice",
                        "phone-a",
                        "task_plan.completed",
                        {},
                        session_id="session-a",
                    )
                )
                self.assertEqual((await session_a.get())["type"], "task_plan.completed")
                self.assertTrue(session_b.empty())
                self.assertTrue(unbound.empty())
            finally:
                await hub.stop()

        asyncio.run(scenario())

    def test_terminal_done_statuses_preserve_failure_and_interruption(self) -> None:
        expected = {
            "": "completed",
            "completed": "completed",
            "success": "completed",
            "failed": "failed",
            "error": "failed",
            "limited": "interrupted",
            "paused": "interrupted",
            "interrupted": "interrupted",
            "cancelled": "cancelled",
        }
        for upstream_status, bridge_status in expected.items():
            payload = json.dumps(
                {"type": "done", "metadata": {"status": upstream_status}}
            )
            status, _ = bridge_run_broker._terminal_status(payload)
            self.assertEqual(status, bridge_status, upstream_status)

        # Some upstream adapters put status beside metadata.  It must receive
        # the same mapping instead of falling through to completed.
        status, _ = bridge_run_broker._terminal_status(
            json.dumps({"type": "done", "status": "failed"})
        )
        self.assertEqual(status, "failed")

    def test_event_hub_snapshot_uses_app_source_and_custom_app_session_ids(self) -> None:
        async def scenario() -> None:
            calls: list[tuple[str, str, dict[str, object]]] = []

            class Upstream:
                async def request_json(self, method, path, **kwargs):
                    calls.append((method, path, kwargs))
                    if path.endswith("/tasks"):
                        return {"plans": []}
                    return {"sessions": []}

                async def health(self):
                    return {"status": "ok"}

            hub = bridge_events.EventHub(Upstream())
            snapshot = await hub._snapshot("alice")
            self.assertEqual(snapshot["tasks"], {"plans": []})
            task_call = next(call for call in calls if call[1].endswith("/tasks"))
            session_call = next(call for call in calls if call[1].endswith("/sessions"))
            self.assertEqual(task_call[2]["params"], {"source": "app"})
            self.assertEqual(
                session_call[2]["params"],
                {"source": "app", "limit": "50"},
            )

            queue = hub.subscribe("alice", "phone-a", "custom-session")
            await hub._emit_diff(
                "alice",
                {"health": {"status": "ok"}, "tasks": {"plans": []}, "sessions": {"sessions": []}},
                {
                    "health": {"status": "ok"},
                    "tasks": {"plans": []},
                    "sessions": {
                        "sessions": [
                            {
                                "session_id": "custom-session",
                                "source": "app",
                                "rounds": 1,
                            }
                        ]
                    },
                },
            )
            self.assertEqual((await queue.get())["type"], "conversation.completed")
            await hub.stop()

        asyncio.run(scenario())

    def test_conversation_delete_and_close_forward_android_client_id(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('@routes.route("DELETE", "/conversations/{session_id}", "chat")', source)
        self.assertIn('@routes.route("POST", "/conversations/{session_id}/close", "chat")', source)
        self.assertGreaterEqual(source.count('client_id: str = Query("", max_length=128)'), 3)
        self.assertGreaterEqual(source.count('params["client_id"] = client_id'), 2)
        self.assertIn("RUNS.delete_user(session.username)", source)
        self.assertIn("RUNS.delete_session(session.username, session_id)", source)

    def test_bridge_exposes_user_scoped_model_capabilities(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('@device.route("GET", "/models/capabilities", "config")', source)
        self.assertIn('f"/api/users/{user}/provider/model-capabilities"', source)
        self.assertIn('params={"model": model, "refresh": str(refresh).lower()}', source)

    def test_bridge_defers_reasoning_selection_to_account_configuration(self) -> None:
        source = "\n".join((MODULE_ROOT / name).read_text(encoding="utf-8") for name in ("app.py", "conversation_routes.py"))
        self.assertIn('reasoning_effort: str = Field(default="", max_length=64)', source)
        self.assertIn('body.model_dump(exclude={"reasoning_effort"})', source)
        self.assertNotIn('pattern="^(minimal|low|medium|high|max)$"', source)

    def test_manifest_is_discoverable_with_runtime_health_snapshot(self) -> None:
        meta = read_manifest(MODULE_ROOT, kind_hint="expand", scope="global")
        self.assertEqual(meta.kind, "expand")
        self.assertFalse(meta.input_open)
        # The background collector may update the tracked manifest from the
        # distributable inactive snapshot to the current live health while the
        # test suite is running. Both are valid discovery states; capability
        # and executable entry points must remain stable.
        self.assertIn(meta.health, {"正常", "异常"})
        self.assertTrue(meta.control_open)
        self.assertEqual(meta.update_script.name, "data_update.py")
        assert meta.control_script is not None
        self.assertEqual(meta.control_script.name, "start_expand.py")
        manifest = yaml.safe_load((MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"))
        if "recent_update" in manifest:
            self.assertIsInstance(manifest["recent_update"], str)
            self.assertTrue(manifest["recent_update"].strip())

    def test_published_status_is_inactive_and_has_initialization_entrypoints(self) -> None:
        initial = (MODULE_ROOT / "input_data.md").read_text(encoding="utf-8")
        status = next((line for line in initial.splitlines() if line.startswith("- 状态: ")), "")
        self.assertIn(status, {"- 状态: **未激活**", "- 状态: **已停用**"})
        self.assertNotIn("上游: online", initial)
        self.assertNotIn("在线设备", initial)
        for name in ("initialize_config.py", "lifecycle.py"):
            self.assertTrue((MODULE_ROOT / name).is_file(), name)

    def test_example_configuration_contains_no_live_credentials(self) -> None:
        config = json.loads((MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertNotIn("token_sha256", config)
        self.assertEqual(config["session_secret"], "")
        self.assertEqual(config["upstream_token"], "")
        self.assertEqual(config["upstream_username"], "")
        self.assertEqual(config["upstream_password"], "")

    def test_initializer_creates_local_files_but_does_not_activate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.example.json").write_text(
                (MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            result = bridge_initialize.initialize(root)
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertTrue(result["initialized"])
            self.assertTrue(result["configured"])
            self.assertFalse((root / "_activated.json").exists())
            self.assertGreaterEqual(len(config["session_secret"]), 32)
            self.assertNotIn("token_sha256", config)
            self.assertEqual(json.loads((root / "users.json").read_text(encoding="utf-8")), {})

    def test_lifecycle_becomes_configured_with_local_secret_and_valid_upstream(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = json.loads((MODULE_ROOT / "config.example.json").read_text(encoding="utf-8"))
            config.update(
                {
                    "session_secret": "s" * 48,
                    "upstream": "http://127.0.0.1:1457",
                }
            )
            (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
            (root / "users.json").write_text(
                json.dumps({"mobile-user": {"salt": "salt", "hash": "hash", "enabled": True}}),
                encoding="utf-8",
            )
            state = bridge_lifecycle.inspect_configuration(root)
            self.assertTrue(state["initialized"])
            self.assertTrue(state["configured"])
            self.assertEqual(state["missing"], [])
            self.assertEqual(state["enabled_users"], 1)

    def test_uninitialized_update_never_probes_localhost(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest_path = root / "module.yaml"
            input_path = root / "input_data.md"
            manifest_path.write_text(
                (MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_update, "BASE_DIR", root),
                mock.patch.object(bridge_update, "INPUT_PATH", input_path),
                mock.patch.object(bridge_update, "MANIFEST_PATH", manifest_path),
                mock.patch.object(bridge_update.urllib.request, "urlopen") as urlopen,
            ):
                result = bridge_update.update()
            self.assertEqual(result["status"], "inactive")
            self.assertFalse(result["active"])
            urlopen.assert_not_called()
            self.assertIn("未激活", input_path.read_text(encoding="utf-8"))
            refreshed_manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(refreshed_manifest["input_open"])
            self.assertEqual(refreshed_manifest["health"], "正常")

    def test_start_refuses_to_spawn_before_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(bridge_start, "BASE_PATH", root),
                mock.patch.object(bridge_start, "PID_PATH", str(root / "_server.pid")),
                mock.patch.object(bridge_start, "LOG_PATH", str(root / "logs" / "server.log")),
                mock.patch.object(bridge_start, "_port_open") as port_open,
                mock.patch.object(bridge_start.subprocess, "Popen") as popen,
            ):
                result = bridge_start.start()
            self.assertFalse(result["ok"])
            self.assertFalse(result["active"])
            self.assertEqual(result["error"], "bridge_not_initialized")
            port_open.assert_not_called()
            popen.assert_not_called()

    def test_start_and_stop_persist_explicit_activation_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activation = root / "_activated.json"
            with (
                mock.patch.object(bridge_start, "ACTIVATION_PATH", str(activation)),
                mock.patch.object(
                    bridge_start,
                    "status",
                    side_effect=[
                        {"ok": True, "configured": True, "running": True, "active": True},
                        {
                            "ok": True,
                            "configured": True,
                            "running": True,
                            "active": True,
                            "activated": True,
                        },
                    ],
                ),
            ):
                started = bridge_start.start()
            self.assertTrue(started["ok"])
            self.assertTrue(activation.is_file())
            saved = json.loads(activation.read_text(encoding="utf-8"))
            self.assertEqual(saved["consecutive_failures"], 0)
            self.assertIsNone(saved["last_launch_attempt"])

            with (
                mock.patch.object(bridge_start, "ACTIVATION_PATH", str(activation)),
                mock.patch.object(bridge_start, "PID_PATH", str(root / "_server.pid")),
                mock.patch.object(bridge_start, "status", return_value={"running": False}),
            ):
                stopped = bridge_start.deactivate()
            self.assertTrue(stopped["ok"])
            self.assertFalse(activation.exists())

    def test_status_requires_pid_and_instance_identity_to_match_health(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            pid_path.write_text(
                json.dumps({"pid": 1234, "instance_id": "instance-a"}),
                encoding="utf-8",
            )
            initialization = {
                "initialized": True,
                "configured": True,
                "missing": [],
                "host": "127.0.0.1",
                "port": 8742,
                "upstream_configured": True,
                "enabled_users": 1,
            }
            with (
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, initialization),
                ),
                mock.patch.object(bridge_start, "_pid_alive", return_value=True),
                mock.patch.object(
                    bridge_start,
                    "_health",
                    return_value={
                        "service": "kemo_bridge",
                        "process_pid": 1234,
                        "instance_id": "instance-a",
                    },
                ),
            ):
                matched = bridge_start.status()
            self.assertTrue(matched["running"])
            self.assertEqual(matched["pid"], 1234)
            self.assertFalse(matched["stale_pid"])

    def test_stop_does_not_signal_process_from_stale_pid_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            pid_path.write_text(
                json.dumps({"pid": 1234, "instance_id": "old-instance"}),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, {"configured": True}),
                ),
                mock.patch.object(bridge_start, "_pid_alive", return_value=True),
                mock.patch.object(
                    bridge_start,
                    "_health",
                    return_value={
                        "service": "kemo_bridge",
                        "process_pid": 9999,
                        "instance_id": "other-instance",
                    },
                ),
                mock.patch.object(bridge_start.os, "kill") as kill,
                mock.patch.object(
                    bridge_start,
                    "status",
                    return_value={"running": True, "unmanaged_process": True},
                ),
            ):
                result = bridge_start._stop_process()
            kill.assert_not_called()
            self.assertFalse(pid_path.exists())
            self.assertTrue(result["unmanaged_process"])

    def test_status_reconciles_pid_when_instance_identity_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            pid_path.write_text(
                json.dumps({"pid": 1234, "instance_id": "instance-a"}),
                encoding="utf-8",
            )
            initialization = {
                "initialized": True,
                "configured": True,
                "missing": [],
                "host": "127.0.0.1",
                "port": 8742,
                "upstream_configured": True,
                "enabled_users": 1,
            }
            with (
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, initialization),
                ),
                mock.patch.object(bridge_start, "_pid_alive", return_value=True),
                mock.patch.object(
                    bridge_start,
                    "_health",
                    return_value={
                        "service": "kemo_bridge",
                        "process_pid": 5678,
                        "instance_id": "instance-a",
                    },
                ),
            ):
                result = bridge_start.status()
            self.assertTrue(result["active"])
            self.assertTrue(result["reconciled_pid"])
            self.assertEqual(result["pid"], 5678)
            self.assertEqual(json.loads(pid_path.read_text(encoding="utf-8"))["pid"], 5678)

    def test_stop_uses_health_pid_when_same_instance_was_handed_off(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            pid_path.write_text(
                json.dumps({"pid": 1234, "instance_id": "instance-a"}),
                encoding="utf-8",
            )
            health = {
                "service": "kemo_bridge",
                "process_pid": 5678,
                "instance_id": "instance-a",
            }
            with (
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, {"configured": True}),
                ),
                mock.patch.object(bridge_start, "_pid_alive", return_value=True),
                mock.patch.object(bridge_start, "_health", side_effect=[health, None, None]),
                mock.patch.object(bridge_start, "_terminate_pid", return_value=True) as terminate,
                mock.patch.object(bridge_start, "status", return_value={"ok": True, "running": False}),
            ):
                result = bridge_start._stop_process()
            terminate.assert_called_once_with(5678)
            self.assertFalse(pid_path.exists())
            self.assertFalse(result["running"])

    def test_start_distinguishes_raw_port_conflict_without_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "status",
                    return_value={"ok": True, "configured": True, "running": False, "active": False},
                ),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, {"configured": True}),
                ),
                mock.patch.object(bridge_start, "_port_open", return_value=True),
                mock.patch.object(bridge_start.subprocess, "Popen") as popen,
            ):
                result = bridge_start.start()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "bridge_port_in_use")
            popen.assert_not_called()

    def test_start_does_not_duplicate_recent_launcher_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            pid_path.write_text(
                json.dumps(
                    {
                        "pid": 1234,
                        "instance_id": "pending-instance",
                        "started_at": bridge_start.datetime.now().astimezone().isoformat(timespec="seconds"),
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "status",
                    return_value={"ok": True, "configured": True, "running": False, "active": False},
                ),
                mock.patch.object(bridge_start, "_pid_alive", return_value=False),
                mock.patch.object(bridge_start.subprocess, "Popen") as popen,
            ):
                result = bridge_start.start()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "bridge_start_pending")
            self.assertTrue(pid_path.exists())
            popen.assert_not_called()

    def test_start_accepts_launcher_exit_when_matching_health_appears(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            activation = root / "_activated.json"
            fake_process = mock.Mock(pid=1234)
            fake_process.poll.return_value = 0
            with (
                mock.patch.object(bridge_start, "BASE_DIR", str(root)),
                mock.patch.object(bridge_start, "SERVER_PATH", str(root / "daemon.py")),
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(bridge_start, "LOG_PATH", str(root / "logs" / "server.log")),
                mock.patch.object(bridge_start, "ACTIVATION_PATH", str(activation)),
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "status",
                    side_effect=[
                        {"ok": True, "configured": True, "running": False, "active": False},
                        {"ok": True, "configured": True, "running": True, "active": True},
                    ],
                ),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, {"configured": True}),
                ),
                mock.patch.object(bridge_start, "_port_open", return_value=False),
                mock.patch.object(
                    bridge_start,
                    "_health",
                    side_effect=[
                        None,
                        {
                            "service": "kemo_bridge",
                            "process_pid": 5678,
                            "instance_id": mock.ANY,
                        },
                    ],
                ) as health,
                mock.patch.object(bridge_start.subprocess, "Popen", return_value=fake_process),
                mock.patch.object(bridge_start.time, "sleep"),
            ):
                # The generated nonce must be reflected by the fake health reply.
                def health_side_effect(_port):
                    if health.call_count == 1:
                        return None
                    nonce = json.loads(pid_path.read_text(encoding="utf-8"))["instance_id"]
                    return {"service": "kemo_bridge", "process_pid": 5678, "instance_id": nonce}

                health.side_effect = health_side_effect
                result = bridge_start.start()
            self.assertTrue(result["ok"])
            self.assertEqual(json.loads(pid_path.read_text(encoding="utf-8"))["pid"], 5678)

    def test_concurrent_start_spawns_only_one_bridge_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pid_path = root / "_server.pid"
            activation = root / "_activated.json"
            spawned: dict[str, Any] = {"instance_id": "", "pid": 4321}
            initialization = {
                "initialized": True,
                "configured": True,
                "missing": [],
                "host": "127.0.0.1",
                "port": 8742,
                "upstream_configured": True,
                "enabled_users": 1,
            }
            fake_process = mock.Mock(pid=4321)
            fake_process.poll.return_value = None

            def fake_popen(*_args, **kwargs):
                spawned["instance_id"] = kwargs["env"]["KEMO_BRIDGE_INSTANCE_ID"]
                return fake_process

            def fake_health(_port):
                nonce = str(spawned["instance_id"])
                if not nonce:
                    return None
                return {
                    "service": "kemo_bridge",
                    "process_pid": int(spawned["pid"]),
                    "instance_id": nonce,
                }

            results: list[dict] = []
            with (
                mock.patch.object(bridge_start, "BASE_DIR", str(root)),
                mock.patch.object(bridge_start, "SERVER_PATH", str(root / "daemon.py")),
                mock.patch.object(bridge_start, "PID_PATH", str(pid_path)),
                mock.patch.object(bridge_start, "LOG_PATH", str(root / "logs" / "server.log")),
                mock.patch.object(bridge_start, "ACTIVATION_PATH", str(activation)),
                mock.patch.object(bridge_start, "LIFECYCLE_LOCK_PATH", str(root / "lifecycle.lock")),
                mock.patch.object(
                    bridge_start,
                    "_load_config",
                    return_value=({"port": 8742}, initialization),
                ),
                mock.patch.object(bridge_start, "_health", side_effect=fake_health),
                mock.patch.object(bridge_start, "_port_open", return_value=False),
                mock.patch.object(bridge_start, "_pid_alive", return_value=True),
                mock.patch.object(bridge_start.subprocess, "Popen", side_effect=fake_popen) as popen,
            ):
                threads = [threading.Thread(target=lambda: results.append(bridge_start.start())) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=5)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(result["ok"] for result in results))
            self.assertEqual(popen.call_count, 1)

    def test_restart_aborts_when_existing_process_cannot_be_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with (
                mock.patch.object(
                    bridge_start,
                    "LIFECYCLE_LOCK_PATH",
                    str(Path(directory) / "lifecycle.lock"),
                ),
                mock.patch.object(
                    bridge_start,
                    "_stop_process",
                    return_value={"ok": False, "running": True, "error": "bridge_stop_timeout"},
                ),
                mock.patch.object(bridge_start, "start") as start,
            ):
                result = bridge_start.restart()
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "bridge_stop_timeout")
            start.assert_not_called()

    def test_data_update_auto_launch_policy_uses_temporary_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            activation = Path(directory) / "_activated.json"
            with mock.patch.object(bridge_update, "ACTIVATION_PATH", activation):
                self.assertEqual(bridge_update._should_auto_launch(), (False, "not_activated"))
                activation.write_text(
                    json.dumps(
                        {
                            "activated_at": "2026-08-12T00:00:00+08:00",
                            "last_launch_attempt": None,
                            "consecutive_failures": 3,
                        }
                    ),
                    encoding="utf-8",
                )
                # A historical failure count is no longer a permanent lockout.
                self.assertEqual(bridge_update._should_auto_launch(), (True, "ok"))
                activation.write_text(
                    json.dumps(
                        {
                            "activated_at": "2026-08-12T00:00:00+08:00",
                            "last_launch_attempt": "2026-08-12T12:00:00+08:00",
                            "consecutive_failures": 3,
                            "blocked_until": "2026-08-12T12:15:00+08:00",
                        }
                    ),
                    encoding="utf-8",
                )
                now = bridge_update.datetime.fromisoformat("2026-08-12T12:10:00+08:00")
                self.assertEqual(bridge_update._should_auto_launch(now), (False, "backoff"))
                after = bridge_update.datetime.fromisoformat("2026-08-12T12:16:00+08:00")
                self.assertEqual(bridge_update._should_auto_launch(after), (True, "ok"))
                activation.write_text(
                    json.dumps(
                        {
                            "activated_at": "2026-08-12T00:00:00+08:00",
                            "last_launch_attempt": "2026-08-12T12:00:00+08:00",
                            "consecutive_failures": 1,
                        }
                    ),
                    encoding="utf-8",
                )
                now = bridge_update.datetime.fromisoformat("2026-08-12T12:00:30+08:00")
                self.assertEqual(bridge_update._should_auto_launch(now), (False, "cooldown"))

    def test_configured_but_deactivated_bridge_is_a_healthy_inactive_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input_data.md"
            manifest_path = root / "module.yaml"
            manifest_path.write_text((MODULE_ROOT / "module.yaml").read_text("utf-8"), "utf-8")
            state = {"initialized": True, "configured": True, "missing": []}
            with (
                mock.patch.object(bridge_update, "INPUT_PATH", input_path),
                mock.patch.object(bridge_update, "MANIFEST_PATH", manifest_path),
                mock.patch.object(bridge_update, "ACTIVATION_PATH", root / "_activated.json"),
                mock.patch.object(bridge_update, "load_ready_config", return_value=({"host": "127.0.0.1", "port": 8742, "session_secret": "test-internal-secret"}, state)),
                mock.patch.object(bridge_update.start_expand, "status") as status,
            ):
                result = bridge_update.update()
            self.assertTrue(result["ok"])
            self.assertEqual(result["status"], "inactive")
            self.assertFalse(result["active"])
            status.assert_not_called()
            manifest = yaml.safe_load(manifest_path.read_text("utf-8"))
            self.assertFalse(manifest["input_open"])
            self.assertEqual(manifest["health"], "正常")

    def test_corrupt_activation_file_is_reported_as_abnormal_without_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input_data.md"
            manifest_path = root / "module.yaml"
            activation = root / "_activated.json"
            manifest_path.write_text((MODULE_ROOT / "module.yaml").read_text("utf-8"), "utf-8")
            activation.write_text("{not-json", encoding="utf-8")
            state = {"initialized": True, "configured": True, "missing": []}
            with (
                mock.patch.object(bridge_update, "INPUT_PATH", input_path),
                mock.patch.object(bridge_update, "MANIFEST_PATH", manifest_path),
                mock.patch.object(bridge_update, "ACTIVATION_PATH", activation),
                mock.patch.object(bridge_update, "load_ready_config", return_value=({"host": "127.0.0.1", "port": 8742, "session_secret": "test-internal-secret"}, state)),
                mock.patch.object(bridge_update.start_expand, "status") as status,
                mock.patch.object(bridge_update.start_expand, "execute") as execute,
            ):
                result = bridge_update.update()

            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "activation_error")
            self.assertEqual(result["diagnosis"]["error_code"], "activation_file_invalid_json")
            status.assert_not_called()
            execute.assert_not_called()
            manifest = yaml.safe_load(manifest_path.read_text("utf-8"))
            self.assertFalse(manifest["input_open"])
            self.assertEqual(manifest["health"], "异常")
            output = input_path.read_text(encoding="utf-8")
            self.assertIn("激活记录异常", output)
            self.assertIn("不会检查、启动或恢复桥接服务", output)

    def test_data_update_records_failed_auto_launch_without_deleting_intent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            activation = Path(directory) / "_activated.json"
            activation.write_text(
                json.dumps(
                    {
                        "activated_at": "2026-08-12T00:00:00+08:00",
                        "last_launch_attempt": None,
                        "consecutive_failures": 0,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_update, "ACTIVATION_PATH", activation),
                mock.patch.object(
                    bridge_update.start_expand,
                    "execute",
                    return_value={"ok": False, "error": "bridge_port_not_ready"},
                ),
            ):
                launched, reason = bridge_update._auto_launch()
            self.assertFalse(launched)
            self.assertEqual(reason, "bridge_port_not_ready")
            saved = json.loads(activation.read_text(encoding="utf-8"))
            self.assertEqual(saved["consecutive_failures"], 1)
            self.assertTrue(saved["last_launch_attempt"])
            self.assertEqual(saved["last_error"], "bridge_port_not_ready")
            self.assertTrue(saved["blocked_until"])

    def test_data_update_does_not_count_environment_conflicts_as_crashes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            activation = root / "_activated.json"
            activation.write_text(
                json.dumps(
                    {
                        "activated_at": "2026-08-12T00:00:00+08:00",
                        "last_launch_attempt": None,
                        "consecutive_failures": 2,
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(bridge_update, "ACTIVATION_PATH", activation),
                mock.patch.object(
                    bridge_start,
                    "LIFECYCLE_LOCK_PATH",
                    str(root / "lifecycle.lock"),
                ),
                mock.patch.object(
                    bridge_update.start_expand,
                    "execute",
                    return_value={"ok": False, "error": "bridge_port_in_use"},
                ),
            ):
                launched, reason = bridge_update._auto_launch()
            self.assertFalse(launched)
            self.assertEqual(reason, "bridge_port_in_use")
            saved = json.loads(activation.read_text(encoding="utf-8"))
            self.assertEqual(saved["consecutive_failures"], 2)
            self.assertEqual(saved["last_error"], "bridge_port_in_use")
            self.assertTrue(saved["blocked_until"])

    def test_data_update_offline_output_contains_readable_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input_data.md"
            manifest_path = root / "module.yaml"
            activation = root / "_activated.json"
            manifest_path.write_text(
                (MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            activation.write_text(
                json.dumps(
                    {
                        "activated_at": "2026-08-12T00:00:00+08:00",
                        "last_launch_attempt": "2026-08-12T12:00:00+08:00",
                        "consecutive_failures": 1,
                        "last_error": "bridge_start_crashed",
                        "last_error_at": "2026-08-12T12:00:00+08:00",
                        "blocked_until": "2099-08-12T12:01:00+08:00",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "initialized": True,
                "configured": True,
                "missing": [],
                "host": "0.0.0.0",
                "port": 8742,
                "upstream_configured": True,
                "enabled_users": 1,
            }
            with (
                mock.patch.object(bridge_update, "INPUT_PATH", input_path),
                mock.patch.object(bridge_update, "MANIFEST_PATH", manifest_path),
                mock.patch.object(bridge_update, "ACTIVATION_PATH", activation),
                mock.patch.object(bridge_update, "load_ready_config", return_value=({"host": "0.0.0.0", "port": 8742, "session_secret": "test-internal-secret"}, state)),
                mock.patch.object(
                    bridge_update.start_expand,
                    "status",
                    return_value={"active": False, "unmanaged_process": False},
                ),
            ):
                result = bridge_update.update()
            self.assertFalse(result["ok"])
            self.assertEqual(result["diagnosis"]["error_code"], "bridge_start_crashed")
            output = input_path.read_text(encoding="utf-8")
            self.assertIn("桥接启动进程提前退出", output)
            self.assertIn("下次允许重试", output)

    def test_existing_local_account_password_and_binding_survive_password_reset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_auth.UserStore(Path(directory) / "users.json", 100_000)
            store.set_password("mobile-user", "long-enough-password")
            self.assertTrue(store.verify("mobile-user", "long-enough-password"))
            self.assertFalse(store.verify("mobile-user", "wrong-password"))
            accounts = json.loads(store.path.read_text("utf-8"))
            accounts["mobile-user"]["agent_user"] = "alice"
            store.path.write_text(json.dumps(accounts), "utf-8")
            store.set_password("mobile-user", "replacement-password")
            self.assertTrue(store.verify("mobile-user", "replacement-password"))
            self.assertEqual(store.agent_user("mobile-user"), "alice")

    def test_sse_stream_has_unbounded_read_timeout_but_rest_stays_bounded(self) -> None:
        client = bridge_upstream.UpstreamClient(
            {"upstream": "http://127.0.0.1:1457", "request_timeout": 30}
        )
        try:
            self.assertEqual(client.request_timeout, 30)
            self.assertEqual(client.client.timeout.read, 30)
            self.assertIsNone(client._sse_timeout.read)  # noqa: SLF001 - transport contract
            self.assertEqual(client._sse_timeout.connect, 5)  # noqa: SLF001
        finally:
            asyncio.run(client.close())

    def test_device_action_queue_is_targeted_validated_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_device_commands.DeviceCommandStore(Path(directory) / "commands.json")
            command = store.enqueue(
                username="mobile-user",
                device_id="phone-1",
                action="alarm.create",
                arguments={"hour": 8, "minute": 30, "label": "起床"},
                ttl_seconds=300,
            )
            self.assertEqual(command["action"], "alarm.create")
            self.assertEqual(len(store.pending_for("mobile-user", "phone-1")), 1)
            self.assertEqual(store.pending_for("mobile-user", "tablet-1"), [])
            updated = store.update(
                command["command_id"],
                username="mobile-user",
                device_id="phone-1",
                status="presented",
                detail={"surface": "system_ui"},
            )
            self.assertEqual(updated["status"], "presented")
            self.assertEqual(updated["detail"]["surface"], "system_ui")
            self.assertEqual(store.pending_for("mobile-user", "phone-1"), [])

            repeated = store.update(
                command["command_id"],
                username="mobile-user",
                device_id="phone-1",
                status="presented",
            )
            self.assertEqual(repeated["status"], "presented")
            with self.assertRaisesRegex(ValueError, "终态"):
                store.update(
                    command["command_id"],
                    username="mobile-user",
                    device_id="phone-1",
                    status="completed",
                )

            another = store.enqueue(
                username="mobile-user",
                device_id="phone-1",
                action="timer.start",
                arguments={"duration_seconds": 60},
            )
            received = store.update(
                another["command_id"],
                username="mobile-user",
                device_id="phone-1",
                status="received",
            )
            self.assertEqual(received["status"], "received")
            completed = store.update(
                another["command_id"],
                username="mobile-user",
                device_id="phone-1",
                status="completed",
            )
            self.assertEqual(completed["status"], "completed")

    def test_device_action_queue_serializes_multiple_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.json"
            errors: list[BaseException] = []

            def enqueue(worker: int) -> None:
                store = bridge_device_commands.DeviceCommandStore(path)
                for index in range(20):
                    try:
                        store.enqueue(
                            username="mobile-user",
                            device_id=f"phone-{worker}",
                            action="timer.start",
                            arguments={"duration_seconds": index + 1},
                        )
                    except BaseException as exc:
                        errors.append(exc)

            workers = [threading.Thread(target=enqueue, args=(index,)) for index in range(6)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()

            self.assertEqual(errors, [])
            state = bridge_device_commands.DeviceCommandStore(path)._read()
            self.assertEqual(len(state["commands"]), 120)

    def test_device_action_status_is_user_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "commands.json"
            store = bridge_device_commands.DeviceCommandStore(path)
            command = store.enqueue(
                username="mobile-user",
                device_id="phone-1",
                action="timer.start",
                arguments={"duration_seconds": 60},
            )
            with mock.patch.object(bridge_start, "DEVICE_COMMAND_PATH", str(path)):
                own = bridge_start.device_action_status(
                    {"command_id": command["command_id"]},
                    context={"user": "mobile-user"},
                )
                other = bridge_start.device_action_status(
                    {"command_id": command["command_id"]},
                    context={"user": "other-user"},
                )
            self.assertTrue(own["ok"])
            self.assertFalse(other["ok"])
            self.assertEqual(other["error"], "command_not_found")

    def test_device_action_rejects_unsafe_or_invalid_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = bridge_device_commands.DeviceCommandStore(Path(directory) / "commands.json")
            with self.assertRaises(ValueError):
                store.enqueue(
                    username="mobile-user",
                    device_id="phone-1",
                    action="android.intent.arbitrary",
                    arguments={},
                )
            with self.assertRaises(ValueError):
                store.enqueue(
                    username="mobile-user",
                    device_id="phone-1",
                    action="timer.start",
                    arguments={"duration_seconds": 0},
                )

    def test_device_action_stdin_preserves_framework_user_context(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "expand" / "global" / "kemo_bridge"
            target.mkdir(parents=True)
            (root / "users" / "mobile-user").mkdir(parents=True)
            for file in MODULE_ROOT.glob("*.py"):
                shutil.copyfile(file, target / file.name)
            peers = bridge_peer_store.PeerStore(target / "_bridge_peers.sqlite3", root / "users")
            device = peers.create("device", "mobile-user")
            request = {
                "command": "device_action",
                "params": {
                    "user": "forged-user",
                    "device_id": device["peer_id"],
                    "action": "timer.start",
                    "arguments": {"duration_seconds": 60},
                },
                "context": {"user": "mobile-user", "scope": "global", "module": "kemo_bridge"},
            }
            process = subprocess.run(
                [sys.executable, str(target / "start_expand.py")],
                input=json.dumps(request),
                capture_output=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1"},
                timeout=15,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            payload = json.loads(process.stdout)
            self.assertTrue(payload["ok"], payload)
            self.assertEqual(payload["device_id"], device["peer_id"])
            store = bridge_device_commands.DeviceCommandStore(target / "_device_commands.json")
            self.assertIsNotNone(store.get(payload["command_id"], username="mobile-user"))
            self.assertIsNone(store.get(payload["command_id"], username="forged-user"))

    def test_device_action_does_not_accept_user_from_untrusted_params(self) -> None:
        result = bridge_start.execute(
            "device_action",
            {
                "user": "forged-user",
                "device_id": "phone-1",
                "action": "timer.start",
                "arguments": {"duration_seconds": 60},
            },
            context={},
        )
        self.assertEqual(result["error"], "missing_user")


if __name__ == "__main__":
    unittest.main()
