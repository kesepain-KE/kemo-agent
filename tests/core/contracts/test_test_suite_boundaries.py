from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class TestSuiteBoundaryTests(unittest.TestCase):
    def test_formal_suite_documents_its_release_boundary(self) -> None:
        readme = (ROOT / "tests" / "README.md").read_text("utf-8")
        for phrase in (
            "发布红线",
            "contracts/",
            "runtime/",
            "storage/",
            "deploy/",
            "tests/template_tests/",
            "开发临时目录/test_kemo/",
        ):
            self.assertIn(phrase, readme)

    def test_local_system_suite_is_gitignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text("utf-8")
        self.assertIn("开发临时目录/", gitignore.splitlines())

    def test_deployment_assertions_have_a_formal_release_gate(self) -> None:
        canonical = ROOT / "tests" / "deploy" / "test_deploy.py"
        compatibility = ROOT / "deploy" / "tests" / "test_deploy.py"
        self.assertTrue(canonical.is_file())
        self.assertIn("class DeploymentTests", canonical.read_text("utf-8"))
        wrapper = compatibility.read_text("utf-8")
        self.assertIn("tests.deploy.test_deploy", wrapper)
        self.assertNotIn("class DeploymentTests", wrapper)

    def test_builtin_expand_private_dotenv_rules_follow_source_exceptions(
        self,
    ) -> None:
        lines = (ROOT / ".gitignore").read_text("utf-8").splitlines()
        private_rule = lines.index("global_expand/**/.env")
        variant_rule = lines.index("global_expand/**/.env.*")
        example_rule = lines.index("!global_expand/**/.env.example")
        for module in ("kemo_graph", "kemo_app"):
            source_rule = lines.index(f"!global_expand/{module}/**")
            self.assertGreater(private_rule, source_rule)
            self.assertGreater(variant_rule, source_rule)
        self.assertGreater(example_rule, variant_rule)


if __name__ == "__main__":
    unittest.main()
