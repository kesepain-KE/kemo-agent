from __future__ import annotations

import ast
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
LEGACY_MODULES = {
    "run.agent_queue",
    "run.agent_runner",
    "run.agent_service",
    "run.atomic_io",
    "run.attachments",
    "run.cli",
    "run.context_service",
    "run.context_summary",
    "run.conversation_runtime",
    "run.cron_log_aggregator",
    "run.cron_runtime_state",
    "run.cron_store",
    "run.errors",
    "run.execution_watchdog",
    "run.expand_runtime",
    "run.guidance",
    "run.guidance_runtime",
    "run.history_index",
    "run.history_store",
    "run.history_summary_scheduler",
    "run.knowledge",
    "run.log_store",
    "run.long_task_runtime",
    "run.maintenance",
    "run.media_outputs",
    "run.memory_analysis",
    "run.memory_pipeline",
    "run.memory_sqlite",
    "run.memory_store",
    "run.model_capabilities",
    "run.module_runtime",
    "run.multimodal",
    "run.process_execution",
    "run.process_utils",
    "run.prompt",
    "run.prompt_sources",
    "run.provider_events",
    "run.provider_tool_recovery",
    "run.request_input",
    "run.round_finalizer",
    "run.run_state",
    "run.runtime_host",
    "run.session_runtime",
    "run.source_policy",
    "run.subagent_invocation",
    "run.task_plan_boundary",
    "run.task_plan_executor",
    "run.task_plan_mutations",
    "run.task_plan_scheduler",
    "run.task_plan_service",
    "run.task_plan_store",
    "run.usage",
    "run.users",
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


def imported_modules(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
            if node.module == "run":
                imports.extend(
                    (node.lineno, f"run.{alias.name}")
                    for alias in node.names
                    if alias.name in {
                        module.removeprefix("run.") for module in LEGACY_MODULES
                    }
                )
        elif isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
    return imports


def is_legacy_reference(value: str) -> bool:
    return any(value == module or value.startswith(module + ".") for module in LEGACY_MODULES)


class RunImportBoundaryTests(unittest.TestCase):
    def test_run_root_contains_only_the_lazy_entry_and_total_facade(self) -> None:
        self.assertEqual(
            {path.name for path in RUN_ROOT.glob("*.py")},
            {"__init__.py", "engine.py"},
        )

    def test_production_code_uses_domain_entries(self) -> None:
        violations: list[str] = []
        for path in production_python_files():
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for line, module in imported_modules(tree):
                parts = module.split(".")
                is_private_domain_import = (
                    len(parts) >= 3
                    and parts[0] == "run"
                    and parts[1] in DOMAINS
                )
                if is_legacy_reference(module) or is_private_domain_import:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{line} imports {module}"
                    )
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and is_legacy_reference(node.value)
                ):
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno} references {node.value}"
                    )
        self.assertEqual(violations, [])

    def test_production_callers_do_not_import_engine_private_names(self) -> None:
        violations: list[str] = []
        for path in production_python_files():
            tree = ast.parse(path.read_text("utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or node.module != "run.engine":
                    continue
                private_names = [
                    alias.name for alias in node.names if alias.name.startswith("_")
                ]
                if private_names:
                    violations.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {', '.join(private_names)}"
                    )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
