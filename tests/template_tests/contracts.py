"""Small, dependency-free result protocol shared by every template check."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


CheckStatus = Literal["passed", "failed", "warning", "skipped"]


@dataclass(frozen=True, slots=True)
class ContractCheck:
    check_id: str
    status: CheckStatus
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.check_id,
            "status": self.status,
            "message": self.message,
        }
        if self.evidence:
            payload["evidence"] = dict(self.evidence)
        return payload


@dataclass(slots=True)
class ContractReport:
    kind: str
    target: Path
    checks: list[ContractCheck] = field(default_factory=list)

    def add(
        self,
        check_id: str,
        status: CheckStatus,
        message: str,
        **evidence: Any,
    ) -> None:
        self.checks.append(
            ContractCheck(
                check_id=check_id,
                status=status,
                message=message,
                evidence={key: value for key, value in evidence.items() if value is not None},
            )
        )

    def passed(self, check_id: str, message: str, **evidence: Any) -> None:
        self.add(check_id, "passed", message, **evidence)

    def failed(self, check_id: str, message: str, **evidence: Any) -> None:
        self.add(check_id, "failed", message, **evidence)

    def warning(self, check_id: str, message: str, **evidence: Any) -> None:
        self.add(check_id, "warning", message, **evidence)

    def skipped(self, check_id: str, message: str, **evidence: Any) -> None:
        self.add(check_id, "skipped", message, **evidence)

    @property
    def ok(self) -> bool:
        return not any(check.status == "failed" for check in self.checks)

    @property
    def complete(self) -> bool:
        return self.ok and not any(check.status == "skipped" for check in self.checks)

    @property
    def summary(self) -> dict[str, int]:
        return {
            status: sum(check.status == status for check in self.checks)
            for status in ("passed", "failed", "warning", "skipped")
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": self.kind,
            "target": str(self.target),
            "ok": self.ok,
            "complete": self.complete,
            "summary": self.summary,
            "checks": [check.to_dict() for check in self.checks],
        }

    def render_text(self) -> str:
        icons = {
            "passed": "PASS",
            "failed": "FAIL",
            "warning": "WARN",
            "skipped": "SKIP",
        }
        lines = [
            f"Template contract: {self.kind}",
            f"Target: {self.target}",
            "",
        ]
        lines.extend(
            f"[{icons[check.status]}] {check.check_id}: {check.message}"
            for check in self.checks
        )
        summary = self.summary
        lines.extend(
            [
                "",
                (
                    "Result: "
                    + ("PASSED" if self.ok else "FAILED")
                    + (
                        " (incomplete checks remain)"
                        if self.ok and not self.complete
                        else ""
                    )
                ),
                (
                    "Summary: "
                    f"{summary['passed']} passed, {summary['failed']} failed, "
                    f"{summary['warning']} warnings, {summary['skipped']} skipped"
                ),
            ]
        )
        return "\n".join(lines)
