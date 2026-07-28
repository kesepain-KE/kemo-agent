"""Small cross-kind helpers; no module-specific contract belongs here."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from cron.executor import _run_module_updater

from tests.template_tests.common import (
    ContractDependencyMissing,
    ensure_plain_directory,
)
from tests.template_tests.contracts import ContractReport


def begin_report(kind: str, target: Path) -> tuple[ContractReport, Path | None]:
    raw_target = Path(target)
    report = ContractReport(kind, raw_target.resolve())
    try:
        directory = ensure_plain_directory(raw_target)
    except BaseException as exc:
        report.failed("target.directory", str(exc))
        return report, None
    report.passed("target.directory", "候选模块是可复制的普通目录")
    report.passed(
        "layout.freedom",
        "额外文件、嵌套目录、第三方源码和完整工程不会因未知结构而被拒绝",
        files=sum(path.is_file() for path in directory.rglob("*")),
    )
    return report, directory


def missing_dependency_is_external(dependency: str) -> bool:
    top = dependency.split(".", 1)[0].casefold()
    return top not in {
        "agents",
        "cron",
        "message",
        "plugins",
        "provider",
        "run",
        "tests",
        "web",
    }


def record_exception(
    report: ContractReport,
    check_id: str,
    exc: BaseException,
    *,
    template_mode: bool,
) -> None:
    if isinstance(exc, ContractDependencyMissing):
        if template_mode and missing_dependency_is_external(exc.dependency):
            report.skipped(
                check_id,
                f"参考模板需要可选外部依赖 {exc.dependency}，当前环境未安装",
                dependency=exc.dependency,
            )
        else:
            report.failed(
                check_id,
                f"运行入口缺少依赖 {exc.dependency}：{exc}",
                dependency=exc.dependency,
            )
        return
    report.failed(
        check_id,
        str(exc) or type(exc).__name__,
        exception=type(exc).__name__,
    )


def run_check(
    report: ContractReport,
    check_id: str,
    function: Callable[[], str | tuple[str, dict[str, Any]] | None],
    *,
    template_mode: bool,
) -> bool:
    try:
        outcome = function()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        record_exception(report, check_id, exc, template_mode=template_mode)
        return False
    if isinstance(outcome, tuple):
        message, evidence = outcome
        report.passed(check_id, message, **evidence)
    else:
        report.passed(check_id, outcome or "合同检查通过")
    return True


def runtime_dependency(result: dict[str, Any]) -> str:
    if result.get("exception_type") != "ModuleNotFoundError":
        return ""
    reason = str(result.get("reason") or "")
    matched = re.search(r"No module named ['\"]([^'\"]+)", reason)
    return matched.group(1) if matched else "unknown"


def check_module_update(
    report: ContractReport,
    *,
    check_id: str,
    entry: Path,
    module: Path,
    timeout: float,
    template_mode: bool,
) -> bool:
    result = _run_module_updater(entry, module, timeout=timeout)
    if result.get("ok") is True:
        report.passed(
            check_id,
            "零参数更新入口在隔离子进程中完成",
            result_type=type(result.get("result")).__name__,
        )
        return True
    dependency = runtime_dependency(result)
    if dependency:
        record_exception(
            report,
            check_id,
            ContractDependencyMissing(
                dependency,
                str(result.get("reason") or ""),
            ),
            template_mode=template_mode,
        )
    else:
        report.failed(
            check_id,
            str(result.get("reason") or "更新入口执行失败"),
            exception=result.get("exception_type"),
        )
    return False

