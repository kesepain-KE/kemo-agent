"""Keep public quick starts aligned with the local distribution contract."""
from __future__ import annotations

from tests.type_assertions import require_present

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
READMES = ("readme.md", "README_EN.md")
DEPLOYMENT_GUIDES = (
    "knowledge/global/overview/project-introduction.md",
    "knowledge/global/release/deployment-and-release.md",
    "deploy/README.md",
)
GUIDES = (*READMES, *DEPLOYMENT_GUIDES)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DeploymentDocumentationTests(unittest.TestCase):
    def test_four_channel_commands_match_local_distribution(self) -> None:
        config = read("deploy/deploy.yaml")
        repo_match = re.search(r"^  repo: (\S+)$", config, re.MULTILINE)
        self.assertIsNotNone(repo_match)
        repo = require_present(repo_match).group(1)
        base = f"https://raw.githubusercontent.com/{repo}/main/deploy"
        package_name = json.loads(read("deploy/npm/package.json"))["name"]
        npm_asset = "kemo-agent-npm.tgz"
        commands = (
            f"irm {base}/windows/install.ps1 | iex",
            f"curl -fsSL {base}/linux/install.sh | sh",
            f"npm install -g https://github.com/{repo}/releases/latest/download/{npm_asset}",
            f"curl -fsSL {base}/docker/docker-compose.yml -o docker-compose.yml && docker compose up -d",
        )
        # npm 渠道走 Release 资产直装，不经过 npm registry（该 registry 即使包为 public 也强制要 token）
        self.assertTrue(package_name.startswith("@kesepain"))
        for guide in DEPLOYMENT_GUIDES:
            with self.subTest(guide=guide):
                text = read(guide)
                for command in commands:
                    self.assertIn(command, text)
        for relative in ("windows/install.ps1", "linux/install.sh", "docker/docker-compose.yml"):
            self.assertTrue((ROOT / "deploy" / relative).is_file())

    def test_readme_quick_starts_match_and_link_to_full_installation(self) -> None:
        def code_blocks(document: str) -> list[str]:
            quick_start = re.search(
                r"^## (?:快速开始|Quick start)\s*\n(.*?)(?=^## |\Z)",
                document, re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(quick_start)
            return [
                line.strip()
                for block in re.findall(
                    r"```[^\n]*\n(.*?)```", require_present(quick_start).group(1), re.DOTALL,
                )
                for line in block.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]

        commands = code_blocks(read("readme.md"))
        self.assertEqual(commands, code_blocks(read("README_EN.md")))
        self.assertEqual(len(commands), 2)
        self.assertIn("/deploy/windows/install.ps1 | iex", commands[0])
        self.assertEqual(commands[1], '& "$env:USERPROFILE\\.kemo-agent\\deploy\\deploy.cmd" start')
        for guide in READMES:
            with self.subTest(guide=guide):
                text = read(guide)
                for anchor in ("linux", "npm", "docker", "源码安装"):
                    self.assertIn(
                        f"https://kesepain-ke.github.io/kemo-agent-doc/guide/installation#{anchor}",
                        text,
                    )

    def test_knowledge_is_discoverable_from_manual(self) -> None:
        self.assertIn("knowledge/global/release/deployment-and-release.md", read("agents.md"))
        self.assertTrue(
            (ROOT / "knowledge/global/release/deployment-and-release.md").is_file()
        )
        for guide in DEPLOYMENT_GUIDES:
            text = read(guide)
            self.assertIn("kemo-agent-release-<version>.zip", text)
            self.assertIn("down -v", text)

    def test_current_version_and_release_prerequisite_are_documented(self) -> None:
        version = json.loads(read("version.json"))["version"]
        for guide in GUIDES:
            with self.subTest(guide=guide):
                text = read(guide)
                self.assertIn(version, text)
                self.assertIn("Release", text)
        for guide in DEPLOYMENT_GUIDES:
            with self.subTest(guide=guide):
                self.assertIn("发布", read(guide))
        package = json.loads(read("deploy/npm/package.json"))
        self.assertNotIn("version", package)
        self.assertTrue(package["private"])

    def test_project_introductions_keep_only_the_latest_release_notes(self) -> None:
        version = json.loads(read("version.json"))["version"]
        release_headings = re.findall(
            r"^### (\d+\.\d+\.\d+)(?=[\s（(])",
            read("knowledge/global/overview/project-introduction.md"), re.MULTILINE,
        )
        self.assertEqual(release_headings, [version])
        for guide in READMES:
            with self.subTest(guide=guide):
                self.assertIn(
                    "https://github.com/kesepain-KE/kemo-agent/releases",
                    read(guide),
                )

    def test_chat_scroll_contract_is_discoverable_from_manual(self) -> None:
        self.assertIn(
            "knowledge/global/web/frontend-conventions.md",
            read("agents.md"),
        )
        topic = read("knowledge/global/web/frontend-conventions.md")
        self.assertIn("composer-auxiliary", topic)
        self.assertIn("内部滚动", topic)
        self.assertIn("内部滚动", read("knowledge/global/overview/project-introduction.md"))


if __name__ == "__main__":
    unittest.main()
