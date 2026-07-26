from __future__ import annotations

import json
import threading
import time
import tempfile
import unittest
from pathlib import Path

from plugins.expand_call.tool import run as call_expand
from plugins.expand_creater.tool import run as create_expand
from run.expand_runtime import ExpandRuntimeError, invoke_expand, read_expand_runtime
from run.module_runtime import (
    ModuleRuntimeCancelled,
    ModuleRuntimeTimeout,
    run_module_updater,
)


class ExpandCallPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "config").mkdir()
        (self.root / "config" / "global_config.json").write_text("{}", "utf-8")
        (self.root / "users" / "alice").mkdir(parents=True)
        (self.root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
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
                "def execute(command, params=None):\n"
                "    child = \"import time; from pathlib import Path; "
                "time.sleep(1); Path('orphan.txt').write_text('late', 'utf-8')\"\n"
                "    subprocess.Popen([sys.executable, '-c', child])\n"
                "    time.sleep(5)\n"
                "    return {'ok': True}\n"
            )
        )
        with self.assertRaises(ModuleRuntimeTimeout):
            invoke_expand(
                root=self.root,
                user="alice",
                scope="user",
                module="flexible_data",
                command="slow",
                params={},
                timeout=0.2,
            )
        time.sleep(1.2)
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
