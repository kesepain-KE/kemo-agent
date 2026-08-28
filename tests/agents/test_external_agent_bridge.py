from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.subagent_dispatch.tool import run as dispatch
from run.agents.external import (
    ExternalAgentError,
    call_external_agent,
    discover_external_agents,
)


class ExternalAgentBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in (
            self.root / "agents",
            self.root / "plugins",
            self.root / "shared_skills",
            self.root / "shared_knowledge",
            self.root / "global_knowledge",
            self.root / "global_expand",
            self.root / "shared_expand",
            self.root / "global_sense",
            self.root / "config",
            self.root / "users" / "alice" / "agents",
            self.root / "users" / "alice" / "expand",
            self.root / "users" / "alice" / "user_skills",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "provider": {
                    "type": "kemo",
                    "base_url": "http://127.0.0.1:1",
                    "model": "mock",
                        "stream": False,
                    },
                    "tools": {"timeout": 2},
                }
            ),
            "utf-8",
        )
        (self.root / "users" / "alice" / "user_config.json").write_text("{}", "utf-8")
        module = self.root / "users" / "alice" / "expand" / "remote_bridge"
        module.mkdir()
        (module / "expand.json").write_text(
            json.dumps(
                {
                    "name": "remote_bridge",
                    "explain": "external agent bridge",
                    "open_input": False,
                    "input_data": "input_data.md",
                    "input_health": "正常",
                    "start_update": "data_update.py",
                    "open_control": True,
                    "start_expand": "start_expand.py",
                    "start_control": "expand_control.md",
                }
            ),
            "utf-8",
        )
        (module / "input_data.md").write_text("unused", "utf-8")
        (module / "data_update.py").write_text("def update(): return None\n", "utf-8")
        (module / "expand_control.md").write_text(
            "## 注入层\n\n无\n\n## 操作层\n\n提供 external_agent_call。\n",
            "utf-8",
        )
        (module / "start_expand.py").write_text(
            "def execute(command, params):\n"
            "    if command != 'external_agent_call': raise ValueError('bad command')\n"
            "    return {'status': 'completed', 'data': {'answer': params['input']['value']}, 'model': 'remote-mock'}\n",
            "utf-8",
        )
        (module / "agent_bridge.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "agents": [
                        {
                            "name": "researcher",
                            "description": "外部研究智能体",
                            "command": "external_agent_call",
                            "input_schema": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                            "output_schema": {
                                "type": "object",
                                "properties": {"answer": {"type": "string"}},
                                "required": ["answer"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                }
            ),
            "utf-8",
        )
        self.handle = "external:user:remote_bridge:researcher"

    def test_discovery_and_dispatch_list_include_external_binding(self) -> None:
        bindings = discover_external_agents(self.root, "alice")
        self.assertEqual([item.handle for item in bindings], [self.handle])
        listed = dispatch("list", context={"root": str(self.root), "user": "alice"})
        self.assertEqual([item["name"] for item in listed["agents"]], [self.handle])
        self.assertEqual(listed["agents"][0]["source"], "external")

    def test_external_binding_calls_expand_and_validates_result(self) -> None:
        result = call_external_agent(
            self.root,
            "alice",
            self.handle,
            {"value": "from remote"},
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["source"], "external")
        self.assertEqual(result["data"], {"answer": "from remote"})
        self.assertEqual(result["model"], "remote-mock")

    def test_external_binding_rejects_background_call(self) -> None:
        with self.assertRaisesRegex(Exception, "只支持同步调用"):
            dispatch(
                "call",
                agent=self.handle,
                input={"value": "x"},
                wait=False,
                context={"root": str(self.root), "user": "alice"},
            )

    def test_external_binding_rejects_invalid_input_before_expand(self) -> None:
        with self.assertRaises(ExternalAgentError):
            call_external_agent(self.root, "alice", self.handle, {"value": 1})

    def test_external_binding_rejects_non_string_contract_fields(self) -> None:
        bridge_path = self.root / "users" / "alice" / "expand" / "remote_bridge" / "agent_bridge.json"
        bridge = json.loads(bridge_path.read_text("utf-8"))
        bridge["agents"][0]["description"] = 123
        bridge_path.write_text(json.dumps(bridge), "utf-8")
        self.assertEqual(discover_external_agents(self.root, "alice"), ())

    def test_external_binding_rejects_boolean_timeout(self) -> None:
        bridge_path = self.root / "users" / "alice" / "expand" / "remote_bridge" / "agent_bridge.json"
        bridge = json.loads(bridge_path.read_text("utf-8"))
        bridge["agents"][0]["timeout"] = True
        bridge_path.write_text(json.dumps(bridge), "utf-8")
        self.assertEqual(discover_external_agents(self.root, "alice"), ())

    def test_dispatch_rejects_boolean_timeout(self) -> None:
        with self.assertRaisesRegex(Exception, "timeout 必须是正数"):
            dispatch(
                "call",
                agent=self.handle,
                input={"value": "x"},
                timeout=True,
                context={"root": str(self.root), "user": "alice"},
            )

    def test_external_binding_rejects_non_terminal_result_status(self) -> None:
        entry = self.root / "users" / "alice" / "expand" / "remote_bridge" / "start_expand.py"
        entry.write_text(
            "def execute(command, params):\n"
            "    return {'status': 'running', 'data': {'answer': 'not finished'}}\n",
            "utf-8",
        )
        with self.assertRaisesRegex(ExternalAgentError, "未完成状态"):
            call_external_agent(self.root, "alice", self.handle, {"value": "x"})

    def test_dispatch_external_timeout_keeps_configured_survival_window(self) -> None:
        binding = SimpleNamespace(handle=self.handle, timeout=3_600.0)
        handle = self.handle
        submitted: dict[str, object] = {}

        class RecordingScheduler:
            def submit_callable(self, *args, **kwargs):
                submitted["args"] = args
                submitted["kwargs"] = kwargs
                return "agent-task-external"

            def wait(self, task_id, timeout=None):
                del task_id, timeout
                return {
                    "status": "completed",
                    "agent": handle,
                    "data": {"answer": "ok"},
                }

        scheduler = RecordingScheduler()
        config = {"agent_runtime": {"timeout_survival_seconds": 120}}
        with (
            patch(
                "plugins.subagent_dispatch.tool.resolve_external_agent",
                return_value=binding,
            ),
            patch("plugins.subagent_dispatch.tool.load_config", return_value=config),
            patch(
                "plugins.subagent_dispatch.tool.get_agent_scheduler",
                return_value=scheduler,
            ),
        ):
            result = dispatch(
                "call",
                agent=self.handle,
                input={"value": "x"},
                timeout=3_600,
                context={"root": str(self.root), "user": "alice"},
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(submitted["kwargs"]["timeout"], 3_720.0)

    def test_dispatch_external_rejects_timeout_above_contract_limit(self) -> None:
        with (
            patch(
                "plugins.subagent_dispatch.tool.resolve_external_agent",
                return_value=SimpleNamespace(handle=self.handle, timeout=600.0),
            ),
            patch("plugins.subagent_dispatch.tool.load_config", return_value={}),
        ):
            with self.assertRaisesRegex(Exception, "不能超过 3600 秒"):
                dispatch(
                    "call",
                    agent=self.handle,
                    input={"value": "x"},
                    timeout=3_601,
                    context={"root": str(self.root), "user": "alice"},
                )

    def test_non_terminal_result_does_not_publish_artifacts(self) -> None:
        entry = self.root / "users" / "alice" / "expand" / "remote_bridge" / "start_expand.py"
        entry.write_text(
            "from pathlib import Path\n"
            "def execute(command, params):\n"
            "    Path('partial.txt').write_text('partial', encoding='utf-8')\n"
            "    return {'status': 'running', 'data': {}, 'artifacts': [{'path': 'partial.txt'}]}\n",
            "utf-8",
        )

        with self.assertRaisesRegex(ExternalAgentError, "未完成状态"):
            call_external_agent(self.root, "alice", self.handle, {"value": "x"})

        self.assertFalse(
            (self.root / "users" / "alice" / "download" / "partial.txt").exists()
        )

    def test_invalid_output_schema_does_not_publish_artifacts(self) -> None:
        entry = self.root / "users" / "alice" / "expand" / "remote_bridge" / "start_expand.py"
        entry.write_text(
            "from pathlib import Path\n"
            "def execute(command, params):\n"
            "    Path('invalid.txt').write_text('invalid', encoding='utf-8')\n"
            "    return {'status': 'completed', 'data': {'wrong': True}, 'artifacts': [{'path': 'invalid.txt'}]}\n",
            "utf-8",
        )

        with self.assertRaisesRegex(ExternalAgentError, "输出不符合 Schema"):
            call_external_agent(self.root, "alice", self.handle, {"value": "x"})

        self.assertFalse(
            (self.root / "users" / "alice" / "download" / "invalid.txt").exists()
        )


if __name__ == "__main__":
    unittest.main()
