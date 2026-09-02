from __future__ import annotations

import io
import hashlib
import json
import os
import shlex
import shutil
import subprocess
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
from run.tools.background_worker import MAX_LOG_BYTES, _capture_stream
from plugins.task_time.tool import run as run_task_time
from plugins.wait_for_condition.tool import run as run_wait_for_condition
from plugins.web_search.tool import run as run_web_search
from run.tools.background_jobs import (
    MAX_ACTIVE_BACKGROUND_JOBS_PER_USER,
    cancel_background_job,
    prepare_background_job,
    reconcile_background_job,
    update_background_job as persist_background_job,
)
from run.tools.background_worker import MAX_LOG_BYTES, _capture_stream
from run.scheduler import CronStore
from run.tools import discover_tools, validate_arguments


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PluginManifestTests(unittest.TestCase):
    def test_repository_discovers_all_eighteen_native_plugins(self) -> None:
        manifests = discover_plugin_manifests(PROJECT_ROOT)
        names = [manifest.tool["name"] for manifest in manifests]
        self.assertEqual(
            names,
            [
                "expand_call",
                "expand_creater",
                "external_message",
                "file",
                "get_current_time",
                "history_search",
                "kemo_graph",
                "memory_manage",
                "multimodal",
                "network",
                "sense_creater",
                "shell",
                "skill_creater",
                "subagent_dispatch",
                "task_plan",
                "task_time",
                "wait_for_condition",
                "web_search",
            ],
        )
        for manifest in manifests:
            self.assertEqual(
                manifest.descriptor.title, manifest.descriptor.path.parent.name
            )
            self.assertEqual(
                manifest.tool["name"], manifest.descriptor.path.parent.name
            )

        registry = discover_tools(PROJECT_ROOT, "alice")
        self.assertEqual(len(registry.tools), 18)
        expand_call = registry.get("expand_call")
        self.assertFalse(expand_call.strict)
        self.assertFalse(expand_call.openai_schema()["function"]["strict"])
        self.assertEqual(
            registry.get("subagent_dispatch").timeout_policy,
            "agent_runtime",
        )
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
            validate_arguments(shell_schema, {"command": "pwd", "unknown": True})
        network_schema = registry.get("network").input_schema
        self.assertEqual(
            set(network_schema["properties"]["action"]["enum"]),
            {"get", "post", "put", "delete", "patch", "read"},
        )
        self.assertEqual(
            network_schema["properties"]["max_bytes"]["maximum"], 10_000_000
        )
        file_schema = registry.get("file").input_schema
        self.assertEqual(
            set(file_schema["properties"]["edit_mode"]["enum"]),
            {
                "insert",
                "replace_line",
                "replace_range",
                "replace_text",
                "delete_line",
                "delete_range",
            },
        )
        self.assertIn("expected_old_text", file_schema["properties"])
        self.assertIn("expected_hash", file_schema["properties"])

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
            (plugin / "SKILL.md").write_text(
                f"# duplicate\ndescription\n\n{block}\n\n{block}\n", "utf-8"
            )
            with self.assertRaisesRegex(PluginManifestError, "只能声明一个"):
                discover_plugin_manifests(root)

    def test_plugin_strict_flag_must_be_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plugins" / "invalid_strict"
            plugin.mkdir(parents=True)
            (plugin / "tool.py").write_text("def run():\n    return {}\n", "utf-8")
            (plugin / "SKILL.md").write_text(
                "# invalid_strict\ndescription\n\n## Tool\n```json\n"
                '{"name":"invalid_strict","description":"x",'
                '"input_schema":{"type":"object"},"version":"1",'
                '"enabled":true,"strict":"false","entrypoint":"tool.py:run"}'
                "\n```\n",
                "utf-8",
            )
            with self.assertRaisesRegex(PluginManifestError, "strict 必须是布尔值"):
                discover_plugin_manifests(root)

    def test_plugin_timeout_grace_must_be_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = root / "plugins" / "invalid_grace"
            plugin.mkdir(parents=True)
            (plugin / "tool.py").write_text("def run():\n    return {}\n", "utf-8")
            (plugin / "SKILL.md").write_text(
                "# invalid_grace\ndescription\n\n## Tool\n```json\n"
                '{"name":"invalid_grace","description":"x",'
                '"input_schema":{"type":"object"},"version":"1",'
                '"enabled":true,"entrypoint":"tool.py:run",'
                '"timeout_grace_seconds":31}'
                "\n```\n",
                "utf-8",
            )
            with self.assertRaisesRegex(
                PluginManifestError,
                "timeout_grace_seconds 必须是 0..30",
            ):
                discover_plugin_manifests(root)


class FilePluginTests(unittest.TestCase):
    def test_write_returns_safe_snapshot_for_immediate_range_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            written = run_file(
                "write",
                "created.txt",
                content="alpha\nbeta\n",
                context=context,
            )
            target = root / "created.txt"
            self.assertEqual(
                written["sha256"], hashlib.sha256(target.read_bytes()).hexdigest()
            )
            self.assertTrue(written["sha256_complete"])
            self.assertEqual(
                written["lines"],
                [{"line": 1, "text": "alpha"}, {"line": 2, "text": "beta"}],
            )
            self.assertFalse(written["snapshot_truncated"])

            edited = run_file(
                "edit",
                target.name,
                edit_mode="replace_range",
                line=1,
                end_line=2,
                expected_old_text="\n".join(item["text"] for item in written["lines"]),
                expected_hash=written["sha256"],
                new_text="gamma\ndelta",
                create_backup=False,
                context=context,
            )
            self.assertTrue(edited["changed"])
            self.assertEqual(target.read_text("utf-8"), "gamma\ndelta\n")

            with self.assertRaisesRegex(ValueError, "write 返回的 lines/sha256"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="replace_line",
                    line=1,
                    expected_old_text="wrong",
                    new_text="value",
                    context=context,
                )

    def test_directory_actions_are_pageable_and_report_complete_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            target = root / "docs"
            target.mkdir()
            (target / "folder").mkdir()
            for name in ("a.txt", "b.txt", "c.txt", "d.txt"):
                (target / name).write_text(name, "utf-8")

            first = run_file("list_dir", "docs", offset=0, limit=2, context=context)
            self.assertEqual(first["action"], "list_dir")
            self.assertEqual(first["total"], 5)
            self.assertEqual(first["count"], 5)
            self.assertEqual(first["returned"], 2)
            self.assertTrue(first["has_more"])
            self.assertEqual(first["next_offset"], 2)
            second = run_file(
                "list_dir",
                "docs",
                offset=first["next_offset"],
                limit=10,
                context=context,
            )
            self.assertEqual(second["returned"], 3)
            self.assertFalse(second["has_more"])
            self.assertIsNone(second["next_offset"])

            tree_first = run_file(
                "tree_dir", "docs", max_depth=2, limit=2, context=context
            )
            self.assertEqual(tree_first["action"], "tree_dir")
            self.assertEqual(tree_first["total"], 5)
            self.assertEqual(tree_first["returned"], 2)
            self.assertEqual(len(tree_first["items"]), 2)
            self.assertTrue(tree_first["has_more"])
            self.assertIn("下一页 offset=2", tree_first["tree"])
            tree_second = run_file(
                "tree_dir",
                "docs",
                max_depth=2,
                offset=tree_first["next_offset"],
                limit=10,
                context=context,
            )
            self.assertEqual(tree_second["returned"], 3)
            self.assertFalse(tree_second["truncated"])

    def test_all_fifteen_actions_and_context_relative_paths(self) -> None:
        self.assertEqual(
            set(FILE_ACTIONS),
            {
                "exists",
                "read",
                "read_range",
                "write",
                "append",
                "edit",
                "list_dir",
                "tree_dir",
                "stat",
                "search",
                "hash",
                "copy",
                "move",
                "make_dir",
                "delete",
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root), "user": "alice"}
            self.assertFalse(
                run_file("exists", "docs/a.txt", context=context)["exists"]
            )
            self.assertTrue(run_file("make_dir", "docs", context=context)["ok"])
            self.assertTrue(
                run_file("write", "docs/a.txt", content="alpha\n", context=context)[
                    "ok"
                ]
            )
            self.assertTrue(
                run_file("append", "docs/a.txt", content="beta\n", context=context)[
                    "ok"
                ]
            )
            read = run_file("read", "docs/a.txt", context=context)
            self.assertEqual(read["content"], "alpha\nbeta\n")
            self.assertFalse(read["truncated"])
            self.assertNotIn("requested_path", read)
            self.assertEqual(
                run_file("exists", "docs/a.txt", context=context)["type"], "file"
            )
            self.assertEqual(run_file("exists", "docs", context=context)["type"], "dir")
            ranged = run_file("read_range", "docs/a.txt", start_line=2, context=context)
            self.assertEqual(ranged["content"], ["beta"])
            self.assertEqual(ranged["lines"], [{"line": 2, "text": "beta"}])
            self.assertEqual(
                ranged["sha256"],
                hashlib.sha256((root / "docs" / "a.txt").read_bytes()).hexdigest(),
            )
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
            self.assertEqual(
                run_file("stat", "docs/a.txt", context=context)["type"], "file"
            )
            self.assertEqual(run_file("list_dir", "docs", context=context)["count"], 1)
            self.assertIn(
                "a.txt", run_file("tree_dir", "docs", context=context)["tree"]
            )
            matches = run_file("search", "docs", query="gamma", context=context)
            self.assertEqual(matches["results"][0]["path"], "a.txt")
            self.assertTrue(
                run_file("copy", "docs/a.txt", dst_path="docs/b.txt", context=context)[
                    "ok"
                ]
            )
            self.assertTrue(
                run_file("move", "docs/b.txt", dst_path="docs/c.txt", context=context)[
                    "ok"
                ]
            )
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
            self.assertEqual(limited["sha256"], "")
            self.assertFalse(limited["sha256_complete"])

            (root / "unicode.txt").write_text("你你", "utf-8")
            unicode_limited = run_file(
                "read", "unicode.txt", max_bytes=4, context=context
            )
            self.assertEqual(unicode_limited["content"], "你")
            self.assertEqual(unicode_limited["encoding"], "utf-8")

            tail = run_file(
                "read_range", "large.log", tail=3, max_bytes=40, context=context
            )
            self.assertEqual(tail["content"], ["line-097", "line-098", "line-099"])
            self.assertTrue(tail["tail_mode"])
            self.assertTrue(tail["total_lines_estimated"] is False)
            self.assertEqual(tail["sha256"], "")
            self.assertFalse(tail["sha256_complete"])

            japanese = "日本語のテキスト"
            (root / "locale.txt").write_bytes(japanese.encode("cp932"))
            with patch(
                "plugins.file.tool.locale.getpreferredencoding", return_value="cp932"
            ):
                decoded = run_file("read", "locale.txt", context=context)
            self.assertEqual(decoded["content"], japanese)
            self.assertEqual(decoded["encoding"], "cp932")

    def test_edit_preserves_newline_styles_bom_and_untouched_regions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            cases = {
                "lf.txt": (b"alpha\nbeta\n", b"one\ntwo\n", "LF"),
                "crlf.txt": (b"alpha\r\nbeta\r\n", b"one\r\ntwo\r\n", "CRLF"),
                "cr.txt": (b"alpha\rbeta\r", b"one\rtwo\r", "CR"),
            }
            for filename, (source, expected, style) in cases.items():
                with self.subTest(filename=filename):
                    target = root / filename
                    target.write_bytes(source)
                    result = run_file(
                        "edit",
                        filename,
                        edit_mode="replace_text",
                        old_text="alpha\nbeta",
                        new_text="one\ntwo",
                        create_backup=False,
                        context=context,
                    )
                    self.assertEqual(target.read_bytes(), expected)
                    self.assertEqual(result["newline_style"], style)
                    self.assertTrue(result["changed"])

            mixed = root / "mixed.txt"
            mixed.write_bytes(b"a\r\nb\nc\rd")
            result = run_file(
                "edit",
                "mixed.txt",
                edit_mode="replace_text",
                old_text="b\nc",
                new_text="B\nC",
                create_backup=False,
                context=context,
            )
            self.assertEqual(mixed.read_bytes(), b"a\r\nB\nC\rd")
            self.assertEqual(result["newline_style"], "mixed")

            bom = root / "bom.txt"
            bom.write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r\n")
            run_file(
                "edit",
                "bom.txt",
                edit_mode="replace_text",
                old_text="alpha",
                new_text="ALPHA",
                create_backup=False,
                context=context,
            )
            self.assertEqual(bom.read_bytes(), b"\xef\xbb\xbfALPHA\r\nbeta\r\n")

    def test_line_and_range_edits_do_not_add_blank_lines_or_change_eof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}

            with_eof = root / "with-eof.txt"
            with_eof.write_bytes(b"A\r\nB\r\n")
            run_file(
                "edit",
                with_eof.name,
                edit_mode="replace_line",
                line=1,
                expected_old_text="A",
                new_text="X\n",
                create_backup=False,
                context=context,
            )
            self.assertEqual(with_eof.read_bytes(), b"X\r\nB\r\n")

            without_eof = root / "without-eof.txt"
            without_eof.write_bytes(b"A\nB")
            run_file(
                "edit",
                without_eof.name,
                edit_mode="replace_line",
                line=2,
                expected_old_text="B",
                new_text="X\n",
                create_backup=False,
                context=context,
            )
            self.assertEqual(without_eof.read_bytes(), b"A\nX")

            ranged = root / "range.txt"
            ranged.write_bytes(b"A\r\nB\r\nC\r\n")
            run_file(
                "edit",
                ranged.name,
                edit_mode="replace_range",
                line=1,
                end_line=2,
                expected_old_text="A\nB",
                new_text="X\nY\n",
                create_backup=False,
                context=context,
            )
            self.assertEqual(ranged.read_bytes(), b"X\r\nY\r\nC\r\n")

    def test_edit_new_text_compatibility_deletion_bounds_and_noop(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            target = root / "edit.txt"
            target.write_bytes(b"alpha beta\n")

            legacy = run_file(
                "edit",
                target.name,
                edit_mode="replace_text",
                old_text="alpha",
                content="ALPHA",
                create_backup=False,
                context=context,
            )
            self.assertTrue(legacy["changed"])
            deleted = run_file(
                "edit",
                target.name,
                edit_mode="replace_text",
                old_text=" beta",
                new_text="",
                create_backup=False,
                context=context,
            )
            self.assertEqual(target.read_bytes(), b"ALPHA\n")
            self.assertEqual(deleted["replacements"], 1)

            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "必须完全一致"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="replace_text",
                    old_text="ALPHA",
                    content="one",
                    new_text="two",
                    context=context,
                )
            with self.assertRaisesRegex(ValueError, "超出范围"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="insert",
                    line=1,
                    column=100,
                    new_text="x",
                    context=context,
                )
            self.assertEqual(target.read_bytes(), before)

            noop = run_file(
                "edit",
                target.name,
                edit_mode="replace_text",
                old_text="ALPHA",
                new_text="ALPHA",
                context=context,
            )
            self.assertFalse(noop["changed"])
            self.assertFalse(noop["backup_created"])
            self.assertFalse((root / "edit.txt.bak").exists())

    def test_guarded_line_edits_explicit_deletion_and_versioned_backups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            target = root / "guarded.txt"
            target.write_bytes(b"A\r\n\tB\r\nC\r\nD\r\n")

            snapshot = run_file(
                "read_range",
                target.name,
                start_line=2,
                end_line=3,
                context=context,
            )
            self.assertEqual(
                snapshot["lines"],
                [{"line": 2, "text": "\tB"}, {"line": 3, "text": "C"}],
            )
            before = target.read_bytes()
            with self.assertRaisesRegex(ValueError, "expected_old_text"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="replace_line",
                    line=2,
                    expected_old_text="B",
                    new_text="X",
                    context=context,
                )
            self.assertEqual(target.read_bytes(), before)

            target.write_bytes(before.replace(b"D", b"external"))
            with self.assertRaisesRegex(ValueError, "expected_hash 不匹配"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="insert",
                    line=2,
                    column=1,
                    expected_hash=snapshot["sha256"],
                    new_text="//",
                    context=context,
                )

            current = run_file("read", target.name, context=context)
            first = run_file(
                "edit",
                target.name,
                edit_mode="delete_line",
                line=2,
                expected_old_text="\tB",
                expected_hash=current["sha256"],
                context=context,
            )
            self.assertEqual(target.read_bytes(), b"A\r\nC\r\nexternal\r\n")
            self.assertTrue(Path(first["backup_path"]).name.endswith(".bak"))

            second = run_file(
                "edit",
                target.name,
                edit_mode="delete_range",
                line=2,
                end_line=3,
                expected_old_text="C\nexternal",
                context=context,
            )
            self.assertEqual(target.read_bytes(), b"A\r\n")
            self.assertTrue(Path(second["backup_path"]).name.endswith(".bak.1"))
            self.assertEqual(
                (root / "guarded.txt.bak").read_bytes(),
                before.replace(b"D", b"external"),
            )
            self.assertEqual(
                (root / "guarded.txt.bak.1").read_bytes(), b"A\r\nC\r\nexternal\r\n"
            )

            with self.assertRaisesRegex(ValueError, "delete_range"):
                run_file(
                    "edit",
                    target.name,
                    edit_mode="replace_range",
                    line=1,
                    expected_old_text="A",
                    new_text="",
                    context=context,
                )

    def test_atomic_edit_failure_keeps_original_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            target = root / "atomic.txt"
            target.write_bytes(b"before\r\n")
            with patch(
                "plugins.file.tool.os.replace", side_effect=OSError("replace failed")
            ):
                result = run_file(
                    "edit",
                    target.name,
                    edit_mode="replace_text",
                    old_text="before",
                    new_text="after",
                    create_backup=False,
                    context=context,
                )
            self.assertFalse(result["ok"])
            self.assertEqual(target.read_bytes(), b"before\r\n")
            self.assertEqual(list(root.glob(".atomic.txt.*.tmp")), [])

    def test_search_supports_source_files_and_reports_large_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            context = {"root": str(root)}
            for filename in (
                "main.c",
                "lib.rs",
                "server.go",
                "build.sh",
                "Makefile",
                "icon.svg",
            ):
                (root / filename).write_text("needle\n", "utf-8")
            (root / "image.png").write_bytes(b"needle")
            (root / "large.txt").write_text("needle-too-large", "utf-8")
            result = run_file(
                "search", ".", query="needle", max_file_bytes=10, context=context
            )
            paths = {entry["path"] for entry in result["results"]}
            self.assertEqual(
                paths,
                {"main.c", "lib.rs", "server.go", "build.sh", "Makefile", "icon.svg"},
            )
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
                run_file(
                    "hash", "source/data.txt", algorithm="sha-256", context=context
                )["hash"],
                "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            )
            self.assertEqual(
                run_file("hash", "source/data.txt", algorithm="sha-1", context=context)[
                    "algorithm"
                ],
                "sha1",
            )
            self.assertEqual(
                run_file("hash", "source/data.txt", algorithm="md5", context=context)[
                    "algorithm"
                ],
                "md5",
            )
            with self.assertRaisesRegex(ValueError, "不支持的哈希算法"):
                run_file("hash", "source/data.txt", algorithm="crc32", context=context)

            refused = run_file("copy", "source", dst_path="copy", context=context)
            self.assertFalse(refused["ok"])
            self.assertIn("recursive=true", refused["error"])
            copied = run_file(
                "copy", "source", dst_path="copy", recursive=True, context=context
            )
            self.assertEqual(copied["type"], "dir")
            self.assertEqual(copied["path"], str(source.resolve()))
            self.assertEqual(copied["dst_path"], str((root / "copy").resolve()))
            self.assertTrue((root / "copy" / "data.txt").is_file())

            moved = run_file("move", "copy", dst_path="moved", context=context)
            self.assertEqual(moved["type"], "dir")
            self.assertTrue((root / "moved" / "data.txt").is_file())
            self.assertFalse((root / "copy").exists())
            with self.assertRaisesRegex(ValueError, "自身子目录"):
                run_file(
                    "copy",
                    "source",
                    dst_path="source/child",
                    recursive=True,
                    context=context,
                )
            with self.assertRaisesRegex(ValueError, "自身子目录"):
                run_file("move", "source", dst_path="source/child", context=context)

            (root / "same.txt").write_text("keep", "utf-8")
            with self.assertRaisesRegex(ValueError, "不能相同"):
                run_file(
                    "move",
                    "same.txt",
                    dst_path="same.txt",
                    overwrite=True,
                    context=context,
                )
            self.assertEqual((root / "same.txt").read_text("utf-8"), "keep")

            delete_directory = run_file("delete", "moved", context=context)
            self.assertFalse(delete_directory["ok"])
            self.assertIn("shell 工具", delete_directory["error"])

    def test_file_manifest_exposes_optimized_parameters(self) -> None:
        registry = discover_tools(PROJECT_ROOT, "alice")
        schema = registry.get("file").input_schema
        self.assertEqual(set(schema["properties"]["action"]["enum"]), set(FILE_ACTIONS))
        for name in (
            "recursive",
            "max_bytes",
            "max_file_bytes",
            "algorithm",
            "new_text",
        ):
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
        self.assertEqual(
            datetime.fromisoformat(tokyo["utc"]),
            datetime.fromisoformat(tokyo["target"]),
        )
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
        self.assertEqual(
            schema["properties"]["format"]["enum"], ["iso", "unix", "date", "time"]
        )
        validate_arguments(schema, {})
        validate_arguments(
            schema, {"target_timezone": "Europe/London", "format": "time"}
        )
        with self.assertRaises(Exception):
            validate_arguments(schema, {"unknown": True})


class ShellPluginTests(unittest.TestCase):
    @staticmethod
    def _context(root: Path = PROJECT_ROOT, **values) -> dict:
        return {
            "root": str(root),
            "user": "alice",
            "source": "cli",
            "tool_timeout": 30,
            **values,
        }

    @staticmethod
    def _python_command(script: str) -> str:
        arguments = [sys.executable, "-c", script]
        return (
            subprocess.list2cmdline(arguments)
            if os.name == "nt"
            else shlex.join(arguments)
        )

    def test_shell_manifest_exposes_managed_background_actions(self) -> None:
        definition = discover_tools(PROJECT_ROOT, "alice").get("shell")
        schema = definition.input_schema
        self.assertEqual(definition.version, "1.4.0")
        self.assertEqual(schema["required"], [])
        self.assertEqual(
            schema["properties"]["action"]["enum"],
            ["run", "status", "cancel"],
        )
        self.assertIn("background", schema["properties"])
        self.assertIn("show_terminal", schema["properties"])
        self.assertIn("job_id", schema["properties"])
        validate_arguments(schema, {})
        validate_arguments(schema, {"action": "status", "job_id": "job_example"})
        validate_arguments(schema, {"command": "pwd", "show_terminal": True})

    def test_shell_hides_sync_console_by_default_and_allows_explicit_visibility(self) -> None:
        completed = SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)
        with (
            patch(
                "plugins.shell.tool.subprocess.run", return_value=completed
            ) as spawned,
            patch(
                "plugins.shell.tool.hidden_subprocess_kwargs",
                return_value={"mode": "hidden"},
            ) as hidden,
            patch(
                "plugins.shell.tool.visible_subprocess_kwargs",
                return_value={"mode": "visible"},
            ) as visible,
        ):
            run_shell("external-command", context=self._context())
            self.assertEqual(spawned.call_args.kwargs["mode"], "hidden")
            hidden.assert_called_once_with()
            visible.assert_not_called()

            run_shell(
                "external-command",
                show_terminal=True,
                context=self._context(),
            )
            self.assertEqual(spawned.call_args.kwargs["mode"], "visible")
            visible.assert_called_once_with()

    def test_shell_forwards_explicit_visibility_to_managed_background_command(self) -> None:
        with patch(
            "plugins.shell.tool._start_background",
            return_value={"ok": True, "status": "running"},
        ) as start_background:
            result = run_shell(
                "external-command",
                background=True,
                show_terminal=True,
                context=self._context(),
            )
        self.assertTrue(result["ok"])
        self.assertTrue(start_background.call_args.kwargs["show_terminal"])

    def test_managed_background_job_completes_without_persisting_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self._context(
                root,
                source="test",
                session_id="space-a",
                cancel_event=threading.Event(),
            )
            secret = "super-secret-command-argument"
            command = self._python_command(
                "import time; "
                f"secret={secret!r}; "
                "time.sleep(0.4); print('background-managed-ok')"
            )
            started = run_shell(command, background=True, context=context)
            self.assertTrue(started["ok"], started)
            self.assertRegex(started["job_id"], r"^job_[a-f0-9]{32}$")
            self.assertGreater(started["pid"], 0)
            self.assertTrue(started["process_started_at"])

            waited = run_wait_for_condition(
                "job_exit",
                10,
                check_interval=0.1,
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(waited["status"], "triggered")
            self.assertEqual(waited["observation"]["status"], "completed")
            self.assertEqual(waited["observation"]["exit_code"], 0)
            stdout_path = root / waited["observation"]["stdout_path"]
            self.assertIn("background-managed-ok", stdout_path.read_text("utf-8"))
            self.assertNotIn(str(root), json.dumps(waited["observation"], ensure_ascii=False))

            jobs_dir = root / "users" / "alice" / "runtime" / "background_jobs"
            record_path = jobs_dir / f"{started['job_id']}.json"
            request_path = jobs_dir / f"{started['job_id']}.request.json"
            self.assertNotIn(secret, record_path.read_text("utf-8"))
            self.assertFalse(request_path.exists())

            status = run_shell(
                action="status",
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(status["status"], "completed")

    def test_background_start_cancel_is_not_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            cancel = threading.Event()
            cancel.set()
            result = run_shell(
                self._python_command("import time; time.sleep(2)"),
                background=True,
                context=self._context(root, cancel_event=cancel),
            )
            self.assertFalse(result["ok"], result)
            self.assertIn(result["status"], {"cancelled", "cancelling"})

    def test_managed_background_job_enforces_explicit_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self._context(
                root,
                source="test",
                session_id="timeout-space",
                cancel_event=threading.Event(),
            )
            started_at = time.monotonic()
            started = run_shell(
                self._python_command("import time; time.sleep(4)"),
                background=True,
                timeout=1,
                context=context,
            )
            waited = run_wait_for_condition(
                "job_exit",
                8,
                check_interval=0.1,
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(waited["observation"]["status"], "failed")
            self.assertEqual(
                waited["observation"]["stop_reason"],
                "background_timeout",
            )
            self.assertLess(time.monotonic() - started_at, 3)
            self.assertFalse(waited["observation"].get("working_dir", "").startswith("/"))
            self.assertNotIn(":\\", waited["observation"].get("stdout_path", ""))

            foreign_context = {**context, "session_id": "space-b"}
            with self.assertRaisesRegex(KeyError, "后台作业不存在"):
                run_shell(
                    action="status",
                    job_id=started["job_id"],
                    context=foreign_context,
                )

    def test_managed_background_job_cancel_is_terminal_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self._context(
                root,
                source="test",
                session_id="space-a",
                cancel_event=threading.Event(),
            )
            started = run_shell(
                self._python_command("import time; time.sleep(30)"),
                background=True,
                context=context,
            )
            self.assertTrue(started["ok"], started)
            cancelled = run_shell(
                action="cancel",
                job_id=started["job_id"],
                context=context,
            )
            self.assertIn(cancelled["status"], {"cancelling", "cancelled"})
            waited = run_wait_for_condition(
                "job_exit",
                10,
                check_interval=0.1,
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(waited["observation"]["status"], "cancelled")
            repeated = run_shell(
                action="cancel",
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(repeated["status"], "cancelled")

    def test_persisted_cancel_refuses_unknown_process_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            record, _ = prepare_background_job(
                root,
                "alice",
                source="test",
                session_id="identity-space",
                working_dir=str(root),
                shell_type="auto",
                command_digest="digest",
            )

            def mark_running(current: dict) -> dict:
                current.update(
                    {
                        "status": "running",
                        "pid": 12345,
                        "process_started_at": "expected-start",
                        "process_name": "python.exe",
                    }
                )
                return current

            persist_background_job(root, "alice", record["job_id"], mark_running)
            with (
                patch(
                    "run.tools.background_jobs.process_snapshot",
                    return_value={
                        "pid": 12345,
                        "exists": True,
                        "query_status": "access_denied",
                        "identity_available": False,
                    },
                ),
                patch("run.tools.background_jobs.terminate_pid_tree") as terminate,
            ):
                cancelled = cancel_background_job(root, "alice", record["job_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(
                cancelled["error"]["code"],
                "background_identity_unknown",
            )
            terminate.assert_not_called()

    def test_persisted_cancel_refuses_missing_process_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            record, _ = prepare_background_job(
                root,
                "alice",
                source="test",
                session_id="missing-identity-space",
                working_dir=str(root),
                shell_type="auto",
                command_digest="digest",
            )
            persist_background_job(
                root,
                "alice",
                record["job_id"],
                lambda current: {
                    **current,
                    "status": "running",
                    "pid": 12345,
                    "process_started_at": "",
                    "process_name": "",
                },
            )
            with (
                patch(
                    "run.tools.background_jobs.process_snapshot",
                    return_value={
                        "pid": 12345,
                        "exists": True,
                        "query_status": "ok",
                    },
                ),
                patch("run.tools.background_jobs.terminate_pid_tree") as terminate,
            ):
                cancelled = cancel_background_job(root, "alice", record["job_id"])
            self.assertEqual(cancelled["status"], "cancelled")
            terminate.assert_not_called()

    def test_reconcile_expired_deadline_terminates_known_process_and_settles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            record, _ = prepare_background_job(
                root,
                "alice",
                source="test",
                session_id="deadline-space",
                working_dir=str(root),
                shell_type="auto",
                command_digest="digest",
                timeout_seconds=60,
            )
            persist_background_job(
                root,
                "alice",
                record["job_id"],
                lambda current: {
                    **current,
                    "status": "running",
                    "pid": 12345,
                    "process_started_at": "expected-start",
                    "process_name": "python.exe",
                    "deadline_at": 0.0,
                },
            )
            alive = True

            def snapshot(pid: int) -> dict:
                return {
                    "pid": pid,
                    "exists": alive if pid == 12345 else False,
                    "query_status": "ok",
                    "process_started_at": "expected-start" if pid == 12345 else "",
                    "process_name": "python.exe" if pid == 12345 else "",
                }

            def terminate(*args, **kwargs):
                nonlocal alive
                alive = False
                return True

            with (
                patch("run.tools.background_jobs.process_snapshot", side_effect=snapshot),
                patch("run.tools.background_jobs.terminate_pid_tree", side_effect=terminate),
            ):
                settled = reconcile_background_job(root, "alice", record["job_id"])
            self.assertEqual(settled["status"], "failed")
            self.assertEqual(settled["stop_reason"], "background_timeout")
            self.assertEqual(settled["error"]["code"], "background_timeout")

    def test_background_job_active_quota_rejects_excess_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            with patch(
                "run.tools.background_jobs.MAX_ACTIVE_BACKGROUND_JOBS_PER_USER",
                1,
            ):
                prepare_background_job(
                    root,
                    "alice",
                    source="test",
                    session_id="quota-a",
                    working_dir=str(root),
                    shell_type="auto",
                    command_digest="digest",
                )
                with self.assertRaisesRegex(ValueError, "活动作业数量"):
                    prepare_background_job(
                        root,
                        "alice",
                        source="test",
                        session_id="quota-b",
                        working_dir=str(root),
                        shell_type="auto",
                        command_digest="digest",
                    )

    def test_background_job_cleanup_removes_expired_terminal_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_root = root / "users" / "alice"
            user_root.mkdir(parents=True)
            record, paths = prepare_background_job(
                root,
                "alice",
                source="test",
                session_id="cleanup-old",
                working_dir=str(root),
                shell_type="auto",
                command_digest="digest",
            )
            persist_background_job(
                root,
                "alice",
                record["job_id"],
                lambda current: {
                    **current,
                    "status": "completed",
                    "finished_at": (datetime.now() - timedelta(days=8)).isoformat(),
                },
            )
            paths["stdout"].write_text("old", encoding="utf-8")
            with patch(
                "run.tools.background_jobs.BACKGROUND_JOB_RETENTION_SECONDS",
                60,
            ):
                prepare_background_job(
                    root,
                    "alice",
                    source="test",
                    session_id="cleanup-new",
                    working_dir=str(root),
                    shell_type="auto",
                    command_digest="digest",
                )
            self.assertFalse(paths["record"].exists())
            self.assertFalse(paths["stdout"].exists())

    def test_managed_background_job_nonzero_exit_is_a_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            context = self._context(
                root,
                source="test",
                session_id="space-a",
                cancel_event=threading.Event(),
            )
            started = run_shell(
                self._python_command("raise SystemExit(7)"),
                background=True,
                context=context,
            )
            waited = run_wait_for_condition(
                "job_exit",
                10,
                check_interval=0.1,
                job_id=started["job_id"],
                context=context,
            )
            self.assertEqual(waited["status"], "triggered")
            self.assertEqual(waited["observation"]["status"], "failed")
            self.assertEqual(waited["observation"]["exit_code"], 7)
            self.assertEqual(
                waited["observation"]["error"]["code"],
                "background_process_failed",
            )

    def test_background_worker_registration_failure_terminates_worker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            fake_worker = SimpleNamespace(pid=12345, poll=lambda: None)
            failed_record = {
                "job_id": "job_00000000000000000000000000000000",
                "status": "failed",
                "error": {"code": "background_worker_start_failed"},
            }
            with (
                patch("plugins.shell.tool.subprocess.Popen", return_value=fake_worker),
                patch(
                    "plugins.shell.tool.process_snapshot",
                    return_value={
                        "pid": 12345,
                        "exists": True,
                        "process_started_at": "started",
                        "process_name": "python.exe",
                    },
                ),
                patch(
                    "plugins.shell.tool.update_background_job",
                    side_effect=[RuntimeError("metadata write failed"), failed_record],
                ),
                patch("plugins.shell.tool.terminate_process_tree") as terminate,
            ):
                result = run_shell(
                    self._python_command("print('must-not-survive')"),
                    background=True,
                    context=self._context(root),
                )
            self.assertFalse(result["ok"])
            self.assertEqual(result["status"], "failed")
            terminate.assert_called_once_with(fake_worker)
            requests = list(
                (root / "users" / "alice" / "runtime" / "background_jobs").glob(
                    "*.request.json"
                )
            )
            self.assertEqual(requests, [])

    def test_background_start_error_does_not_overwrite_worker_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            fake_worker = SimpleNamespace(pid=12345, poll=lambda: None)
            calls = 0

            def update_with_race(
                update_root: Path,
                update_user: str,
                job_id: str,
                mutator,
            ):
                nonlocal calls
                calls += 1
                if calls == 1:
                    def complete(record: dict) -> dict:
                        record["status"] = "completed"
                        record["exit_code"] = 0
                        return record

                    persist_background_job(update_root, update_user, job_id, complete)
                    raise RuntimeError("metadata write failed after worker completion")
                return persist_background_job(update_root, update_user, job_id, mutator)

            with (
                patch("plugins.shell.tool.subprocess.Popen", return_value=fake_worker),
                patch(
                    "plugins.shell.tool.process_snapshot",
                    return_value={
                        "pid": 12345,
                        "exists": True,
                        "process_started_at": "started",
                        "process_name": "python.exe",
                    },
                ),
                patch(
                    "plugins.shell.tool.update_background_job",
                    side_effect=update_with_race,
                ),
                patch("plugins.shell.tool.terminate_process_tree"),
            ):
                result = run_shell(
                    self._python_command("print('already-finished')"),
                    background=True,
                    context=self._context(root),
                )
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["exit_code"], 0)

    def test_managed_background_job_rejects_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "users" / "alice").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "不支持 stdin"):
                run_shell(
                    self._python_command("print('unused')"),
                    background=True,
                    stdin="not-supported",
                    context=self._context(root),
                )

    def test_background_log_capture_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounded.log"
            _capture_stream(
                io.BytesIO(b"x" * (MAX_LOG_BYTES + 1)),
                path,
            )
            data = path.read_bytes()
            self.assertLessEqual(len(data), MAX_LOG_BYTES)
            self.assertIn("日志输出已截断".encode("utf-8"), data)

    def test_background_log_capture_drains_when_log_path_is_unwritable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "blocked"
            blocked.write_text("not a directory", encoding="utf-8")
            stream = io.BytesIO(b"x" * (MAX_LOG_BYTES + 1))
            _capture_stream(stream, blocked / "stdout.log")
            self.assertEqual(stream.tell(), MAX_LOG_BYTES + 1)

    def test_managed_background_job_rejects_runtime_directory_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            user_dir = root / "users" / "alice"
            user_dir.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            runtime = user_dir / "runtime"
            try:
                runtime.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("当前 Windows 环境不允许创建目录符号链接")
            with self.assertRaisesRegex(ValueError, "符号链接"):
                run_shell(
                    self._python_command("print('must-not-run')"),
                    background=True,
                    context=self._context(root),
                )

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
                Path(
                    run_shell("pwd", session_id="same", context=alice)["output"]
                ).samefile(alice_dir)
            )
            self.assertTrue(
                Path(
                    run_shell("pwd", session_id="same", context=alice_web)["output"]
                ).samefile(root)
            )
            self.assertTrue(
                Path(
                    run_shell("pwd", session_id="same", context=bob)["output"]
                ).samefile(root)
            )

    def test_file_builtins_work_without_session_or_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hello.txt").write_text("你好", "utf-8")
            (root / "folder").mkdir()
            with patch("plugins.shell.tool.subprocess.run") as spawned:
                self.assertTrue(
                    run_shell('mkdir "nested dir"', context=self._context(root))["ok"]
                )
                self.assertEqual(
                    run_shell("cat hello.txt", context=self._context(root))["output"],
                    "你好",
                )
                listing = run_shell("dir", context=self._context(root))["output"]
                self.assertIn("folder/", listing)
                self.assertIn("hello.txt", listing)
                self.assertEqual(
                    run_shell("echo hello world", context=self._context(root))[
                        "output"
                    ],
                    "hello world",
                )
                refused = run_shell("rm folder", context=self._context(root))
                self.assertFalse(refused["ok"])
                self.assertIn("目标是目录", refused["output"])
                deleted = run_shell("del hello.txt", context=self._context(root))
                self.assertTrue(deleted["ok"])
            spawned.assert_not_called()
            self.assertFalse((root / "hello.txt").exists())

    def test_decode_output_uses_system_locale_then_replacement(self) -> None:
        encoded = "日本語".encode("cp932")
        with patch(
            "plugins.shell.tool.locale.getpreferredencoding", return_value="cp932"
        ):
            self.assertEqual(_decode_output(encoded), "日本語")
        with patch(
            "plugins.shell.tool.locale.getpreferredencoding", side_effect=RuntimeError
        ):
            self.assertIn("�", _decode_output(b"\xff"))

    def test_shell_type_selects_explicit_interpreter(self) -> None:
        completed = SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)
        cases = {
            "auto": ("external-command", True),
            "cmd": (["cmd", "/c", "external-command"], False),
            "powershell": (
                ["powershell", "-NoProfile", "-NonInteractive", "-Command"],
                False,
            ),
            "pwsh": (
                ["pwsh", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command"],
                False,
            ),
            "bash": (["bash", "-c", "external-command"], False),
            "bash_login": (["bash", "-l", "-c", "external-command"], False),
        }
        for shell_type, (expected_command, expected_shell) in cases.items():
            with (
                self.subTest(shell_type=shell_type),
                patch(
                    "plugins.shell.tool.subprocess.run", return_value=completed
                ) as spawned,
            ):
                result = run_shell(
                    "external-command", shell_type=shell_type, context=self._context()
                )
                self.assertTrue(result["ok"])
                actual_command = spawned.call_args.args[0]
                if shell_type in {"powershell", "pwsh"}:
                    self.assertEqual(actual_command[:-1], expected_command)
                    self.assertIn("Set-StrictMode -Version Latest", actual_command[-1])
                    self.assertTrue(actual_command[-1].endswith("external-command"))
                else:
                    self.assertEqual(actual_command, expected_command)
                self.assertEqual(spawned.call_args.kwargs["shell"], expected_shell)

    def test_shell_and_timeout_modes_are_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "action"):
            run_shell("pwd", action="run_command", context=self._context())
        with self.assertRaisesRegex(ValueError, "shell_type"):
            run_shell("pwd", shell_type="fish", context=self._context())
        with self.assertRaisesRegex(ValueError, "chain_timeout_mode"):
            run_shell("pwd", chain_timeout_mode="forever", context=self._context())

    def test_native_command_chain_runs_in_one_interpreter_process(self) -> None:
        process_result = {
            "ok": True,
            "output": "ok",
            "exit_code": 0,
            "timed_out": False,
            "truncated": False,
        }
        for timeout_mode in ("total", "per_command"):
            with (
                self.subTest(timeout_mode=timeout_mode),
                patch(
                    "plugins.shell.tool._run_process", return_value=process_result
                ) as process,
            ):
                result = run_shell(
                    "one; two",
                    timeout=10,
                    chain_timeout_mode=timeout_mode,
                    context=self._context(),
                )
            self.assertTrue(result["ok"])
            process.assert_called_once()
            self.assertEqual(process.call_args.args[0], "one; two")
            self.assertEqual(process.call_args.kwargs["timeout"], 10.0)

    def test_windows_command_failures_include_platform_specific_hints(self) -> None:
        failure = {
            "ok": False,
            "output": "STDERR:\n命令不存在",
            "exit_code": 255,
            "timed_out": False,
            "truncated": False,
            "cwd": str(PROJECT_ROOT),
        }
        with (
            patch("plugins.shell.tool._is_windows", return_value=True),
            patch("plugins.shell.tool._execute", return_value=failure),
        ):
            head = run_shell(
                "Get-Content large.log | head -c 2000",
                shell_type="powershell",
                context=self._context(),
            )
        self.assertIn("Select-Object -First", head["hint"])
        self.assertEqual(head["output"], failure["output"])
        self.assertEqual(head["exit_code"], 255)

        missing_hash = {**failure, "output": "无法将 Get-FileHash 识别为 cmdlet"}
        with (
            patch("plugins.shell.tool._is_windows", return_value=True),
            patch("plugins.shell.tool._execute", return_value=missing_hash),
        ):
            hashed = run_shell(
                "Get-FileHash -LiteralPath data.bin",
                shell_type="powershell",
                context=self._context(),
            )
        self.assertIn("file 工具的 hash action", hashed["hint"])
        self.assertIn("certutil -hashfile", hashed["hint"])

    @unittest.skipUnless(
        os.name == "nt" and shutil.which("powershell"), "需要 Windows PowerShell"
    )
    def test_powershell_chain_preserves_variables_and_rejects_undefined_values(
        self,
    ) -> None:
        preserved = run_shell(
            '$a = 42; Write-Output "VAR_TEST a=$a"',
            shell_type="powershell",
            context=self._context(),
        )
        self.assertTrue(preserved["ok"], preserved)
        self.assertIn("VAR_TEST a=42", preserved["output"])

        rejected = run_shell(
            'Write-Output "DST=$destinationThatWasNeverAssigned"',
            shell_type="powershell",
            context=self._context(),
        )
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("destinationThatWasNeverAssigned", rejected["output"])

    @unittest.skipUnless(shutil.which("pwsh"), "需要 PowerShell 7")
    def test_pwsh_chain_preserves_variables(self) -> None:
        result = run_shell(
            '$a = 42; Write-Output "VAR_TEST a=$a"',
            shell_type="pwsh",
            context=self._context(),
        )
        self.assertTrue(result["ok"], result)
        self.assertIn("VAR_TEST a=42", result["output"])

    def test_timeout_must_come_from_argument_or_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "tool_timeout"):
            run_shell("pwd", context={"root": str(PROJECT_ROOT), "user": "alice"})
        with self.assertRaisesRegex(ValueError, "tool_timeout"):
            run_shell("pwd", timeout=5, context={"root": str(PROJECT_ROOT)})
        self.assertTrue(run_shell("pwd", timeout=5, context=self._context())["ok"])


class NetworkPluginTests(unittest.TestCase):
    def test_all_six_actions_are_registered(self) -> None:
        self.assertEqual(
            set(NETWORK_ACTIONS), {"get", "post", "put", "delete", "patch", "read"}
        )

    def test_rest_actions_forward_method_body_timeout_and_max_bytes(self) -> None:
        response = (
            201,
            {"Content-Type": "application/json"},
            b'{"saved":true}',
            False,
            None,
        )
        cases = {
            "get": ("GET", None),
            "post": ("POST", b"payload"),
            "put": ("PUT", b"payload"),
            "delete": ("DELETE", None),
            "patch": ("PATCH", b"payload"),
        }
        for action, (method, data) in cases.items():
            with (
                self.subTest(action=action),
                patch("plugins.network.tool._open", return_value=response) as opened,
            ):
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
            result = run_network(
                "get", "https://example.test/missing", context={"tool_timeout": 10}
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "Not Found")
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["content_type"], "text/plain")
        self.assertFalse(result["truncated"])

        with patch(
            "plugins.network.tool._open",
            side_effect=ConnectionError("连接失败: refused"),
        ):
            result = run_network(
                "delete", "https://example.test/items/1", context={"tool_timeout": 10}
            )
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
        direct = (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            b"<html><body>short</body></html>",
            False,
            None,
        )
        reader = (
            200,
            {"Content-Type": "text/markdown"},
            b"# Article\n" + b"body " * 20,
            False,
            None,
        )
        with patch("plugins.network.tool._open", side_effect=[direct, reader]):
            result = run_network(
                "read", "https://example.test/article", context={"tool_timeout": 10}
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["source"], "reader:jina")
        self.assertEqual(result["content_type"], "text/markdown")
        self.assertFalse(result["truncated"])

    def test_read_failure_keeps_unified_fields(self) -> None:
        with patch(
            "plugins.network.tool._open", side_effect=ConnectionError("offline")
        ):
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
            context = {
                "root": str(root),
                "user": "alice",
                "source": "cli",
                "session_id": "s1",
            }
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
            updated = run_task_time(
                "update", task_id=task_id, title="renamed", context=context
            )
            self.assertTrue(updated["ok"])
            self.assertEqual(updated["task"]["type"], "recurring")
            self.assertEqual(updated["task"]["interval_seconds"], 300)
            paused = run_task_time(
                "update", task_id=task_id, status="paused", context=context
            )
            self.assertEqual(paused["task"]["status"], "paused")
            self.assertEqual(run_task_time("list", context=context)["total"], 1)
            stored = CronStore(root, "alice").read(task_id)
            self.assertEqual(stored["user"], "alice")
            self.assertNotIn("source", stored)
            self.assertNotIn("session_id", stored)
            self.assertTrue(
                run_task_time("delete", task_id=task_id, context=context)["deleted"]
            )


class _FakeTavily:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {
            "answer": "answer",
            "results": [{"title": "one", "content": "body"}],
            "images": [],
        }

    def map(self, **kwargs):
        self.calls.append(("map", kwargs))
        return {"results": ["https://example.test/a"]}

    def research(self, **kwargs):
        self.calls.append(("research", kwargs))
        return {
            "request_id": "req-1",
            "status": "completed",
            "content": "report",
            "sources": [],
        }


class WebSearchPluginTests(unittest.TestCase):
    def test_missing_api_key_returns_actionable_configuration_error_without_network(
        self,
    ) -> None:
        with (
            patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False),
            patch("plugins.web_search.tool.HAS_TAVILY", True),
            patch("plugins.web_search.tool.TavilyClient") as client,
        ):
            with self.assertRaisesRegex(
                RuntimeError, r"\.env.*TAVILY_API_KEY.*重启智能体"
            ):
                run_web_search("search", query="kemo", context={})
        client.assert_not_called()

    def test_missing_dependency_returns_installation_error_without_network(
        self,
    ) -> None:
        with (
            patch.dict(
                os.environ, {"TAVILY_API_KEY": "configured-for-test"}, clear=False
            ),
            patch("plugins.web_search.tool.HAS_TAVILY", False),
            patch("plugins.web_search.tool.TavilyClient") as client,
        ):
            with self.assertRaisesRegex(
                RuntimeError, r"pip install tavily-python.*重启智能体"
            ):
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
