from __future__ import annotations

import io
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.file.tool import _ACTIONS as FILE_ACTIONS
from plugins.file.tool import run as run_file
from plugins.get_current_time.tool import run as run_get_current_time
from plugins.manifest import PluginManifestError, discover_plugin_manifests
from plugins.network.tool import _ACTIONS as NETWORK_ACTIONS
from plugins.network.tool import _open as open_network
from plugins.network.tool import _read_limited
from plugins.network.tool import run as run_network
from plugins.shell.tool import run as run_shell
from plugins.shell.tool import _decode_output
from plugins.task_time.tool import run as run_task_time
from plugins.web_search.tool import run as run_web_search
from run.cron_store import CronStore
from run.tools import discover_tools, validate_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PluginManifestTests(unittest.TestCase):
    def test_repository_discovers_all_fifteen_native_plugins(self) -> None:
        manifests = discover_plugin_manifests(PROJECT_ROOT)
        names = [manifest.tool["name"] for manifest in manifests]
        self.assertEqual(
            names,
            [
                "expand_creater",
                "external_message",
                "file",
                "get_current_time",
                "history_search",
                "memory_manage",
                "multimodal",
                "network",
                "sense_creater",
                "shell",
                "skill_creater",
                "subagent_dispatch",
                "task_plan",
                "task_time",
                "web_search",
            ],
        )
        for manifest in manifests:
            self.assertEqual(manifest.descriptor.title, manifest.descriptor.path.parent.name)
            self.assertEqual(manifest.tool["name"], manifest.descriptor.path.parent.name)

        registry = discover_tools(PROJECT_ROOT, "alice")
        self.assertEqual(len(registry.tools), 15)
        expand_schema = registry.get("expand_creater").input_schema
        self.assertEqual(
            set(expand_schema["properties"]["action"]["enum"]),
            {"list", "create", "validate"},
        )
        self.assertEqual(
            set(expand_schema["properties"]["scope"]["enum"]),
            {"user", "shared"},
        )
        sense_schema = registry.get("sense_creater").input_schema
        self.assertEqual(
            set(sense_schema["properties"]["action"]["enum"]),
            {"list", "create", "validate"},
        )
        self.assertNotIn("scope", sense_schema["properties"])
        shell_schema = registry.get("shell").input_schema
        validate_arguments(shell_schema, {"command": "pwd"})
        with self.assertRaises(Exception):
            validate_arguments(shell_schema, {"action": "run_command", "command": "pwd"})
        network_schema = registry.get("network").input_schema
        self.assertEqual(
            set(network_schema["properties"]["action"]["enum"]),
            {"get", "post", "put", "delete", "patch", "read"},
        )
        self.assertEqual(network_schema["properties"]["max_bytes"]["maximum"], 10_000_000)

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
    def test_all_fifteen_actions_and_context_relative_paths(self) -> None:
        self.assertEqual(
            set(FILE_ACTIONS),
            {"exists", "read", "read_range", "write", "append", "edit", "list_dir", "tree_dir", "stat", "search", "hash", "copy", "move", "make_dir", "delete"},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root), "user": "alice"}
            self.assertFalse(run_file("exists", "docs/a.txt", context=context)["exists"])
            self.assertTrue(run_file("make_dir", "docs", context=context)["ok"])
            self.assertTrue(run_file("write", "docs/a.txt", content="alpha\n", context=context)["ok"])
            self.assertTrue(run_file("append", "docs/a.txt", content="beta\n", context=context)["ok"])
            read = run_file("read", "docs/a.txt", context=context)
            self.assertEqual(read["content"], "alpha\nbeta\n")
            self.assertFalse(read["truncated"])
            self.assertNotIn("requested_path", read)
            self.assertEqual(run_file("exists", "docs/a.txt", context=context)["type"], "file")
            self.assertEqual(run_file("exists", "docs", context=context)["type"], "dir")
            ranged = run_file("read_range", "docs/a.txt", start_line=2, context=context)
            self.assertEqual(ranged["content"], ["beta"])
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

    def test_read_limits_tail_and_locale_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            content = "\n".join(f"line-{number:03d}" for number in range(100)) + "\n"
            (root / "large.log").write_text(content, "utf-8")
            limited = run_file("read", "large.log", max_bytes=12, context=context)
            self.assertTrue(limited["truncated"])
            self.assertEqual(limited["read_bytes"], 12)
            self.assertEqual(limited["size"], (root / "large.log").stat().st_size)

            (root / "unicode.txt").write_text("你你", "utf-8")
            unicode_limited = run_file("read", "unicode.txt", max_bytes=4, context=context)
            self.assertEqual(unicode_limited["content"], "你")
            self.assertEqual(unicode_limited["encoding"], "utf-8")

            tail = run_file("read_range", "large.log", tail=3, max_bytes=40, context=context)
            self.assertEqual(tail["content"], ["line-097", "line-098", "line-099"])
            self.assertTrue(tail["tail_mode"])
            self.assertTrue(tail["total_lines_estimated"] is False)

            japanese = "日本語のテキスト"
            (root / "locale.txt").write_bytes(japanese.encode("cp932"))
            with patch("plugins.file.tool.locale.getpreferredencoding", return_value="cp932"):
                decoded = run_file("read", "locale.txt", context=context)
            self.assertEqual(decoded["content"], japanese)
            self.assertEqual(decoded["encoding"], "cp932")

    def test_search_supports_source_files_and_reports_large_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            for filename in ("main.c", "lib.rs", "server.go", "build.sh", "Makefile", "icon.svg"):
                (root / filename).write_text("needle\n", "utf-8")
            (root / "image.png").write_bytes(b"needle")
            (root / "large.txt").write_text("needle-too-large", "utf-8")
            result = run_file("search", ".", query="needle", max_file_bytes=10, context=context)
            paths = {entry["path"] for entry in result["results"]}
            self.assertEqual(paths, {"main.c", "lib.rs", "server.go", "build.sh", "Makefile", "icon.svg"})
            self.assertEqual(result["skipped_large"], ["large.txt"])
            self.assertFalse(result["truncated"])

    def test_hash_copy_and_move_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            source = root / "source"
            source.mkdir()
            (source / "data.txt").write_bytes(b"abc")

            self.assertEqual(
                run_file("hash", "source/data.txt", algorithm="sha-256", context=context)["hash"],
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
            self.assertEqual(run_file("hash", "source/data.txt", algorithm="sha-1", context=context)["algorithm"], "sha1")
            self.assertEqual(run_file("hash", "source/data.txt", algorithm="md5", context=context)["algorithm"], "md5")
            with self.assertRaisesRegex(ValueError, "不支持的哈希算法"):
                run_file("hash", "source/data.txt", algorithm="crc32", context=context)

            refused = run_file("copy", "source", dst_path="copy", context=context)
            self.assertFalse(refused["ok"])
            self.assertIn("recursive=true", refused["error"])
            copied = run_file("copy", "source", dst_path="copy", recursive=True, context=context)
            self.assertEqual(copied["type"], "dir")
            self.assertEqual(copied["path"], str(source.resolve()))
            self.assertEqual(copied["dst_path"], str((root / "copy").resolve()))
            self.assertTrue((root / "copy" / "data.txt").is_file())

            moved = run_file("move", "copy", dst_path="moved", context=context)
            self.assertEqual(moved["type"], "dir")
            self.assertTrue((root / "moved" / "data.txt").is_file())
            self.assertFalse((root / "copy").exists())
            with self.assertRaisesRegex(ValueError, "自身子目录"):
                run_file("copy", "source", dst_path="source/child", recursive=True, context=context)
            with self.assertRaisesRegex(ValueError, "自身子目录"):
                run_file("move", "source", dst_path="source/child", context=context)

            (root / "same.txt").write_text("keep", "utf-8")
            with self.assertRaisesRegex(ValueError, "不能相同"):
                run_file("move", "same.txt", dst_path="same.txt", overwrite=True, context=context)
            self.assertEqual((root / "same.txt").read_text("utf-8"), "keep")

            delete_directory = run_file("delete", "moved", context=context)
            self.assertFalse(delete_directory["ok"])
            self.assertIn("shell 工具", delete_directory["error"])

    def test_file_manifest_exposes_optimized_parameters(self) -> None:
        registry = discover_tools(PROJECT_ROOT, "alice")
        schema = registry.get("file").input_schema
        self.assertEqual(set(schema["properties"]["action"]["enum"]), set(FILE_ACTIONS))
        for name in ("recursive", "max_bytes", "max_file_bytes", "algorithm"):
            self.assertIn(name, schema["properties"])
        self.assertEqual(schema["properties"]["max_bytes"]["maximum"], 536_870_912)


class CurrentTimePluginTests(unittest.TestCase):
    def test_default_is_beijing_and_target_uses_iana_timezone(self) -> None:
        result = run_get_current_time()
        utc = datetime.fromisoformat(result["utc"])
        local = datetime.fromisoformat(result["local"])
        self.assertEqual(utc, local)
        self.assertEqual(local.utcoffset(), timedelta(hours=8))
        self.assertEqual(result["iana_timezone"], "Asia/Shanghai")
        self.assertEqual(result["utc_offset"], "+0800")
        self.assertEqual(result["format"], "iso")
        self.assertNotIn("target", result)

        tokyo = run_get_current_time(target_timezone=" Asia/Tokyo ")
        self.assertEqual(datetime.fromisoformat(tokyo["utc"]), datetime.fromisoformat(tokyo["target"]))
        self.assertEqual(tokyo["target_timezone"], "Asia/Tokyo")
        self.assertEqual(tokyo["target_offset"], "+0900")

    def test_formats_and_validation(self) -> None:
        unix = run_get_current_time(target_timezone="Asia/Tokyo", format="unix")
        self.assertTrue(unix["utc"].isdigit())
        self.assertEqual(unix["utc"], unix["local"])
        self.assertEqual(unix["local"], unix["target"])

        date = run_get_current_time(format="date")
        self.assertRegex(date["utc"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(date["local"], r"^\d{4}-\d{2}-\d{2}$")
        clock = run_get_current_time(format="time")
        self.assertRegex(clock["utc"], r"^\d{2}:\d{2}:\d{2}$")
        self.assertRegex(clock["local"], r"^\d{2}:\d{2}:\d{2}$")

        with self.assertRaisesRegex(ValueError, "不支持的 format"):
            run_get_current_time(format="friendly")
        with self.assertRaisesRegex(ValueError, "无效的 IANA 时区名"):
            run_get_current_time(target_timezone="Mars/Olympus_Mons")
        with self.assertRaisesRegex(ValueError, "必须是 IANA 时区名字符串"):
            run_get_current_time(target_timezone=None)  # type: ignore[arg-type]

    def test_manifest_exposes_optional_parameters(self) -> None:
        definition = discover_tools(PROJECT_ROOT, "alice").get("get_current_time")
        self.assertEqual(definition.version, "1.1.0")
        schema = definition.input_schema
        self.assertEqual(set(schema["properties"]), {"target_timezone", "format"})
        self.assertEqual(schema["properties"]["format"]["enum"], ["iso", "unix", "date", "time"])
        validate_arguments(schema, {})
        validate_arguments(schema, {"target_timezone": "Europe/London", "format": "time"})
        with self.assertRaises(Exception):
            validate_arguments(schema, {"unknown": True})


class ShellPluginTests(unittest.TestCase):
    @staticmethod
    def _context(root: Path = PROJECT_ROOT, **values) -> dict:
        return {"root": str(root), "user": "alice", "source": "cli", "tool_timeout": 30, **values}

    def test_stdin_is_forwarded_to_subprocess(self) -> None:
        command = f'"{sys.executable}" -c "import sys;print(sys.stdin.read())"'
        result = run_shell(
            command,
            stdin="hello-shell",
            context=self._context(),
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("hello-shell", result["output"])

    def test_shell_process_tree_is_terminated_by_emergency_cancel(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            result = run_shell(
                f'"{sys.executable}" -c "import time;time.sleep(10)"',
                context=self._context(cancel_event=cancel),
            )
        finally:
            timer.cancel()
        self.assertFalse(result["ok"], result)
        self.assertTrue(result.get("cancelled"), result)
        self.assertLess(time.monotonic() - started, 3)

    def test_session_state_is_isolated_by_user_and_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            alice_dir = root / "alice-dir"
            alice_dir.mkdir()
            alice = self._context(root)
            alice_web = self._context(root, source="web")
            bob = self._context(root, user="bob")
            changed = run_shell(f'cd "{alice_dir}"', session_id="same", context=alice)
            self.assertTrue(changed["ok"])
            self.assertTrue(
                Path(run_shell("pwd", session_id="same", context=alice)["output"]).samefile(alice_dir)
            )
            self.assertTrue(
                Path(run_shell("pwd", session_id="same", context=alice_web)["output"]).samefile(root)
            )
            self.assertTrue(
                Path(run_shell("pwd", session_id="same", context=bob)["output"]).samefile(root)
            )

    def test_file_builtins_work_without_session_or_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hello.txt").write_text("你好", "utf-8")
            (root / "folder").mkdir()
            with patch("plugins.shell.tool.subprocess.run") as spawned:
                self.assertTrue(run_shell('mkdir "nested dir"', context=self._context(root))["ok"])
                self.assertEqual(run_shell("cat hello.txt", context=self._context(root))["output"], "你好")
                listing = run_shell("dir", context=self._context(root))["output"]
                self.assertIn("folder/", listing)
                self.assertIn("hello.txt", listing)
                self.assertEqual(run_shell("echo hello world", context=self._context(root))["output"], "hello world")
                refused = run_shell("rm folder", context=self._context(root))
                self.assertFalse(refused["ok"])
                self.assertIn("目标是目录", refused["output"])
                deleted = run_shell("del hello.txt", context=self._context(root))
                self.assertTrue(deleted["ok"])
            spawned.assert_not_called()
            self.assertFalse((root / "hello.txt").exists())

    def test_decode_output_uses_system_locale_then_replacement(self) -> None:
        encoded = "日本語".encode("cp932")
        with patch("plugins.shell.tool.locale.getpreferredencoding", return_value="cp932"):
            self.assertEqual(_decode_output(encoded), "日本語")
        with patch("plugins.shell.tool.locale.getpreferredencoding", side_effect=RuntimeError):
            self.assertIn("�", _decode_output(b"\xff"))

    def test_shell_type_selects_explicit_interpreter(self) -> None:
        completed = SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)
        cases = {
            "auto": ("external-command", True),
            "cmd": (["cmd", "/c", "external-command"], False),
            "powershell": (["powershell", "-NoProfile", "-Command", "external-command"], False),
            "bash": (["bash", "-c", "external-command"], False),
            "bash_login": (["bash", "-l", "-c", "external-command"], False),
        }
        for shell_type, (expected_command, expected_shell) in cases.items():
            with self.subTest(shell_type=shell_type), patch(
                "plugins.shell.tool.subprocess.run", return_value=completed
            ) as spawned:
                result = run_shell("external-command", shell_type=shell_type, context=self._context())
                self.assertTrue(result["ok"])
                self.assertEqual(spawned.call_args.args[0], expected_command)
                self.assertEqual(spawned.call_args.kwargs["shell"], expected_shell)

    def test_shell_and_timeout_modes_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "shell_type"):
            run_shell("pwd", shell_type="fish", context=self._context())
        with self.assertRaisesRegex(ValueError, "chain_timeout_mode"):
            run_shell("pwd", chain_timeout_mode="forever", context=self._context())

    def test_chain_timeout_can_be_total_or_per_command(self) -> None:
        process_result = {"ok": True, "output": "ok", "exit_code": 0, "timed_out": False, "truncated": False}
        with patch("plugins.shell.tool._run_process", return_value=process_result) as process, patch(
            "plugins.shell.tool.time.monotonic", side_effect=[100.0, 101.0, 103.0]
        ):
            result = run_shell("one; two", timeout=10, chain_timeout_mode="total", context=self._context())
        self.assertTrue(result["ok"])
        self.assertEqual([call.kwargs["timeout"] for call in process.call_args_list], [9.0, 7.0])

        with patch("plugins.shell.tool._run_process", return_value=process_result) as process:
            result = run_shell("one; two", timeout=10, chain_timeout_mode="per_command", context=self._context())
        self.assertTrue(result["ok"])
        self.assertEqual([call.kwargs["timeout"] for call in process.call_args_list], [10.0, 10.0])

    def test_timeout_must_come_from_argument_or_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "tool_timeout"):
            run_shell("pwd", context={"root": str(PROJECT_ROOT), "user": "alice"})
        with self.assertRaisesRegex(ValueError, "tool_timeout"):
            run_shell("pwd", timeout=5, context={"root": str(PROJECT_ROOT)})
        self.assertTrue(run_shell("pwd", timeout=5, context=self._context())["ok"])


class NetworkPluginTests(unittest.TestCase):
    def test_all_six_actions_are_registered(self) -> None:
        self.assertEqual(set(NETWORK_ACTIONS), {"get", "post", "put", "delete", "patch", "read"})

    def test_rest_actions_forward_method_body_timeout_and_max_bytes(self) -> None:
        response = (201, {"Content-Type": "application/json"}, b'{"saved":true}', False, None)
        cases = {
            "get": ("GET", None),
            "post": ("POST", b"payload"),
            "put": ("PUT", b"payload"),
            "delete": ("DELETE", None),
            "patch": ("PATCH", b"payload"),
        }
        for action, (method, data) in cases.items():
            with self.subTest(action=action), patch("plugins.network.tool._open", return_value=response) as opened:
                result = run_network(
                    action,
                    "https://example.test/items",
                    body="payload",
                    max_bytes=3456,
                    context={"tool_timeout": 23},
                )
                self.assertTrue(result["ok"])
                self.assertEqual(result["body"], {"saved": True})
                self.assertEqual(result["truncated"], False)
                self.assertNotIn("response_truncated", result)
                self.assertEqual(opened.call_args.kwargs["method"], method)
                self.assertEqual(opened.call_args.kwargs["data"], data)
                self.assertEqual(opened.call_args.kwargs["timeout"], 23.0)
                self.assertEqual(opened.call_args.kwargs["max_bytes"], 3456)

    def test_http_and_connection_failures_have_uniform_error_fields(self) -> None:
        response = (404, {"Content-Type": "text/plain"}, b"missing", False, "Not Found")
        with patch("plugins.network.tool._open", return_value=response):
            result = run_network("get", "https://example.test/missing", context={"tool_timeout": 10})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Not Found")
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["content_type"], "text/plain")
        self.assertFalse(result["truncated"])

        with patch("plugins.network.tool._open", side_effect=ConnectionError("连接失败: refused")):
            result = run_network("delete", "https://example.test/items/1", context={"tool_timeout": 10})
        self.assertEqual(
            result,
            {
                "ok": False,
                "error": "连接失败: refused",
                "status": 0,
                "url": "https://example.test/items/1",
                "body": "",
                "content_type": "",
                "truncated": False,
            },
        )

    def test_read_returns_content_type_and_auto_falls_back_to_reader(self) -> None:
        direct = (200, {"Content-Type": "text/html; charset=utf-8"}, b"<html><body>short</body></html>", False, None)
        reader = (200, {"Content-Type": "text/markdown"}, b"# Article\n" + b"body " * 20, False, None)
        with patch("plugins.network.tool._open", side_effect=[direct, reader]):
            result = run_network("read", "https://example.test/article", context={"tool_timeout": 10})
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "reader:jina")
        self.assertEqual(result["content_type"], "text/markdown")
        self.assertFalse(result["truncated"])

    def test_read_failure_keeps_unified_fields(self) -> None:
        with patch("plugins.network.tool._open", side_effect=ConnectionError("offline")):
            result = run_network(
                "read",
                "https://example.test/article",
                strategy="direct",
                context={"tool_timeout": 10},
            )
        self.assertFalse(result["ok"])
        self.assertIn("offline", result["error"])
        self.assertEqual(result["text"], "")
        self.assertEqual(result["chars"], 0)
        self.assertEqual(result["content_type"], "")
        self.assertFalse(result["truncated"])

    def test_dynamic_response_limit_reads_only_one_extra_byte(self) -> None:
        response = SimpleNamespace(read=lambda size: b"x" * size)
        raw, truncated = _read_limited(response, 1234)
        self.assertEqual(len(raw), 1234)
        self.assertTrue(truncated)

    def test_open_extracts_http_error_reason_and_respects_limit(self) -> None:
        failure = urllib.error.HTTPError(
            "https://example.test/missing",
            404,
            "Not Found",
            {"Content-Type": "text/plain"},
            io.BytesIO(b"0123456789abcdef"),
        )
        with patch("plugins.network.tool.urllib.request.urlopen", side_effect=failure):
            status, headers, raw, truncated, reason = open_network(
                "https://example.test/missing",
                method="GET",
                timeout=10,
                max_bytes=10,
            )
        self.assertEqual(status, 404)
        self.assertEqual(headers["Content-Type"], "text/plain")
        self.assertEqual(raw, b"0123456789")
        self.assertTrue(truncated)
        self.assertEqual(reason, "Not Found")

    def test_timeout_must_be_present_in_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "tool_timeout"):
            run_network("get", "https://example.test", timeout=5, context={})

    def test_non_http_protocol_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            run_network("get", "file:///etc/passwd", context={"tool_timeout": 10})


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
    def test_missing_api_key_returns_actionable_configuration_error_without_network(self) -> None:
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False),
            patch("plugins.web_search.tool.HAS_TAVILY", True),
            patch("plugins.web_search.tool.TavilyClient") as client,
        ):
            with self.assertRaisesRegex(RuntimeError, r"\.env.*TAVILY_API_KEY.*重启智能体"):
                run_web_search("search", query="kemo", context={})
        client.assert_not_called()

    def test_missing_dependency_returns_installation_error_without_network(self) -> None:
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": "configured-for-test"}, clear=False),
            patch("plugins.web_search.tool.HAS_TAVILY", False),
            patch("plugins.web_search.tool.TavilyClient") as client,
        ):
            with self.assertRaisesRegex(RuntimeError, r"pip install tavily-python.*重启智能体"):
                run_web_search("search", query="kemo", context={})
        client.assert_not_called()

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
