"""Exercise real documentation checks and deliberate stale/invalid variants."""
from __future__ import annotations

import importlib.util
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


@pytest.fixture
def isolated_repository(tmp_path: Path) -> Path:
    """Copy public documentation definitions only; never copy/create real users."""
    sources = {checker.CONTRACT, "agents.md"}
    for rule in CONTRACT["rules"]:
        sources.update(row["path"] for row in rule.get("facts", {}).values())
        sources.update(row["path"] for row in rule.get("source_guards", []))
    sources.update(row["source"] for row in CONTRACT["runtime_paths"])
    for group in CONTRACT["inventories"]:
        for path in ROOT.glob(group["glob"]):
            sources.add(str(path.relative_to(ROOT)))
            if group["kind"] == "plugin":
                sources.update(str(entry.relative_to(ROOT)) for entry in path.parent.glob("*.py"))
            if group["kind"] == "agent":
                raw = json.loads(path.read_text("utf-8"))
                sources.update(str((path.parent / name).relative_to(ROOT)) for name in ("AGENT.md", "agent-config.json", raw["trigger"]))
    for token in re.findall(r"`([^`\n]+)`", MANUAL):
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
    assert len(result["inventory"]["agent"]) >= 7


def test_production_prompt_loads_the_complete_single_manual() -> None:
    from run.config.prompt import _base_section

    section = _base_section(ROOT, "agents_manual", ROOT / "agents.md")
    assert section is not None
    assert section.content == MANUAL.strip()
    assert section.injected_chars == section.original_chars == len(MANUAL.strip())
    assert section.source_files == ("agents.md",)
    assert "agents-manual.contract.json" not in section.source_files


@pytest.mark.parametrize(
    ("before", "after", "error"),
    [
        ("tools.max_iterations=80", "tools.max_iterations=81", "claim tool-budget"),
        ("不要盲目重试", "可以直接重试", "required safety"),
        ("### 指令与资料边界", "### 内部优先级与身份边界", "duplicate h3"),
        ("global_knowledge/inline-widgets.md", "global_knowledge/missing.md", "source path missing"),
        ("users/<name>/file_upload/", "users/<name>/upload/", "runtime path not classified"),
        ("`web_search`", "搜索工具", "coverage plugin"),
        ("动态读取", "静态值", "claim tool-reuse"),
    ],
)
def test_manual_drift_is_rejected(before: str, after: str, error: str) -> None:
    assert before in MANUAL
    result = checker.check(ROOT, manual_text=MANUAL.replace(before, after))
    assert not result["passed"]
    assert any(error in item for item in result["errors"]), result["errors"]


def test_config_change_without_manual_update_fails(isolated_repository: Path) -> None:
    path = isolated_repository / "config/global_config.json"
    value = json.loads(path.read_text("utf-8"))
    value["tools"]["max_iterations"] = 81
    path.write_text(json.dumps(value), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim tool-budget" in item and "81" in item for item in result["errors"])


def test_same_shell_returns_with_changed_condition_are_rejected(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/shell/tool.py"
    text = path.read_text("utf-8")
    assert "if _looks_like_powershell(command):" in text
    path.write_text(text.replace("if _looks_like_powershell(command):", "if not _looks_like_powershell(command):", 1), "utf-8")
    result = checker.check(isolated_repository)
    assert any("claim shell-selection: source behavior changed" in item for item in result["errors"])


def test_new_plugin_requires_reachable_entry(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/new_capability/SKILL.md"
    path.parent.mkdir()
    path.write_text('# new_capability\n\nDiscover me.\n\n## Tool\n\n```json\n'
                    '{"name":"new_capability","enabled":true,"description":"test",'
                    '"version":"1.0.0","entrypoint":"tool.py:run",'
                    '"input_schema":{"type":"object"}}\n```\n', "utf-8")
    (path.parent / "tool.py").write_text("def run(): pass\n", "utf-8")
    result = checker.check(isolated_repository)
    assert any("coverage plugin: no explicit capability/entry for new_capability" in item for item in result["errors"])


def test_bad_manifest_does_not_count_as_directory_coverage(isolated_repository: Path) -> None:
    path = isolated_repository / "plugins/web_search/SKILL.md"
    path.write_text("# web_search\n\nA directory is not a valid registration.\n", "utf-8")
    result = checker.check(isolated_repository)
    assert any("one ## Tool required" in item for item in result["errors"])


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
    result = checker.check(ROOT, manual_text=MANUAL.replace("`web_search`", "搜索工具") + "\n<!-- `web_search` -->\n")
    assert any("coverage plugin" in item for item in result["errors"])


def test_numeric_fact_parser_does_not_execute_code(tmp_path: Path) -> None:
    marker = tmp_path / "must_not_exist"
    text = f"VALUE = __import__('pathlib').Path({str(marker)!r}).write_text('bad')"
    with pytest.raises(ValueError):
        checker.literal(checker.python_node(text, "VALUE"))
    assert not marker.exists()
