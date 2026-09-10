from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_versions  # noqa: E402


def run_check(root: Path, *argv: str) -> tuple[int, str]:
    with mock.patch.object(check_versions, "ROOT", root):
        buffer = io.StringIO()
        with mock.patch.object(sys, "stdout", buffer), mock.patch.object(
            sys, "stderr", buffer
        ):
            code = check_versions.main(list(argv))
        return code, buffer.getvalue()


def build_release_tree(root: Path, version: str = "1.2.7") -> None:
    """Create a minimal repository layout that satisfies every base check."""
    (root / "version.json").write_text(
        '{"version": "%s", "components": {"core": {"version": "%s"}}}'
        % (version, version),
        encoding="utf-8",
    )
    frontend = root / "web" / "frontend"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "package.json").write_text(
        '{"name": "kemo-agent-web", "version": "%s"}' % version, encoding="utf-8"
    )
    (frontend / "package-lock.json").write_text(
        '{"name": "kemo-agent-web", "version": "%s", '
        '"packages": {"": {"name": "kemo-agent-web", "version": "%s"}}}'
        % (version, version),
        encoding="utf-8",
    )
    badge = "img.shields.io/badge/version-%s-" % version
    (root / "readme.md").write_text(
        f"![v]({badge}blue)\n\n当前版本：`{version}`\n", encoding="utf-8"
    )
    (root / "README_EN.md").write_text(
        f"![v]({badge}blue)\n\nCurrent version: `{version}`\n", encoding="utf-8"
    )
    (root / "cli.py").write_text('VERSION = "%s"\n' % version, encoding="utf-8")
    knowledge = root / "global_knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    (knowledge / "project-introduction.md").write_text(
        f"当前稳定版本为 `{version}`。\n", encoding="utf-8"
    )
    (knowledge / "version-and-update-modules.md").write_text(
        '{"version": "%s"}\n' % version, encoding="utf-8"
    )
    (root / "agents.md").write_text(
        "当前稳定版本：`kemo-agent %s`，本版本重点加固 Chat 兼容传输链路。\n\n## 架构\n"
        % version,
        encoding="utf-8",
    )


class CheckVersionsTests(unittest.TestCase):
    def test_consistent_tree_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            code, output = run_check(root)
            self.assertEqual(code, 0, output)
            self.assertIn("通过：1.2.7", output)

    def test_agents_manual_version_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            (root / "agents.md").write_text(
                "当前稳定版本：`kemo-agent 1.2.6`，重点说明。\n\n## 架构\n",
                encoding="utf-8",
            )
            code, output = run_check(root)
            self.assertEqual(code, 1)
            self.assertIn("agents.md 运行手册稳定版本不一致", output)

    def test_agents_manual_missing_marker_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            (root / "agents.md").write_text("# kemo-agent 运行手册\n", encoding="utf-8")
            code, output = run_check(root)
            self.assertEqual(code, 1)
            self.assertIn("缺少「当前稳定版本", output)

    def test_agents_manual_summary_must_mention_release_focus(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            (root / "agents.md").write_text(
                "当前稳定版本：`kemo-agent 1.2.7`，本版本只是修正错别字。\n\n## 架构\n",
                encoding="utf-8",
            )
            code, output = run_check(root)
            self.assertEqual(code, 1)
            self.assertIn("运行手册版本摘要未包含 1.2.7 的重点", output)

    def test_agents_manual_release_focus_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            (root / "agents.md").write_text(
                "当前稳定版本：`kemo-agent 1.2.7`，本版本重点加固 Chat 兼容传输链路与 CI 稳定性。\n\n## 架构\n",
                encoding="utf-8",
            )
            code, output = run_check(root)
            self.assertEqual(code, 0, output)

    def test_tag_mismatch_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            code, output = run_check(root, "--tag", "v1.2.6")
            self.assertEqual(code, 1)
            self.assertIn("发布标签", output)


if __name__ == "__main__":
    unittest.main()
