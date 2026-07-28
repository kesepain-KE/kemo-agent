import json
from pathlib import Path

from tests.template_tests.user.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_user_contract_and_report_redaction() -> None:
    report = validate(
        ROOT / "template" / "user",
        repository_root=ROOT,
        template_mode=True,
    )
    assert report.ok, report.render_text()
    rendered = json.dumps(report.to_dict(), ensure_ascii=False)
    assert '"schema_version": 1' in rendered
    assert '"api_key"' not in rendered

