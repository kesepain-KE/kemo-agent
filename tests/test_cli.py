from __future__ import annotations


import io
import json
import tempfile
import unittest
from pathlib import Path

import cli


class CLITests(unittest.TestCase):
    def make_root(self, *users: str) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        users_dir = Path(temporary.name) / "users"
        users_dir.mkdir()
        (users_dir / "_template").mkdir()
        for user in users:
            (users_dir / user).mkdir()
        self.addCleanup(temporary.cleanup)
        return temporary

    def test_discovers_only_local_user(self) -> None:
        root = Path(self.make_root("kesepain").name)
        self.assertEqual(cli.discover_user(None, root), "kesepain")

    def test_multiple_users_require_explicit_selection(self) -> None:
        root = Path(self.make_root("alice", "bob").name)
        with self.assertRaises(cli.CLIError):
            cli.discover_user(None, root)
        self.assertEqual(cli.discover_user("bob", root), "bob")

    def test_single_prompt_builds_stable_request(self) -> None:
        root = Path(self.make_root("kesepain").name)
        received: list[dict[str, str]] = []

        def handler(request: dict[str, str]) -> str:
            received.append(request)
            return "ok"

        stdout = io.StringIO()
        code = cli.main(
            ["--prompt", "hello", "--source", "cron", "--session", "job-1"],
            handler=handler,
            stdout=stdout,
            stderr=io.StringIO(),
            root=root,
        )

        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), "ok\n")
        self.assertEqual(
            received,
            [{"user": "kesepain", "prompt": "hello", "source": "cron", "session_id": "job-1"}],
        )

    def test_stdin_input(self) -> None:
        root = Path(self.make_root("kesepain").name)
        received: list[dict[str, str]] = []

        def handler(request: dict[str, str]) -> str:
            received.append(request)
            return "done"

        code = cli.main(
            ["--stdin"],
            handler=handler,
            stdin=io.StringIO("from pipe\n"),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
            root=root,
        )
        self.assertEqual(code, 0)
        self.assertEqual(received[0]["prompt"], "from pipe")

    def test_async_handler_and_json_output(self) -> None:
        root = Path(self.make_root("kesepain").name)

        async def handler(request: dict[str, str]) -> dict[str, object]:
            return {"text": "async ok", "user": request["user"]}

        stdout = io.StringIO()
        code = cli.main(
            ["hello", "world", "--output", "json"],
            handler=handler,
            stdout=stdout,
            stderr=io.StringIO(),
            root=root,
        )

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload, {"text": "async ok", "user": "kesepain"})

    def test_interactive_reuses_session_and_stops(self) -> None:
        root = Path(self.make_root("kesepain").name)
        received: list[dict[str, str]] = []

        def handler(request: dict[str, str]) -> str:
            received.append(request)
            return f"echo:{request['prompt']}"

        stdout = io.StringIO()
        code = cli.main(
            ["--interactive", "--session", "terminal-1"],
            handler=handler,
            stdin=io.StringIO("first\nsecond\n/exit\n"),
            stdout=stdout,
            stderr=io.StringIO(),
            root=root,
        )

        self.assertEqual(code, 0)
        self.assertEqual([item["prompt"] for item in received], ["first", "second"])
        self.assertTrue(all(item["session_id"] == "terminal-1" for item in received))
        self.assertEqual(stdout.getvalue(), "echo:first\necho:second\n")

    def test_interactive_session_commands(self) -> None:
        root = Path(self.make_root("kesepain").name)
        (root / "users" / "kesepain" / "history").mkdir()
        stdout = io.StringIO()
        code = cli.main(
            ["--interactive", "--session", "initial"],
            handler=lambda _: "unused",
            stdin=io.StringIO(
                "/new alpha\n/status\n/clear\n/sessions\n/history\n/use beta\n/status\n/exit\n"
            ),
            stdout=stdout,
            stderr=io.StringIO(),
            root=root,
        )
        self.assertEqual(code, 0)
        value = stdout.getvalue()
        self.assertIn("已新建并切换会话：alpha", value)
        self.assertIn("session=alpha", value)
        self.assertIn("已清空会话：alpha", value)
        self.assertIn("* alpha", value)
        self.assertIn("当前会话暂无历史", value)
        self.assertIn("已切换会话：beta", value)
        self.assertIn("session=beta", value)

    def test_interactive_memory_commands(self) -> None:
        root = Path(self.make_root("kesepain").name)
        (root / "config").mkdir()
        (root / "config" / "global_config.json").write_text(
            json.dumps(
                {
                    "provider": {
                        "type": "kemo",
                        "base_url": "http://127.0.0.1:1/v1",
                        "api_key_env": "UNUSED",
                        "model": "mock",
                    },
                    "memory": {
                        "tiers": {
                            "seven_days": {"days": 7, "upgrade_threshold": 3, "next": "one_month"},
                            "one_month": {"days": 30, "upgrade_threshold": 10, "next": "half_year"},
                            "half_year": {"days": 180, "upgrade_threshold": 60, "next": "permanent"},
                            "permanent": {"days": None, "upgrade_threshold": None, "next": None},
                        }
                    },
                }
            ),
            "utf-8",
        )
        (root / "users" / "kesepain" / "user_config.json").write_text("{}", "utf-8")
        stdout = io.StringIO()
        code = cli.main(
            ["--interactive"],
            handler=lambda _: "unused",
            stdin=io.StringIO(
                "/remember 用户喜欢川菜\n/memory\n/forget 川菜\n/memory\n/exit\n"
            ),
            stdout=stdout,
            stderr=io.StringIO(),
            root=root,
        )
        self.assertEqual(code, 0)
        value = stdout.getvalue()
        self.assertIn("已保存永久记忆", value)
        self.assertIn("permanent | weight=0 | 用户喜欢川菜", value)
        self.assertIn("已删除 1 条记忆", value)
        self.assertIn("暂无记忆", value)

    def test_handler_failure_returns_nonzero(self) -> None:
        root = Path(self.make_root("kesepain").name)

        def handler(_: dict[str, str]) -> str:
            raise RuntimeError("provider unavailable")

        stderr = io.StringIO()
        code = cli.main(
            ["--prompt", "hello"],
            handler=handler,
            stdout=io.StringIO(),
            stderr=stderr,
            root=root,
        )

        self.assertEqual(code, 1)
        self.assertIn("provider unavailable", stderr.getvalue())

    def test_rejects_conflicting_input_sources(self) -> None:
        root = Path(self.make_root("kesepain").name)
        stderr = io.StringIO()
        code = cli.main(
            ["message", "--prompt", "other"],
            handler=lambda _: "unused",
            stdout=io.StringIO(),
            stderr=stderr,
            root=root,
        )
        self.assertEqual(code, 2)
        self.assertIn("只能选择一种", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
