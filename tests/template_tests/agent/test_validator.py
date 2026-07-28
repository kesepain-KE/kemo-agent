from pathlib import Path

from tests.template_tests.agent.validator import validate


ROOT = Path(__file__).resolve().parents[3]


def test_reference_agent_contract() -> None:
    report = validate(
        ROOT / "template" / "agent",
        repository_root=ROOT,
        template_mode=True,
        timeout=8,
    )
    assert report.ok, report.render_text()

