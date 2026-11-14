"""Exercise real documentation checks and deliberate stale/invalid variants."""
from __future__ import annotations

import importlib.util
import yaml
import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "agents_manual_contract_check", ROOT / ".github/scripts/check_agents_manual.py"
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
CONTRACT = json.loads((ROOT / checker.CONTRACT).read_text("utf-8"))
MANUAL = (ROOT / "agents.md").read_text("utf-8")
ENGINEERING = (ROOT / CONTRACT["engineering_manual"]).read_text("utf-8")


@pytest.fixture
def isolated_repository(tmp_path: Path) -> Path:
    """Copy public documentation definitions only; never copy/create real users."""
    sources = {
        checker.CONTRACT,
        "agents.md",
        CONTRACT["engineering_manual"],
    }
    for rule in CONTRACT["rules"]:
        sources.update(row["path"] for row in rule.get("facts", {}).values())
        sources.update(row["path"] for row in rule.get("source_guards", []))
    sources.update(row["source"] for row in CONTRACT["runtime_paths"])
    for group in CONTRACT["inventories"]:
        for path in ROOT.glob(group["glob"]):
            sources.add(str(path.relative_to(ROOT)))
            if group["kind"] == "plugin":
                sources.update(str(entry.relative_to(ROOT)) for entry in path.parent.glob("*.py"))
                sources.add(str(path.with_name("plugin.yaml").relative_to(ROOT)))
            if group["kind"] == "agent":
                raw = json.loads(path.read_text("utf-8"))
                sources.update(str((path.parent / name).relative_to(ROOT)) for name in ("AGENT.md", "agent-config.json", raw["trigger"]))
    for document in (MANUAL, ENGINEERING):
        for token in re.findall(r"`([^`\n]+)`", document):
            if re.fullmatch(r"[.\w<>*-]+(?:/[.\w<>*()-]+)+/?", token) or re.fullmatch(r"[\w.-]+\.(?:md|py|json|yml)", token):
                if not token.startswith(("users/", "tmp/", "runtime/")) and (ROOT / token).exists():
                    sources.add(token)
    for relative in sources:
        source, target = ROOT / relative, tmp_path / relative
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return tmp_path


def test_repository_manual_contract() -> None:
    result = checker.check(ROOT)
    assert result["passed"], result["errors"]
    assert len(result["inventory"]["plugin"]) >= 19
    assert set(result["inventory"]["agent"]) == {
        "general_worker", "history_summary", "memory_temporary_important",
        "self_improve", "task_plan", "time_plan",
    }
    assert "context_manage" not in result["inventory"]["agent"]


def test_production_prompt_loads_the_complete_single_manual() -> None:
    from run.config.prompt import _base_section

    section = _base_section(ROOT, "agents_manual", ROOT / "agents.md")
    assert section is not None
    assert section.content == MANUAL.strip()
    assert section.injected_chars == section.original_chars == len(MANUAL.strip())
    assert section.source_files == ("agents.md",)
    assert "agents-manual.contract.json" not in section.source_files


def test_personal_manual_is_light_and_engineering_reference_is_separate() -> None:
    assert len(MANUAL) < 8000
    assert "个人智能操作入口" in MANUAL
    assert "knowledge/global/engineering/engineering-operations.md" in MANUAL
    assert "MAX_TOOL_RESULT_CHARS" in ENGINEERING
    assert "provider_runtime.max_retries" in ENGINEERING
    assert "MAX_TOOL_RESULT_CHARS" not in MANUAL
    assert "provider_runtime.max_retries" not in MANUAL


@pytest.mark.parametrize(
    ("before", "after", "error", "in_engineering"),
    [
        ("tools.timeout=240", "tools.timeout=241", "claim tool-budget", True),
        ("不要盲目重试", "可以直接重试", "required safety", True),
        ("### 用户指定执行路径", "### 信任与资料", "duplicate h3", False),
        ("knowledge/global/web/inline-widgets.md", "knowledge/global/missing.md", "source path missing", False),
        ("users/<name>/file_upload/", "users/<name>/upload/", "runtime path not classified", True),
        ("`web_search`", "搜索工具", "coverage plugin", True),
        ("查询工具对相同参数也重新执行", "静态查询", "claim tool-dedup", True),
        ("agents.output_reserve=10000", "agents.output_reserve=4096", "claim context-budget", True),
    ],
)
def test_manual_drift_is_rejected(
    before: str, after: str, error: str, in_engineering: bool
) -> None:
    source = ENGINEERING if in_engineering else MANUAL
    assert before in source
    arguments = {"engineering_text": ENGINEERING.replace(before, after)}
    if before in MANUAL:
        arguments["manual_text"] = MANUAL.replace(before, after)
    elif not in_engineering:
        arguments["manual_text"] = source.replace(before, after)
    result = checker.check(ROOT, **arguments)
    assert not result["passed"]
    assert any(error in item for item in result["errors"]), result["errors"]


def test_config_change_without_manual_update_fails(isolated_repository: Path) -> None:
    path = isolated_repository / "config/global_config.yaml"
    value = yaml.safe_load(path.read_text("utf-8"))
    value["tools"]["timeout"] = 241
    path.write_text(json.dumps(value), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim tool-budget" in item and "241" in item for item in result["errors"])


def test_same_shell_returns_with_changed_condition_are_rejected(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/shell/tool.py"
    text = path.read_text("utf-8")
    assert "if _looks_like_powershell(command):" in text
    path.write_text(text.replace("if _looks_like_powershell(command):", "if not _looks_like_powershell(command):", 1), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim shell-selection: source behavior changed" in item for item in result["errors"])


def test_main_raw_preservation_bypass_is_rejected(isolated_repository: Path) -> None:
    path = isolated_repository / "run/tools/__init__.py"
    text = path.read_text("utf-8")
    assert "if preserve_result:" in text
    path.write_text(text.replace("if preserve_result:", "if False:", 1), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim tool-result: source behavior changed" in item for item in result["errors"])


def test_unbounded_progress_retries_are_rejected(isolated_repository: Path) -> None:
    path = isolated_repository / "run/retry/loop.py"
    text = path.read_text("utf-8")
    assert "self.attempts >= self.max_attempts" in text
    path.write_text(text.replace("self.attempts >= self.max_attempts", "False"), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim provider-retries: source behavior changed" in item for item in result["errors"])


def test_new_plugin_requires_reachable_entry(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/new_capability/SKILL.md"
    path.parent.mkdir()
    path.write_text('# new_capability\n\nDiscover me.\n', "utf-8")
    path.with_name("plugin.yaml").write_text(
                    '{"name":"new_capability","enabled":true,"description":"test",'
                    '"version":"1.0.0","entrypoint":"tool.py:run",'
                    '"input_schema":{"type":"object"}}\n', "utf-8")
    (path.parent / "tool.py").write_text("def run(): pass\n", "utf-8")
    result = checker.check(isolated_repository)
    assert any("coverage plugin: no explicit capability/entry for new_capability" in item for item in result["errors"])


def test_bad_manifest_does_not_count_as_directory_coverage(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/web_search/plugin.yaml"
    path.write_text("[]\n", "utf-8")
    result = checker.check(isolated_repository)
    assert any("plugin.yaml must be a mapping" in item for item in result["errors"])


def test_runtime_templates_do_not_require_users_or_external_repository(isolated_repository: Path) -> None:
    assert not (isolated_repository / "users").exists()
    assert not (isolated_repository / "agent_control.md").exists()
    result = checker.check(isolated_repository)
    assert result["passed"], result["errors"]


def test_budget_and_unclosed_fence_fail_closed() -> None:
    result = checker.check(ROOT, manual_text=MANUAL + "字" * CONTRACT["max_characters"])
    assert any("budget:" in item for item in result["errors"])
    with pytest.raises(ValueError, match="unclosed Markdown fence"):
        checker.check(ROOT, manual_text=MANUAL + "\n```text\n")


def test_fenced_headings_and_comments_are_not_real_coverage() -> None:
    assert checker.headings("## Real\n\n```md\n### Fake\n```\n") == [(1, 2, "Real")]
    result = checker.check(
        ROOT,
        engineering_text=ENGINEERING.replace("`web_search`", "搜索工具")
        + "\n<!-- `web_search` -->\n",
    )
    assert any("coverage plugin" in item for item in result["errors"])


def test_numeric_fact_parser_does_not_execute_code(tmp_path: Path) -> None:
    marker = tmp_path / "must_not_exist"
    text = f"VALUE = __import__('pathlib').Path({str(marker)!r}).write_text('bad')"
    with pytest.raises(ValueError):
        checker.literal(checker.python_node(text, "VALUE"))
    assert not marker.exists()
