from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / ".github" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import check_versions  # noqa: E402


def run_check(
    root: Path, *argv: str, env: dict[str, str] | None = None
) -> tuple[int, str]:
    """Check a fixture tree, isolated from the caller's own CI tag context.

    发布工作流会在 tag 上导出 GITHUB_REF_TYPE/GITHUB_REF_NAME；如果让它们泄漏进
    fixture，检查器会把当前发布的标签和 fixture 里的示例版本作比较。
    """

    environment = {"GITHUB_REF_TYPE": "branch", "GITHUB_REF_NAME": "main"}
    if env:
        environment.update(env)
    with mock.patch.object(check_versions, "ROOT", root), mock.patch.dict(
        os.environ, environment
    ):
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

    def test_130_gateway_compatibility_markers_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root, "1.3.0")
            version_path = root / "version.json"
            manifest = json.loads(version_path.read_text(encoding="utf-8"))
            manifest["compatibility"] = {"kemo-adapter-api": "0.8.2"}
            version_path.write_text(json.dumps(manifest), encoding="utf-8")
            gateway_marker = "配套 kemo-adapter-api 0.8.2"
            (root / "readme.md").write_text(
                (root / "readme.md").read_text(encoding="utf-8")
                + f"\n{gateway_marker}\n",
                encoding="utf-8",
            )
            (root / "README_EN.md").write_text(
                (root / "README_EN.md").read_text(encoding="utf-8")
                + "\nCompatible kemo-adapter-api 0.8.2\n",
                encoding="utf-8",
            )
            (root / "global_knowledge" / "project-introduction.md").write_text(
                "当前稳定版本为 `1.3.0`。配套 kemo-adapter-api 0.8.2。\n",
                encoding="utf-8",
            )
            (root / "global_knowledge" / "version-and-update-modules.md").write_text(
                '{"version": "1.3.0", "compatibility": {"kemo-adapter-api": "0.8.2"}}\n',
                encoding="utf-8",
            )
            (root / "agents.md").write_text(
                "当前稳定版本：`kemo-agent 1.3.0`，配套 kemo-adapter-api 0.8.2，"
                "本版本完成长期智能、会话生命周期、模块面板与 Web 交互收敛。\n\n## 架构\n",
                encoding="utf-8",
            )
            soul = root / "config" / "global_soul.md"
            soul.parent.mkdir(parents=True, exist_ok=True)
            soul.write_text(
                "当前能力基线：kemo-agent 1.3.0，配套 kemo-adapter-api 0.8.2。\n",
                encoding="utf-8",
            )

            code, output = run_check(root)
            self.assertEqual(code, 0, output)

            manifest.pop("compatibility")
            version_path.write_text(json.dumps(manifest), encoding="utf-8")
            code, output = run_check(root)
            self.assertEqual(code, 1)
            self.assertIn("version.json 缺少 compatibility.kemo-adapter-api", output)

            manifest["compatibility"] = {"kemo-adapter-api": "0.8.2"}
            version_path.write_text(json.dumps(manifest), encoding="utf-8")

            (root / "README_EN.md").write_text(
                (root / "README_EN.md")
                .read_text(encoding="utf-8")
                .replace("0.8.2", "0.8.1"),
                encoding="utf-8",
            )
            code, output = run_check(root)
            self.assertEqual(code, 1)
            self.assertIn("README_EN.md 未声明配套 kemo-adapter-api 0.8.2", output)

    def test_tag_mismatch_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            code, output = run_check(root, "--tag", "v1.2.6")
            self.assertEqual(code, 1)
            self.assertIn("发布标签", output)


    def test_tag_environment_must_match_the_fixture_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build_release_tree(root)
            code, output = run_check(
                root, env={"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2.7"}
            )
            self.assertEqual(code, 0, output)
            code, output = run_check(
                root, env={"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2.8"}
            )
            self.assertEqual(code, 1)
            self.assertIn("发布标签", output)


if __name__ == "__main__":
    unittest.main()
