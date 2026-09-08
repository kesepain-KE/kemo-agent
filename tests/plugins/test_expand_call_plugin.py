from __future__ import annotations

import json
import os
import shutil
import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.expand_call.tool import run as call_expand
from plugins.expand_creater.tool import run as create_expand
from run.extensions import (
    ExpandOperationError,
    ExpandRuntimeError,
    invoke_expand,
    read_expand_runtime,
)
from run.extensions import (
    ModuleRuntimeCancelled,
    ModuleRuntimeTimeout,
    run_module_updater,
)
from run.infra.process_identity import process_identity_matches, process_snapshot
from run.tools import ToolProcessError, discover_tools, execute_tool


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ExpandCallPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "global_config.json").write_text("{}", "utf-8")
        (self.root / "users" / "alice").mkdir(parents=True)
        (self.root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        shutil.copytree(
            PROJECT_ROOT / "plugins" / "expand_call",
            self.root / "plugins" / "expand_call",
        )
        self.context = {
            "root": str(self.root),
            "user": "alice",
            "source": "test",
            "tool_timeout": 10,
        }

    def _create(self, *, scope: str = "user", code: str) -> Path:
        created = create_expand(
            "create",
            scope,
            name="flexible_data",
            explain="测试结构化数据和文件产物。",
            injection="测试拓展可用。",
            operations="调用 echo，返回任意数据和文件产物。",
            start_expand=code,
            context=self.context,
        )
        return self.root / created["path"]

    def test_canonical_stdin_call_returns_data_and_publishes_artifact(self) -> None:
        module = self._create(
            code=(
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                "    target = Path(__file__).resolve().parent / 'artifacts' / '结果.txt'\n"
                "    target.parent.mkdir(exist_ok=True)\n"
                "    target.write_text(params['text'], encoding='utf-8')\n"
                "    return {'ok': True, 'data': {'command': command, 'nested': params}, "
                "'artifacts': [{'path': 'artifacts/结果.txt', 'kind': 'file'}]}\n"
            )
        )
        input_before = (module / "input_data.md").read_bytes()
        result = call_expand(
            "user",
            "flexible_data",
            "echo",
            {"text": '中文、换行\n和引号"', "items": [1, {"x": True}]},
            context=self.context,
        )
        self.assertEqual(result["result"]["data"]["command"], "echo")
        self.assertEqual(result["result"]["data"]["nested"]["items"][1]["x"], True)
        self.assertEqual(len(result["artifacts"]), 1)
        artifact = result["artifacts"][0]
        self.assertEqual(artifact["scope"], "download")
        downloaded = self.root / artifact["project_path"]
        self.assertEqual(downloaded.read_text("utf-8"), '中文、换行\n和引号"')
        self.assertEqual((module / "input_data.md").read_bytes(), input_before)
        runtime = read_expand_runtime(module)
        self.assertEqual(runtime["control"]["status"], "completed")
        self.assertEqual(runtime["control"]["last_command"], "echo")

    def test_legacy_single_object_execute_is_supported(self) -> None:
        self._create(
            code=(
                "def execute(command):\n"
                "    return {'ok': True, 'data': {'action': command['action'], "
                "'value': command['value']}}\n"
            )
        )
        result = call_expand(
            "user",
            "flexible_data",
            "legacy",
            {"value": 42},
            context=self.context,
        )
        self.assertEqual(result["result"]["data"], {"action": "legacy", "value": 42})

    def test_execute_receives_trusted_call_context(self) -> None:
        self._create(code=(
            "def execute(command, params=None, *, context=None):\n"
            "    return {'ok': True, 'context': context}\n"
        ))
        result = invoke_expand(
            root=self.root,
            user="alice",
            scope="user",
            module="flexible_data",
            command="context",
            params={},
            timeout=10,
        )
        self.assertEqual(result["result"]["context"], {
            "user": "alice", "scope": "user", "module": "flexible_data"
        })

    def test_module_process_uses_private_env_without_framework_secrets(self) -> None:
        module = self._create(code=(
            "import os\n"
            "def execute(command, params=None):\n"
            "    return {'ok': True, 'configured': bool(os.getenv('EXPAND_API_KEY')), "
            "'provider_visible': bool(os.getenv('KEMO_API_KEY')), "
            "'web_base_url': os.getenv('KEMO_AGENT_WEB_BASE_URL'), "
            "'has_path': bool(os.getenv('PATH'))}\n"
        ))
        (module / ".env").write_text(
            "EXPAND_API_KEY=module-owned\n"
            "KEMO_AGENT_WEB_BASE_URL=http://stale.invalid:1\n",
            "utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "KEMO_API_KEY": "framework-provider-secret",
                "EXPAND_API_KEY": "parent-value-must-not-win",
                "KEMO_AGENT_WEB_BASE_URL": "http://127.0.0.1:24680",
            },
            clear=False,
        ):
            result = invoke_expand(
                root=self.root,
                user="alice",
                scope="user",
                module="flexible_data",
                command="environment",
                params={},
                timeout=10,
            )["result"]

        self.assertTrue(result["configured"])
        self.assertFalse(result["provider_visible"])
        self.assertTrue(result["has_path"])
        self.assertEqual(result["web_base_url"], "http://127.0.0.1:24680")

    def test_structured_domain_failure_keeps_the_original_error(self) -> None:
        self._create(
            code=(
                "def execute(command, params=None):\n"
                "    return {'ok': False, 'initialized': 1, 'failed': 1, 'domains': [\n"
                "        {'domain_id': 'global:knowledge', 'ok': True},\n"
                "        {'domain_id': 'user:alice:history', 'ok': False, "
                "         'status': 'failed', 'error': 'HTTP 422 INVALID_PARAM: scope'}\n"
                "    ]}\n"
            )
        )

        with self.assertRaisesRegex(
            ExpandRuntimeError,
            r"user:alice:history: HTTP 422 INVALID_PARAM: scope",
        ):
            call_expand(
                "user",
                "flexible_data",
                "initialize",
                context=self.context,
            )

    def test_retryable_metadata_is_preserved_for_safe_read_command(self) -> None:
        created = self._create(
            code=(
                "class TemporaryFailure(RuntimeError):\n"
                "    category = 'upstream_error'\n"
                "    status_code = 503\n"
                "    retryable = True\n"
                "    retry_after_ms = 900\n"
                "def execute(command, params=None):\n"
                "    raise TemporaryFailure('temporary graph outage')\n"
            )
        )
        module = self.root / "global_expand" / "kemo_graph"
        module.parent.mkdir(parents=True)
        shutil.move(str(created), str(module))

        definition = discover_tools(self.root, "alice").get("expand_call")
        with self.assertRaises(ToolProcessError) as raised:
            execute_tool(
                definition,
                {
                    "scope": "global",
                    "module": "kemo_graph",
                    "command": "query",
                },
                context=self.context,
                timeout=10,
            )

        self.assertEqual(raised.exception.category, "upstream_error")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after_ms, 900)

    def test_untrusted_or_mutating_expand_command_cannot_enable_whole_call_retry(
        self,
    ) -> None:
        self._create(
            code=(
                "def execute(command, params=None):\n"
                "    return {'ok': False, 'error': {\n"
                "        'message': 'write outcome unknown',\n"
                "        'category': 'upstream_error',\n"
                "        'status_code': 503,\n"
                "        'retryable': True,\n"
                "        'retry_after_ms': 700,\n"
                "    }}\n"
            )
        )

        for command in ("query", "ingest"):
            with self.subTest(command=command):
                with self.assertRaises(ExpandOperationError) as raised:
                    call_expand(
                        "user",
                        "flexible_data",
                        command,
                        context=self.context,
                    )

                self.assertEqual(raised.exception.category, "upstream_error")
                self.assertEqual(raised.exception.status_code, 503)
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(raised.exception.retry_after_ms, 700)

    def test_library_failure_metadata_is_aggregated_before_safe_read_retry(self) -> None:
        created = self._create(
            code=(
                "def execute(command, params=None):\n"
                "    rows = [{'library_id': 'public', 'ok': False, "
                "'status': 'error', 'error': 'temporary outage', "
                "'category': 'upstream_error', 'status_code': 503, "
                "'retryable': True, 'retry_after_ms': 500}]\n"
                "    if (params or {}).get('mixed'):\n"
                "        rows.append({'library_id': 'private', 'ok': False, "
                "'status': 'source_unavailable', 'error': 'source missing'})\n"
                "    return {'ok': False, 'retryable': True, 'libraries': rows}\n"
            )
        )
        module = self.root / "global_expand" / "kemo_graph"
        module.parent.mkdir(parents=True)
        shutil.move(str(created), str(module))

        with self.assertRaises(ExpandOperationError) as retryable:
            call_expand(
                "global",
                "kemo_graph",
                "status",
                context=self.context,
            )
        self.assertIn("public: temporary outage", str(retryable.exception))
        self.assertEqual(retryable.exception.category, "upstream_error")
        self.assertEqual(retryable.exception.status_code, 503)
        self.assertTrue(retryable.exception.retryable)
        self.assertEqual(retryable.exception.retry_after_ms, 500)

        with self.assertRaises(ExpandOperationError) as mixed:
            call_expand(
                "global",
                "kemo_graph",
                "status",
                {"mixed": True},
                context=self.context,
            )
        self.assertIn("private: source missing", str(mixed.exception))
        self.assertFalse(mixed.exception.retryable)

        with self.assertRaises(ExpandOperationError) as non_whitelisted:
            call_expand(
                "global",
                "kemo_graph",
                "libraries",
                context=self.context,
            )
        self.assertFalse(non_whitelisted.exception.retryable)

    def test_shared_allowlist_and_artifact_boundary_are_enforced(self) -> None:
        module = self._create(
            scope="shared",
            code="def execute(command, params=None):\n    return {'ok': True}\n",
        )
        config_path = self.root / "users" / "alice" / "user_config.json"
        config_path.write_text(
            json.dumps({"expand": {"shared_whitelist": ["another"]}}),
            "utf-8",
        )
        with self.assertRaisesRegex(ExpandRuntimeError, "白名单"):
            call_expand(
                "shared",
                "flexible_data",
                "echo",
                context=self.context,
            )

        config_path.write_text("{}", "utf-8")
        (module / "start_expand.py").write_text(
            "def execute(command, params=None):\n"
            "    return {'ok': True, 'artifacts': [{'path': '../expand.json'}]}\n",
            "utf-8",
        )
        with self.assertRaisesRegex(ExpandRuntimeError, "artifact"):
            call_expand(
                "shared",
                "flexible_data",
                "echo",
                context=self.context,
            )

    def test_partial_artifact_publication_is_rolled_back(self) -> None:
        self._create(
            code=(
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                "    root = Path(__file__).resolve().parent\n"
                "    (root / 'artifacts').mkdir(exist_ok=True)\n"
                "    (root / 'artifacts' / 'first.txt').write_text('first', encoding='utf-8')\n"
                "    return {'ok': True, 'artifacts': ["
                "{'path': 'artifacts/first.txt'}, {'path': '../expand.json'}]}\n"
            )
        )
        with self.assertRaisesRegex(ExpandRuntimeError, "artifact"):
            call_expand(
                "user",
                "flexible_data",
                "publish",
                context=self.context,
            )
        download = self.root / "users" / "alice" / "download"
        self.assertEqual(list(download.iterdir()), [])

    def test_timeout_terminates_the_expand_process_tree(self) -> None:
        module = self._create(
            code=(
                "import subprocess, sys, time\n"
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                "    child = \"import time; from pathlib import Path; "
                "time.sleep(10); Path('orphan.txt').write_text('late', 'utf-8')\"\n"
                "    process = subprocess.Popen([sys.executable, '-c', child])\n"
                "    Path('child.pid').write_text(str(process.pid), encoding='ascii')\n"
                "    time.sleep(10)\n"
                "    return {'ok': True}\n"
            )
        )
        errors: list[BaseException] = []

        def invoke() -> None:
            try:
                invoke_expand(
                    root=self.root,
                    user="alice",
                    scope="user",
                    module="flexible_data",
                    command="slow",
                    params={},
                    timeout=2.0,
                )
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=invoke)
        worker.start()
        pid_path = module / "child.pid"
        child_pid: int | None = None
        ready_deadline = time.monotonic() + 4.0
        while child_pid is None and worker.is_alive():
            try:
                raw_pid = pid_path.read_text("ascii").strip()
                child_pid = int(raw_pid) if raw_pid else None
            except (FileNotFoundError, OSError, UnicodeError, ValueError):
                child_pid = None
            if time.monotonic() >= ready_deadline:
                break
            time.sleep(0.01)

        # The old one-second marker assertion measured taskkill startup latency:
        # under concurrent CI load the child could write before taskkill finished,
        # even though no descendant remained after termination returned.  Observe
        # the actual child identity so this test checks the process-tree contract.
        self.assertIsNotNone(child_pid, "拓展子进程未在超时前报告后代 PID")
        assert child_pid is not None
        child_before = process_snapshot(child_pid)
        self.assertTrue(child_before.get("exists"), "后代进程未成功启动")

        worker.join(timeout=8.0)
        self.assertFalse(worker.is_alive(), "拓展调用超时后未及时返回")
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ModuleRuntimeTimeout)

        child_after = process_snapshot(child_pid)
        if child_before.get("identity_available"):
            self.assertIsNot(
                process_identity_matches(
                    child_after,
                    process_started_at=str(
                        child_before.get("process_started_at") or ""
                    ),
                    process_name=str(child_before.get("process_name") or ""),
                ),
                True,
                "拓展调用超时后原后代进程仍在运行",
            )
        else:
            self.assertFalse(
                child_after.get("exists"),
                "拓展调用超时后后代进程仍在运行",
            )
        self.assertFalse((module / "orphan.txt").exists())
        runtime = read_expand_runtime(module)
        self.assertEqual(runtime["control"]["status"], "failed")
        self.assertEqual(runtime["control"]["error"]["type"], "ModuleRuntimeTimeout")

    def test_emergency_cancel_terminates_the_expand_process(self) -> None:
        module = self._create(
            code=(
                "import time\n"
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                "    time.sleep(1)\n"
                "    Path('late.txt').write_text('late', encoding='utf-8')\n"
                "    return {'ok': True}\n"
            )
        )
        cancel_event = threading.Event()
        timer = threading.Timer(0.15, cancel_event.set)
        timer.start()
        try:
            with self.assertRaises(ModuleRuntimeCancelled):
                invoke_expand(
                    root=self.root,
                    user="alice",
                    scope="user",
                    module="flexible_data",
                    command="cancel",
                    params={},
                    timeout=5,
                    cancel_event=cancel_event,
                )
        finally:
            timer.cancel()
        time.sleep(1.0)
        self.assertFalse((module / "late.txt").exists())
        runtime = read_expand_runtime(module)
        self.assertEqual(runtime["control"]["error"]["type"], "ModuleRuntimeCancelled")

    def test_calls_to_the_same_module_are_serialized(self) -> None:
        module = self._create(
            code=(
                "import time\n"
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                "    root = Path(__file__).resolve().parent\n"
                "    running = root / 'running.flag'\n"
                "    if running.exists():\n"
                "        (root / 'overlap.flag').write_text('overlap', encoding='utf-8')\n"
                "    running.write_text(command, encoding='utf-8')\n"
                "    time.sleep(0.2)\n"
                "    running.unlink(missing_ok=True)\n"
                "    return {'ok': True, 'command': command}\n"
            )
        )
        results: list[dict[str, object]] = []
        errors: list[BaseException] = []

        def call(command: str) -> None:
            try:
                results.append(
                    invoke_expand(
                        root=self.root,
                        user="alice",
                        scope="user",
                        module="flexible_data",
                        command=command,
                        params={},
                        timeout=3,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        first = threading.Thread(target=call, args=("first",))
        second = threading.Thread(target=call, args=("second",))
        first.start()
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertFalse((module / "overlap.flag").exists())

    def test_update_and_control_share_the_same_execution_lock(self) -> None:
        common = (
            "    root = Path(__file__).resolve().parent\n"
            "    running = root / 'running.flag'\n"
            "    if running.exists():\n"
            "        (root / 'overlap.flag').write_text('overlap', encoding='utf-8')\n"
            "    running.write_text('running', encoding='utf-8')\n"
            "    time.sleep(0.2)\n"
            "    running.unlink(missing_ok=True)\n"
        )
        module = self._create(
            code=(
                "import time\n"
                "from pathlib import Path\n"
                "def execute(command, params=None):\n"
                + common
                + "    return {'ok': True}\n"
            )
        )
        updater = module / "data_update.py"
        updater.write_text(
            "import time\n"
            "from pathlib import Path\n"
            "def update():\n"
            + common
            + "    return {'ok': True}\n",
            "utf-8",
        )
        start = threading.Barrier(2)
        results: list[object] = []

        def update() -> None:
            start.wait(timeout=2)
            results.append(run_module_updater(updater, module, timeout=3))

        def control() -> None:
            start.wait(timeout=2)
            results.append(
                invoke_expand(
                    root=self.root,
                    user="alice",
                    scope="user",
                    module="flexible_data",
                    command="control",
                    params={},
                    timeout=3,
                )
            )

        update_thread = threading.Thread(target=update)
        control_thread = threading.Thread(target=control)
        update_thread.start()
        control_thread.start()
        update_thread.join(timeout=5)
        control_thread.join(timeout=5)

        self.assertFalse(update_thread.is_alive())
        self.assertFalse(control_thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertFalse((module / "overlap.flag").exists())


if __name__ == "__main__":
    unittest.main()
