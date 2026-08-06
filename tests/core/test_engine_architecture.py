from __future__ import annotations

import ast
import unittest
from pathlib import Path

import run.engine as engine


ROOT = Path(__file__).resolve().parents[2]
ENGINE_PUBLIC_API = {
    "ContextLengthExceededError",
    "EngineError",
    "compress_context",
    "context_status",
    "handle_request",
    "iter_request_events",
    "stream_request",
}
DOMAIN_MODULES = (
    "conversation_runtime.py",
    "context_service.py",
    "memory_analysis.py",
    "provider_events.py",
    "request_input.py",
    "round_finalizer.py",
    "run_state.py",
    "session_runtime.py",
    "usage.py",
)


class EngineArchitectureTests(unittest.TestCase):
    def test_engine_is_a_stable_public_facade(self) -> None:
        self.assertEqual(set(engine.__all__), ENGINE_PUBLIC_API)
        source = (ROOT / "run" / "engine.py").read_text("utf-8")
        self.assertNotIn("def _iter_request_events_impl", source)
        self.assertLess(len(source.splitlines()), 40)

    def test_domain_modules_do_not_depend_on_engine_facade(self) -> None:
        for filename in DOMAIN_MODULES:
            path = ROOT / "run" / filename
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            imports_engine = any(
                isinstance(node, ast.ImportFrom) and node.module == "run.engine"
                for node in ast.walk(tree)
            )
            self.assertFalse(imports_engine, filename)

    def test_production_callers_do_not_import_engine_private_names(self) -> None:
        violations: list[str] = []
        for folder in ("run", "web", "message", "cron"):
            for path in (ROOT / folder).rglob("*.py"):
                tree = ast.parse(path.read_text("utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or node.module != "run.engine":
                        continue
                    private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
                    if private_names:
                        violations.append(
                            f"{path.relative_to(ROOT)}: {', '.join(private_names)}"
                        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
