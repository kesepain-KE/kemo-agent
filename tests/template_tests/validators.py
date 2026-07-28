"""Thin auto-detection and dispatch facade for the six independent validators."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from tests.template_tests.common import PROJECT_ROOT
from tests.template_tests.contracts import ContractReport
from tests.template_tests.detection import SUPPORTED_KINDS, detect_kind


_MODULES = {
    kind: f"tests.template_tests.{kind}.validator"
    for kind in SUPPORTED_KINDS
}


def validate_template(
    kind: str,
    target: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
    template_mode: bool = False,
    runtime_probe: bool = True,
) -> ContractReport:
    normalized = str(kind or "").strip().casefold()
    raw_target = Path(target)
    if normalized == "auto":
        try:
            normalized = detect_kind(raw_target)
        except BaseException as exc:
            report = ContractReport("auto", raw_target.resolve())
            report.failed("target.kind", str(exc))
            return report
    if normalized not in SUPPORTED_KINDS:
        report = ContractReport(normalized, raw_target.resolve())
        report.failed(
            "target.kind",
            "不支持的模板类型；只允许 " + ", ".join(SUPPORTED_KINDS),
        )
        return report
    module: Any = importlib.import_module(_MODULES[normalized])
    return module.validate(
        raw_target,
        repository_root=repository_root,
        timeout=timeout,
        template_mode=template_mode,
        runtime_probe=runtime_probe,
    )


__all__ = ["SUPPORTED_KINDS", "detect_kind", "validate_template"]
