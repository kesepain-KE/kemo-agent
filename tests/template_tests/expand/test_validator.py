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

