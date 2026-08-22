from __future__ import annotations

import ast
import importlib
import re
import unittest
from pathlib import Path


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
PRODUCTION_EXCLUDES = {
    ".git",
    ".venv",
    "build",
    "开发临时目录",
    "dist",
    "node_modules",
    "output",
    "run",
    "tests",
    "tmp",
    "users",
    "venv",
}
ALIAS_PATTERN = re.compile(
    r'_sys\.modules\[__name__\]\s*=\s*_import_module\("(run\.[^"]+)"\)'
)
IDENTITY_SAMPLES = {
    "run.agent_runner": "run.agents.runner",
    "run.attachments": "run.extensions.attachments",
    "run.atomic_io": "run.infra.atomic_io",
    "run.context_service": "run.context.service",
    "run.conversation_runtime": "run.conversation.runtime",
    "run.history_store": "run.history.store",
    "run.memory_store": "run.memory.store",
    "run.runtime_host": "run.scheduler.runtime_host",
    "run.source_policy": "run.config.source_policy",
    "run.task_plan_store": "run.tasks.store",
}


def top_level_shims() -> dict[str, str]:
    shims: dict[str, str] = {}
    for path in sorted(RUN_ROOT.glob("*.py")):
        if path.name in {"__init__.py", "engine.py"}:
            continue
        source = path.read_text("utf-8")
        match = ALIAS_PATTERN.search(source)
        if match:
            shims[path.stem] = match.group(1)
    return shims


def imported_modules(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text("utf-8"), filename=str(path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
    return imports


def production_python_files() -> list[Path]:
    result: list[Path] = []
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT)
        if relative.parts and relative.parts[0] in PRODUCTION_EXCLUDES:
            continue
        if "node_modules" in relative.parts or "__pycache__" in relative.parts:
            continue
        result.append(path)
    return result


class RunImportCompatibilityTests(unittest.TestCase):
    def test_every_legacy_top_level_module_is_a_true_alias_shim(self) -> None:
        expected = {
            path.stem
            for path in RUN_ROOT.glob("*.py")
            if path.name not in {"__init__.py", "engine.py"}
        }
        shims = top_level_shims()
        self.assertEqual(set(shims), expected)
        for stem, target in shims.items():
            source = (RUN_ROOT / f"{stem}.py").read_text("utf-8")
            self.assertNotIn("import *", source, stem)
            self.assertTrue(target.startswith("run."), stem)

    def test_legacy_and_canonical_imports_share_module_identity(self) -> None:
        for legacy, canonical in IDENTITY_SAMPLES.items():
            with self.subTest(legacy=legacy):
                self.assertIs(
                    importlib.import_module(legacy),
                    importlib.import_module(canonical),
                )

    def test_production_code_uses_domain_entries(self) -> None:
        shims = top_level_shims()
        legacy_modules = {f"run.{stem}" for stem in shims}
        violations: list[str] = []
        for path in production_python_files():
            for line, module in imported_modules(path):
                parts = module.split(".")
                is_private_domain_import = (
                    len(parts) >= 3
                    and parts[0] == "run"
                    and parts[1] in DOMAINS
                )
                if module in legacy_modules or is_private_domain_import:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line} imports {module}"
                    )
        self.assertEqual(violations, [])

    def test_production_callers_do_not_import_engine_private_names(self) -> None:
        violations: list[str] = []
        for path in production_python_files():
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "run.engine":
                    continue
                private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
                if private_names:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {', '.join(private_names)}"
                    )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
