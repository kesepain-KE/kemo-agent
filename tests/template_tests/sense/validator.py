"""Read-only perception module contract checks."""

from __future__ import annotations

from pathlib import Path

from run.config import _read_sense_meta, load_prompt_source_registry

from tests.template_tests.base import begin_report, check_module_update, run_check
from tests.template_tests.common import (
    PROJECT_ROOT,
    copy_candidate,
    probe_python_entry,
    sandbox,
)
from tests.template_tests.contracts import ContractReport


def validate(
    target: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
    template_mode: bool = False,
    runtime_probe: bool = True,
) -> ContractReport:
    report, directory = begin_report("sense", target)
    if directory is None:
        return report
    try:
        _validate(
            report,
            directory,
            repository_root=repository_root.resolve(),
            timeout=max(0.1, float(timeout)),
            template_mode=bool(template_mode),
            runtime_probe=bool(runtime_probe),
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.failed("sense.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
    return report


def _validate(
    report: ContractReport,
    target: Path,
    *,
    repository_root: Path,
    timeout: float,
    template_mode: bool,
    runtime_probe: bool,
) -> None:
    with sandbox(repository_root=repository_root) as root:
        module = copy_candidate(target, root / "global_sense" / target.name)
        meta = _read_sense_meta(module)
        if not meta.valid:
            report.failed("sense.manifest", meta.error)
            return
        report.passed(
            "sense.manifest",
            "sense.json 可被真实感知清单解析器读取",
            name=meta.name,
            update=meta.start_update,
            output=meta.data_md,
        )
        entry = module / meta.start_update
        imported = run_check(
            report,
            "sense.update_import",
            lambda: (
                "更新脚本提供同步零参数 update() 或 main()",
                probe_python_entry(
                    entry,
                    one_of=(("update", 0), ("main", 0)),
                    sandbox_root=root,
                    repository_root=repository_root,
                    timeout=timeout,
                ),
            ),
            template_mode=template_mode,
        )
        updated = False
        if not runtime_probe:
            report.skipped("sense.update_runtime", "已请求仅静态检查，未执行数据采集入口")
        elif imported:
            updated = check_module_update(
                report,
                check_id="sense.update_runtime",
                entry=entry,
                module=module,
                timeout=timeout,
                template_mode=template_mode,
            )
        else:
            report.skipped("sense.update_runtime", "更新入口导入未完成，未执行数据采集")
        update_status = next(
            (
                check.status
                for check in reversed(report.checks)
                if check.check_id == "sense.update_runtime"
            ),
            "skipped",
        )
        if update_status == "failed":
            return
        current = _read_sense_meta(module)
        if not current.valid:
            report.failed("sense.output", current.error)
            return
        output_text = current.data_md_path.read_text("utf-8-sig").strip()
        if not output_text:
            report.failed("sense.output", "清单声明的 Markdown 数据出口为空")
            return
        if updated and current.health != "正常":
            report.failed("sense.output", "更新完成后 sense.json.health 未变为“正常”")
            return
        report.passed(
            "sense.output",
            "Markdown 数据出口非空，健康状态与清单一致",
            chars=len(output_text),
            health=current.health,
        )
        registry = load_prompt_source_registry(root, "contract_user")
        selection = registry.select_perception(max_chars=1_000_000)
        if selection.original_items >= 1 and output_text in selection.text:
            report.passed("sense.prompt_injection", "真实 Prompt 来源注册器可发现并注入感知输出")
        else:
            report.failed("sense.prompt_injection", "感知输出未进入 Prompt 来源选择结果")

