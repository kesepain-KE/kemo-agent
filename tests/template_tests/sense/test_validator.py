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

