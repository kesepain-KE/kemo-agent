"""Subagent package contract checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agents._runtime.schema import AgentDefinition, discover_agents
from plugins.manifest import discover_plugin_manifests

from tests.template_tests.base import begin_report, run_check
from tests.template_tests.common import (
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
    probe_python_entry,
    read_json_object,
    sandbox,
)
from tests.template_tests.contracts import ContractReport
from tests.template_tests.agent.probes import probe_agent_executor, sample_from_schema


_SAFE_PACKAGE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def validate(
    target: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
    template_mode: bool = False,
    runtime_probe: bool = True,
) -> ContractReport:
    report, directory = begin_report("agent", target)
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
        report.failed("agent.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
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
    raw = read_json_object(target / "agent.json", label="agent.json")
    raw_name = str(raw.get("name") or "").strip()
    destination_name = raw_name if _SAFE_PACKAGE_NAME.fullmatch(raw_name) else target.name
    user = "contract_user"
    with sandbox(repository_root=repository_root) as root:
        prepare_user(root, user)
        destination = copy_candidate(
            target,
            root / "users" / user / "agents" / destination_name,
        )
        holder: dict[str, AgentDefinition] = {}

        def discover() -> tuple[str, dict[str, Any]]:
            registry = discover_agents(root, user)
            definition = registry.get(raw_name)
            holder["definition"] = definition
            return "真实子代理发现器已加载清单、指令、触发和能力配置", {
                "name": definition.name,
                "executor": definition.executor,
                "exposure": definition.capabilities.exposure,
            }

        if not run_check(
            report,
            "agent.discovery",
            discover,
            template_mode=template_mode,
        ):
            return
        definition = holder["definition"]
        if definition.instruction.strip() and definition.trigger_registration.strip():
            report.passed(
                "agent.prompt_contract",
                "AGENT.md 与 trigger.md 注册信息均可进入框架",
                instruction_file=definition.instruction_file,
                trigger_file=definition.trigger_file,
            )
        else:
            report.failed("agent.prompt_contract", "子代理指令或触发注册信息为空")

        public = definition in discover_agents(root, user).public_agents("main_agent")
        if definition.capabilities.exposure == "tool":
            if "main_agent" in definition.capabilities.allowed_callers and public:
                report.passed("agent.call_policy", "主智能体可按清单权限发现并调用该子代理")
            elif "main_agent" not in definition.capabilities.allowed_callers:
                report.warning("agent.call_policy", "该子代理未授权 main_agent；只能由清单中的其他调用方使用")
            else:
                report.failed("agent.call_policy", "公开调用策略与发现结果不一致")
        else:
            report.passed("agent.call_policy", "内部子代理不会暴露给主智能体工具列表")

        plugin_names = {
            str(item.tool.get("name") or "")
            for item in discover_plugin_manifests(repository_root)
        }
        unavailable = sorted(set(definition.capabilities.plugin_tools) - plugin_names)
        if unavailable:
            report.failed(
                "agent.tool_permissions",
                "能力配置引用了框架中不存在的插件工具：" + ", ".join(unavailable),
                unavailable=unavailable,
            )
        else:
            report.passed(
                "agent.tool_permissions",
                "工具权限声明可解析；未授权工具仍由运行时隔离",
                declared=sorted(definition.capabilities.plugin_tools),
            )

        executor_imported = True
        if definition.executor != "builtin:llm":
            entry_name, _, _function = definition.executor.partition(":")
            executor_imported = run_check(
                report,
                "agent.executor_import",
                lambda: (
                    "自定义 executor 可隔离导入且签名兼容 execute(context, input_data)",
                    probe_python_entry(
                        destination / entry_name,
                        required={"execute": 2},
                        sandbox_root=root,
                        repository_root=repository_root,
                        timeout=timeout,
                    ),
                ),
                template_mode=template_mode,
            )
        else:
            report.passed("agent.executor_import", "清单选择框架内置 LLM executor")

        input_data = sample_from_schema(definition.input_schema)
        output_data = sample_from_schema(definition.output_schema)
        if not isinstance(input_data, dict) or not isinstance(output_data, dict):
            report.failed("agent.schema_samples", "子代理输入与输出 Schema 必须生成 JSON 对象")
            return
        properties = definition.input_schema.get("properties") or {}
        if "trigger" in properties:
            input_data["trigger"] = sample_from_schema(properties["trigger"])
        elif definition.input_schema.get("additionalProperties", True) is not False:
            input_data.setdefault("trigger", "default")
        report.passed("agent.schema_samples", "输入与输出 JSON Schema 可构造无副作用样例")

        if not runtime_probe:
            report.skipped("agent.executor_roundtrip", "已请求仅静态检查，未调用候选 executor")
        elif not executor_imported:
            report.skipped("agent.executor_roundtrip", "执行入口导入未完成，未重复调用")
        else:
            run_check(
                report,
                "agent.executor_roundtrip",
                lambda: (
                    "executor 使用框架上下文和假 Provider 完成输入→输出闭环",
                    probe_agent_executor(
                        sandbox_root=root,
                        user=user,
                        name=definition.name,
                        input_data=input_data,
                        output_data=output_data,
                        repository_root=repository_root,
                        timeout=timeout,
                    ),
                ),
                template_mode=template_mode,
            )
