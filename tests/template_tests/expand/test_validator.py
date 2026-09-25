import json
import shutil
import tempfile
from pathlib import Path

from tests.template_tests.detection import detect_kind
from tests.template_tests.expand.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_expand_contract() -> None:
    report = validate(
        ROOT / "template" / "expand",
        repository_root=ROOT,
        template_mode=True,
        timeout=8,
    )
    assert report.ok, report.render_text()
    passed_ids = {check.check_id for check in report.checks if check.status == "passed"}
    assert {"expand.panel_contract", "expand.panel_runtime", "expand.panel_actions"} <= passed_ids


def test_nested_complete_project_does_not_change_expand_contract() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "expand_candidate"
        shutil.copytree(ROOT / "template" / "expand", target)
        nested = target / "vendor" / "complete_project"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text(
            "# Vendored helper\n\nNested project descriptor.\n",
            "utf-8",
        )
        (nested / "arbitrary.bin").write_bytes(b"arbitrary")
        assert detect_kind(target) == "expand"
        report = validate(
            target,
            repository_root=ROOT,
            template_mode=True,
            timeout=8,
        )
        assert report.ok, report.render_text()


def test_invalid_component_panel_is_an_expand_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "broken_panel_expand"
        shutil.copytree(ROOT / "template" / "expand", target)
        panel_path = target / "module" / "panel.json"
        panel = json.loads(panel_path.read_text("utf-8"))
        panel["containers"][0]["source"] = "../../outside.json"
        panel_path.write_text(json.dumps(panel, ensure_ascii=False), "utf-8")
        report = validate(
            target,
            repository_root=ROOT,
            runtime_probe=False,
        )
        failed_ids = {check.check_id for check in report.checks if check.status == "failed"}
        assert "expand.panel_contract" in failed_ids, report.render_text()
