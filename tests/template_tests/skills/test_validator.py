from pathlib import Path

from tests.template_tests.skills.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_skill_contract() -> None:
    report = validate(
        ROOT / "template" / "skills",
        repository_root=ROOT,
        template_mode=True,
    )
    assert report.ok, report.render_text()


def test_skill_placeholders_fail_outside_template_mode() -> None:
    report = validate(
        ROOT / "template" / "skills",
        repository_root=ROOT,
        template_mode=False,
    )
    assert not report.ok, report.render_text()
    assert any(
        check.check_id == "skills.placeholder" and check.status == "failed"
        for check in report.checks
    )

