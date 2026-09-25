import json
import shutil
import tempfile
from pathlib import Path

from tests.template_tests.sense.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_sense_contract() -> None:
    report = validate(
        ROOT / "template" / "sense",
        repository_root=ROOT,
        template_mode=True,
        timeout=8,
    )
    assert report.ok, report.render_text()
    passed_ids = {check.check_id for check in report.checks if check.status == "passed"}
    assert {"sense.panel_contract", "sense.panel_runtime"} <= passed_ids


def test_broken_update_entry_is_a_sense_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "broken_sense"
        shutil.copytree(ROOT / "template" / "sense", target)
        (target / "data_update.py").write_text("def update(:\n    pass\n", "utf-8")
        report = validate(
            target,
            repository_root=ROOT,
            runtime_probe=False,
        )
        failed_ids = {
            check.check_id for check in report.checks if check.status == "failed"
        }
        assert "sense.update_import" in failed_ids, report.render_text()


def test_invalid_component_action_is_a_sense_failure() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "broken_panel_sense"
        shutil.copytree(ROOT / "template" / "sense", target)
        panel_path = target / "module" / "panel.json"
        panel = json.loads(panel_path.read_text("utf-8"))
        panel["containers"][-1]["controls"][0]["command"] = "probe"
        panel_path.write_text(json.dumps(panel, ensure_ascii=False), "utf-8")
        report = validate(
            target,
            repository_root=ROOT,
            runtime_probe=False,
        )
        failed_ids = {check.check_id for check in report.checks if check.status == "failed"}
        assert "sense.panel_contract" in failed_ids, report.render_text()
