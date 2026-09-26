"""Keep public quick starts aligned with the local distribution contract."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GUIDES = (
    "readme.md",
    "README_EN.md",
    "global_knowledge/project-introduction.md",
    "global_knowledge/deployment-and-release.md",
    "deploy/README.md",
)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class DeploymentDocumentationTests(unittest.TestCase):
    def test_four_channel_commands_match_local_distribution(self) -> None:
        config = read("deploy/deploy.yaml")
        repo_match = re.search(r"^  repo: (\S+)$", config, re.MULTILINE)
        self.assertIsNotNone(repo_match)
        repo = repo_match.group(1)
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
        for guide in GUIDES:
            with self.subTest(guide=guide):
                text = read(guide)
                for command in commands:
                    self.assertIn(command, text)
        for relative in ("windows/install.ps1", "linux/install.sh", "docker/docker-compose.yml"):
            self.assertTrue((ROOT / "deploy" / relative).is_file())

    def test_daily_commands_match_between_languages(self) -> None:
        def code_blocks(document: str) -> list[str]:
            quick_start = document.split("### 1.3.", 1)[0]
            return [
                line.strip()
                for block in re.findall(r"```[^\n]*\n(.*?)```", quick_start, re.DOTALL)
                for line in block.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]

        self.assertEqual(code_blocks(read("readme.md")), code_blocks(read("README_EN.md")))

    def test_knowledge_is_discoverable_from_manual_and_index(self) -> None:
        for guide in ("agents.md", "global_knowledge/data_structure.md"):
            self.assertIn("deployment-and-release.md", read(guide))
        for guide in GUIDES:
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
                self.assertIn(
                    "Publication prerequisite" if guide == "README_EN.md" else "发布",
                    text,
                )
        package = json.loads(read("deploy/npm/package.json"))
        self.assertNotIn("version", package)
        self.assertTrue(package["private"])


if __name__ == "__main__":
    unittest.main()
