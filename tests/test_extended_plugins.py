from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.file.tool import _ACTIONS as FILE_ACTIONS
from plugins.file.tool import run as run_file
from plugins.manifest import PluginManifestError, discover_plugin_manifests
from plugins.network.tool import run as run_network
from plugins.shell.tool import run as run_shell
from plugins.task_time.tool import run as run_task_time
from plugins.web_search.tool import run as run_web_search
from run.cron_store import CronStore
from run.tools import discover_tools, validate_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PluginManifestTests(unittest.TestCase):
    def test_repository_discovers_all_eleven_native_plugins(self) -> None:
        manifests = discover_plugin_manifests(PROJECT_ROOT)
        names = [manifest.tool["name"] for manifest in manifests]
        self.assertEqual(
            names,
            [
                "file",
                "get_current_time",
                "history_search",
                "memory_manage",
                "network",
                "shell",
                "skill_creater",
                "subagent_dispatch",
                "task_time",
                "web_search",
            ],
        )
        for manifest in manifests:
            self.assertEqual(manifest.descriptor.title, manifest.descriptor.path.parent.name)
            self.assertEqual(manifest.tool["name"], manifest.descriptor.path.parent.name)

        registry = discover_tools(PROJECT_ROOT, "alice")
        self.assertEqual(len(registry.tools), 10)
        shell_schema = registry.get("shell").input_schema
        validate_arguments(shell_schema, {"action": "run_command", "command": "pwd"})
        with self.assertRaises(Exception):
            validate_arguments(shell_schema, {"command": "pwd"})

    def test_more_than_one_tool_block_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plugins" / "duplicate"
            plugin.mkdir(parents=True)
            block = (
                "## Tool\n```json\n"
                '{"name":"duplicate","description":"x","input_schema":{"type":"object"},'
                '"version":"1","enabled":true,"entrypoint":"tool.py:run"}\n```'
            )
            (plugin / "SKILL.md").write_text(f"# duplicate\ndescription\n\n{block}\n\n{block}\n", "utf-8")
            with self.assertRaisesRegex(PluginManifestError, "只能声明一个"):
                discover_plugin_manifests(root)


class FilePluginTests(unittest.TestCase):
    def test_all_thirteen_actions_and_context_relative_paths(self) -> None:
        self.assertEqual(
            set(FILE_ACTIONS),
            {"read", "read_range", "write", "append", "edit", "list_dir", "tree_dir", "stat", "search", "copy", "move", "make_dir", "delete"},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root), "user": "alice"}
            self.assertTrue(run_file("make_dir", "docs", context=context)["ok"])
            self.assertTrue(run_file("write", "docs/a.txt", content="alpha\n", context=context)["ok"])
            self.assertTrue(run_file("append", "docs/a.txt", content="beta\n", context=context)["ok"])
            read = run_file("read", "docs/a.txt", context=context)
            self.assertEqual(read["content"], "alpha\nbeta\n")
            ranged = run_file("read_range", "docs/a.txt", start_line=2, context=context)
            self.assertEqual(ranged["lines"], ["beta"])
            edited = run_file(
                "edit",
                "docs/a.txt",
                edit_mode="replace_text",
                old_text="alpha",
                content="gamma",
                expected_count=1,
                create_backup=False,
                context=context,
            )
            self.assertTrue(edited["ok"])
            self.assertEqual(run_file("stat", "docs/a.txt", context=context)["type"], "file")
            self.assertEqual(run_file("list_dir", "docs", context=context)["count"], 1)
            self.assertIn("a.txt", run_file("tree_dir", "docs", context=context)["tree"])
            matches = run_file("search", "docs", query="gamma", context=context)
            self.assertEqual(matches["results"][0]["path"], "a.txt")
            self.assertTrue(run_file("copy", "docs/a.txt", dst_path="docs/b.txt", context=context)["ok"])
            self.assertTrue(run_file("move", "docs/b.txt", dst_path="docs/c.txt", context=context)["ok"])
            self.assertTrue(run_file("delete", "docs/c.txt", context=context)["ok"])
            self.assertFalse((root / "docs" / "c.txt").exists())


class ShellPluginTests(unittest.TestCase):
    def test_stdin_is_forwarded_to_subprocess(self) -> None:
        command = f'"{sys.executable}" -c "import sys;print(sys.stdin.read())"'
        result = run_shell(
            "run_command",
            command,
            stdin="hello-shell",
            context={"root": str(PROJECT_ROOT), "user": "alice"},
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("hello-shell", result["output"])

    def test_session_state_is_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alice_dir = root / "alice-dir"
            alice_dir.mkdir()
            alice = {"root": str(root), "user": "alice", "source": "cli"}
            bob = {"root": str(root), "user": "bob", "source": "cli"}
            changed = run_shell("run_command", f'cd "{alice_dir}"', session_id="same", context=alice)
            self.assertTrue(changed["ok"])
            self.assertEqual(Path(run_shell("run_command", "pwd", session_id="same", context=alice)["output"]), alice_dir)
            self.assertEqual(Path(run_shell("run_command", "pwd", session_id="same", context=bob)["output"]), root)


class NetworkPluginTests(unittest.TestCase):
    def test_empty_post_stays_post_and_uses_context_timeout(self) -> None:
        response = (201, {"Content-Type": "application/json"}, b'{"created":true}', False)
        with patch("plugins.network.tool._open", return_value=response) as opened:
            result = run_network(
                "post",
                "https://example.test/items",
                body="",
                context={"tool_timeout": 23},
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["body"], {"created": True})
        self.assertEqual(opened.call_args.kwargs["method"], "POST")
        self.assertEqual(opened.call_args.kwargs["data"], b"")
        self.assertEqual(opened.call_args.kwargs["timeout"], 23.0)

    def test_non_http_protocol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_network("get", "file:///etc/passwd", context={})


class TaskTimePluginTests(unittest.TestCase):
    def test_crud_and_title_only_update_preserves_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root), "user": "alice", "source": "cli", "session_id": "s1"}
            created = run_task_time(
                "create",
                title="heartbeat",
                prompt="ping",
                type="recurring",
                interval_seconds=300,
                context=context,
            )
            self.assertTrue(created["ok"])
            task_id = created["task"]["task_id"]
            updated = run_task_time("update", task_id=task_id, title="renamed", context=context)
            self.assertTrue(updated["ok"])
            self.assertEqual(updated["task"]["type"], "recurring")
            self.assertEqual(updated["task"]["interval_seconds"], 300)
            paused = run_task_time("update", task_id=task_id, status="paused", context=context)
            self.assertEqual(paused["task"]["status"], "paused")
            self.assertEqual(run_task_time("list", context=context)["total"], 1)
            stored = CronStore(root, "alice").read(task_id)
            self.assertEqual(stored["user"], "alice")
            self.assertNotIn("source", stored)
            self.assertNotIn("session_id", stored)
            self.assertTrue(run_task_time("delete", task_id=task_id, context=context)["deleted"])


class _FakeTavily:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"answer": "answer", "results": [{"title": "one", "content": "body"}], "images": []}

    def map(self, **kwargs):
        self.calls.append(("map", kwargs))
        return {"results": ["https://example.test/a"]}

    def research(self, **kwargs):
        self.calls.append(("research", kwargs))
        return {"request_id": "req-1", "status": "completed", "content": "report", "sources": []}


class WebSearchPluginTests(unittest.TestCase):
    def test_search_parses_domain_lists_and_propagates_timeout(self) -> None:
        client = _FakeTavily()
        with patch("plugins.web_search.tool._get_client", return_value=client):
            result = run_web_search(
                "search",
                query="kemo",
                include_domains="example.com, docs.example.com",
                context={"tool_timeout": 17},
            )
        self.assertTrue(result["ok"])
        params = client.calls[0][1]
        self.assertEqual(params["include_domains"], ["example.com", "docs.example.com"])
        self.assertEqual(params["timeout"], 17.0)

    def test_map_and_completed_research_use_current_sdk_shapes(self) -> None:
        client = _FakeTavily()
        with patch("plugins.web_search.tool._get_client", return_value=client):
            mapped = run_web_search("map", urls="https://example.test", context={})
            researched = run_web_search("research", input="topic", context={})
        self.assertEqual(mapped["urls"], ["https://example.test/a"])
        self.assertEqual(researched["status"], "completed")
        self.assertEqual(researched["report"], "report")


if __name__ == "__main__":
    unittest.main()
