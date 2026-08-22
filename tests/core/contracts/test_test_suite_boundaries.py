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
            "tests/template_tests/",
            "开发临时目录/test_kemo/",
        ):
            self.assertIn(phrase, readme)

    def test_local_system_suite_is_gitignored(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text("utf-8")
        self.assertIn("开发临时目录/", gitignore.splitlines())


if __name__ == "__main__":
    unittest.main()
