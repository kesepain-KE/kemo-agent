from __future__ import annotations

import ast
import unittest
from pathlib import Path

import run.engine as engine


ROOT = Path(__file__).resolve().parents[3]
RUN_ROOT = ROOT / "run"
DOMAINS = {
    "agents",
    "config",
    "context",
    "conversation",
    "extensions",
    "history",
    "infra",
    "long_task",
    "memory",
    "scheduler",
    "tasks",
    "tools",
}
ENGINE_PUBLIC_API = {
    "ContextLengthExceededError",
    "EngineError",
    "compress_context",
    "context_status",
    "handle_request",
    "iter_request_events",
    "stream_request",
}


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
    return imports


class RunPackageLayoutTests(unittest.TestCase):
    def test_all_runtime_domains_have_one_package_entry(self) -> None:
        actual = {
            path.name
            for path in RUN_ROOT.iterdir()
            if path.is_dir() and not path.name.startswith("__")
        }
        self.assertTrue(DOMAINS <= actual)
        for domain in sorted(DOMAINS):
            self.assertTrue((RUN_ROOT / domain / "__init__.py").is_file(), domain)

    def test_engine_is_the_small_stable_total_facade(self) -> None:
        self.assertEqual(set(engine.__all__), ENGINE_PUBLIC_API)
        source = (RUN_ROOT / "engine.py").read_text("utf-8")
        self.assertLess(len(source.splitlines()), 40)
        self.assertNotIn("_sys.modules[__name__]", source)
        self.assertNotIn("def _iter_request_events_impl", source)

    def test_run_package_root_keeps_runtime_imports_lazy(self) -> None:
        path = RUN_ROOT / "__init__.py"
        tree = ast.parse(path.read_text("utf-8"), filename=str(path))
        eager_runtime_imports = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("run."):
                eager_runtime_imports.append((node.lineno, node.module))
            elif isinstance(node, ast.Import):
                eager_runtime_imports.extend(
                    (node.lineno, alias.name)
                    for alias in node.names
                    if alias.name.startswith("run.")
                )
        self.assertEqual(eager_runtime_imports, [])
        self.assertIn("def __getattr__", path.read_text("utf-8"))

    def test_domains_do_not_import_another_domain_private_module(self) -> None:
        violations: list[str] = []
        for owner in sorted(DOMAINS):
            for path in (RUN_ROOT / owner).rglob("*.py"):
                for line, module in imported_modules(path):
                    parts = module.split(".")
                    if (
                        len(parts) >= 3
                        and parts[0] == "run"
                        and parts[1] in DOMAINS
                        and parts[1] != owner
                    ):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{line} imports {module}"
                        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
