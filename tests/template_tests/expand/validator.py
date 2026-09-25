"""Data-collection and control Expand contract checks."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from run.extensions import EXPAND_CALL_RESULT_PREFIX, EXPAND_CALL_RUNNER
from run.extensions import run_protocol_process
from run.config import load_prompt_source_registry, read_expand_meta
from web.services.module_panels import load_module_panel, module_panel_response

from tests.template_tests.base import (
    begin_report,
    check_module_update,
    record_exception,
    run_check,
    runtime_dependency,
)
from tests.template_tests.common import (
    ContractDependencyMissing,
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
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
    report, directory = begin_report("expand", target)
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
        report.failed("expand.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
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
    user = "contract_user"
    with sandbox(repository_root=repository_root) as root:
        prepare_user(root, user)
        module = copy_candidate(target, root / "users" / user / "expand" / target.name)
        meta = read_expand_meta(module)
        if not meta.valid:
            report.failed("expand.manifest", meta.error)
            return
        report.passed(
            "expand.manifest",
            "expand.json 可被真实拓展清单解析器读取",
            name=meta.name,
            input_enabled=meta.open_input,
            control_enabled=meta.open_control,
        )

        panel_path = module / "module" / "panel.json"
        panel, panel_error = load_module_panel(module, "expand")
        if panel is None:
            if panel_error:
                report.failed("expand.panel_contract", panel_error)
                return
            if template_mode:
                report.failed(
                    "expand.panel_contract",
                    "正式拓展模板缺少 module/panel.json",
                )
                return
            report.skipped("expand.panel_contract", "模块未声明用户配置组件")
        else:
            report.passed(
                "expand.panel_contract",
                "组件面板可被框架真实解析器读取",
                path=str(panel_path.relative_to(module)),
                containers=len(panel["containers"]),
            )

        update_entry = module / meta.start_update
        update_imported = run_check(
            report,
            "expand.update_import",
            lambda: (
                "采集入口提供同步零参数 update() 或 main()",
                probe_python_entry(
                    update_entry,
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
            report.skipped("expand.update_runtime", "已请求仅静态检查，未执行数据采集入口")
        elif update_imported:
            updated = check_module_update(
                report,
                check_id="expand.update_runtime",
                entry=update_entry,
                module=module,
                timeout=timeout,
                template_mode=template_mode,
            )
        else:
            report.skipped("expand.update_runtime", "采集入口导入未完成，未执行数据采集")
        update_status = next(
            (
                check.status
                for check in reversed(report.checks)
                if check.check_id == "expand.update_runtime"
            ),
            "skipped",
        )
        if update_status == "failed":
            return

        current = read_expand_meta(module)
        input_path = module / current.input_data
        if not current.valid:
            report.failed("expand.input_output", current.error)
            return
        input_text = input_path.read_text("utf-8-sig").strip() if input_path.is_file() else ""
        if current.open_input and not input_text:
            report.failed("expand.input_output", "采集已开启，但 input_data Markdown 出口为空")
            return
        if updated and current.input_health != "正常":
            report.failed("expand.input_output", "更新完成后 input_health 未变为“正常”")
            return
        report.passed(
            "expand.input_output",
            "采集端通过清单声明的 Markdown 摘要出口连接 Prompt",
            chars=len(input_text),
            health=current.input_health,
        )
        if panel is not None:
            panel_response = module_panel_response(module, "expand")
            status_containers = [
                container
                for container in panel_response["panel"]["containers"]
                if container["kind"] == "status"
            ]
            if not status_containers or status_containers[0].get("data_error"):
                report.failed("expand.panel_runtime", "采集后组件状态文件不可读")
                return
            report.passed(
                "expand.panel_runtime",
                "采集入口已刷新可热读取的组件状态",
                fields=len(status_containers[0].get("data", {})),
            )

        registry = load_prompt_source_registry(root, user)
        selection = registry.select_expand(max_chars=1_000_000)
        diagnostics = registry.selection_diagnostics().get("expand", {}).get("user", {})
        discovered = target.name in diagnostics.get("selected", [])
        input_connected = (
            not current.open_input
            or current.input_health != "正常"
            or not input_text
            or input_text in selection.text
        )
        if discovered and input_connected:
            report.passed("expand.prompt_injection", "真实 Prompt 来源注册器可发现拓展并注入采集/操控说明")
        else:
            report.failed("expand.prompt_injection", "拓展未进入 Prompt 来源选择结果")

        if not current.open_control:
            report.passed("expand.control_import", "清单明确关闭操控端；无需执行入口")
            report.skipped("expand.control_roundtrip", "操控端未开放")
            return
        control_entry = module / current.start_expand
        control_imported = run_check(
            report,
            "expand.control_import",
            lambda: (
                "操控入口兼容 execute(command, params) 或旧版 execute(command_dict)",
                probe_python_entry(
                    control_entry,
                    one_of=(("execute", 2), ("execute", 1)),
                    sandbox_root=root,
                    repository_root=repository_root,
                    timeout=timeout,
                ),
            ),
            template_mode=template_mode,
        )
        if not runtime_probe:
            report.skipped("expand.control_roundtrip", "已请求仅静态检查，未调用候选操控入口")
        elif not control_imported:
            report.skipped("expand.control_roundtrip", "操控入口导入未完成，未调用候选入口")
        else:
            returncode, payload, stdout, stderr = run_protocol_process(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    EXPAND_CALL_RUNNER,
                    str(control_entry),
                    str(module),
                ],
                cwd=module,
                timeout=timeout,
                result_prefix=EXPAND_CALL_RESULT_PREFIX,
                stdin_payload=json.dumps(
                    {"command": "contract_probe", "params": {}},
                    ensure_ascii=False,
                ),
            )
            if payload and payload.get("ok") is True:
                report.passed(
                    "expand.control_roundtrip",
                    "操控入口通过 JSON 请求/结果子进程协议返回",
                    result_type=type(payload.get("result")).__name__,
                )
            elif payload and payload.get("exception_type") == "ModuleNotFoundError":
                dependency = runtime_dependency(payload) or "unknown"
                record_exception(
                    report,
                    "expand.control_roundtrip",
                    ContractDependencyMissing(
                        dependency,
                        str(payload.get("reason") or ""),
                    ),
                    template_mode=template_mode,
                )
            elif payload and payload.get("exception_type") in {
                "KeyError",
                "NotImplementedError",
                "PermissionError",
                "ValueError",
            }:
                report.passed(
                    "expand.control_roundtrip",
                    "操控入口将未知探测命令转换为结构化拒绝",
                    rejection=payload.get("exception_type"),
                )
            else:
                detail = str((payload or {}).get("reason") or stderr or stdout).strip()[-800:]
                report.failed(
                    "expand.control_roundtrip",
                    detail or f"操控协议子进程退出码为 {returncode}",
                )

        if panel is not None and template_mode:
            controls = [
                control
                for container in panel["containers"]
                if container["kind"] == "action"
                for control in container["controls"]
            ]
            if not controls:
                report.passed("expand.panel_actions", "正式模板没有声明额外操控按钮")
            elif not runtime_probe:
                report.skipped("expand.panel_actions", "已请求仅静态检查，未调用面板动作")
            elif not control_imported:
                report.skipped("expand.panel_actions", "操控入口导入未完成，未调用面板动作")
            else:
                failed_action = ""
                failure_detail = ""
                for control in controls:
                    command = str(control.get("command") or "")
                    params = {
                        field["key"]: field.get("default", "")
                        for field in control.get("inputs", [])
                    }
                    _, action_payload, action_stdout, action_stderr = run_protocol_process(
                        [
                            sys.executable,
                            "-I",
                            "-c",
                            EXPAND_CALL_RUNNER,
                            str(control_entry),
                            str(module),
                        ],
                        cwd=module,
                        timeout=timeout,
                        result_prefix=EXPAND_CALL_RESULT_PREFIX,
                        stdin_payload=json.dumps(
                            {"command": command, "params": params},
                            ensure_ascii=False,
                        ),
                    )
                    action_result = (
                        action_payload.get("result")
                        if isinstance(action_payload, dict)
                        else None
                    )
                    if (
                        not isinstance(action_payload, dict)
                        or action_payload.get("ok") is not True
                        or not isinstance(action_result, dict)
                        or action_result.get("ok") is False
                    ):
                        failed_action = command
                        failure_detail = str(
                            (action_result or {}).get("error")
                            if isinstance(action_result, dict)
                            else (action_payload or {}).get("reason")
                            if isinstance(action_payload, dict)
                            else action_stderr or action_stdout
                        )
                        break
                if failed_action:
                    report.failed(
                        "expand.panel_actions",
                        f"面板声明的命令未被默认操控入口真实实现：{failed_action}（{failure_detail}）",
                    )
                else:
                    report.passed(
                        "expand.panel_actions",
                        "正式模板声明的组件动作均能通过真实拓展协议执行",
                        commands=[control["command"] for control in controls],
                    )
