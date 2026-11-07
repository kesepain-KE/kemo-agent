"""Read-only perception module contract checks."""

from __future__ import annotations

from typing import cast
from pathlib import Path

from run.config import load_prompt_source_registry
from run.modules import ModuleSpecError, read_manifest
from web.services.module_panels import load_module_panel, module_panel_response

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
        module = copy_candidate(target, root / "sense" / target.name)
        try:
            meta = read_manifest(module, kind_hint="sense", scope="global")
        except ModuleSpecError as exc:
            report.failed("sense.manifest", str(exc))
            return
        report.passed(
            "sense.manifest",
            "module.yaml 可被真实感知清单解析器读取",
            name=meta.name,
            update=meta.update_script.name,
            output=meta.input_file.name if meta.input_file else None,
        )
        panel_path = module / "module" / "panel.json"
        panel, panel_error = load_module_panel(module, "sense")
        if panel is None:
            if panel_error:
                report.failed("sense.panel_contract", panel_error)
                return
            if template_mode:
                report.failed(
                    "sense.panel_contract",
                    "正式感知模板缺少 module/panel.json",
                )
                return
            report.skipped("sense.panel_contract", "模块未声明用户配置组件")
        else:
            report.passed(
                "sense.panel_contract",
                "组件面板可被框架真实解析器读取",
                path=str(panel_path.relative_to(module)),
                containers=len(panel["containers"]),
            )
        entry = module / meta.update_script.name
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
        try:
            current = read_manifest(module, kind_hint="sense", scope="global")
        except ModuleSpecError as exc:
            report.failed("sense.output", str(exc))
            return
        output_text = (
            cast(Path, current.input_file).read_text("utf-8-sig").strip()
            if current.input_open else ""
        )
        if current.input_open and not output_text:
            report.failed("sense.output", "清单声明的 Markdown 数据出口为空")
            return
        if updated and current.health != "正常":
            report.failed("sense.output", "更新完成后 module.yaml.health 未变为“正常”")
            return
        report.passed(
            "sense.output",
            "Markdown 数据出口非空，健康状态与清单一致",
            chars=len(output_text),
            health=current.health,
        )
        if panel is not None:
            panel_response = module_panel_response(module, "sense")
            status_containers = [
                container
                for container in panel_response["panel"]["containers"]
                if container["kind"] == "status"
            ]
            if not status_containers or status_containers[0].get("data_error"):
                report.failed("sense.panel_runtime", "采集后组件状态文件不可读")
                return
            report.passed(
                "sense.panel_runtime",
                "采集入口已刷新可热读取的组件状态",
                fields=len(status_containers[0].get("data", {})),
            )
        registry = load_prompt_source_registry(root, "contract_user")
        selection = registry.select_perception(max_chars=1_000_000)
        if not current.input_open and not selection.text:
            report.passed("sense.prompt_injection", "清单关闭数据注入，Prompt 未注入感知输出")
        elif selection.original_items >= 1 and output_text in selection.text:
            report.passed("sense.prompt_injection", "真实 Prompt 来源注册器可发现并注入感知输出")
        else:
            report.failed("sense.prompt_injection", "感知输出未进入 Prompt 来源选择结果")
