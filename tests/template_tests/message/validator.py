"""External message transport contract checks without live platform effects."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from message.plugin import (
    MessagePluginConfig,
    _normalize_state,
    discover_message_plugins,
    parse_message_buffer,
)
from run.users import validate_user_name

from tests.template_tests.base import begin_report, record_exception, run_check
from tests.template_tests.common import (
    ContractValidationError,
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
    probe_python_entry,
    read_json_object,
    sandbox,
)
from tests.template_tests.contracts import ContractReport


_SAFE_DIRECTORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def validate(
    target: Path,
    *,
    repository_root: Path = PROJECT_ROOT,
    timeout: float = 10.0,
    template_mode: bool = False,
    runtime_probe: bool = True,
) -> ContractReport:
    del runtime_probe  # Live start/send/check is never part of generic validation.
    report, directory = begin_report("message", target)
    if directory is None:
        return report
    try:
        _validate(
            report,
            directory,
            repository_root=repository_root.resolve(),
            timeout=max(0.1, float(timeout)),
            template_mode=bool(template_mode),
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.failed("message.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
    return report


def _validate(
    report: ContractReport,
    target: Path,
    *,
    repository_root: Path,
    timeout: float,
    template_mode: bool,
) -> None:
    raw = read_json_object(target / "message.json", label="message.json")
    bound_user = str(raw.get("bound_user") or "").strip()
    platform = str(raw.get("platform") or target.name).strip().casefold() or target.name
    destination_name = platform if _SAFE_DIRECTORY.fullmatch(platform) else target.name
    with sandbox(repository_root=repository_root) as root:
        if bound_user:
            try:
                validate_user_name(bound_user)
                prepare_user(root, bound_user)
            except Exception:
                prepare_user(root, "contract_user")
        module = copy_candidate(target, root / "message" / "out" / destination_name)
        holder: dict[str, MessagePluginConfig] = {}

        def load_manifest() -> tuple[str, dict[str, Any]]:
            config = MessagePluginConfig.load(root, module)
            holder["config"] = config
            return "message.json 可被真实外部消息配置解析器读取", {
                "platform": config.platform,
                "machine_id": config.machine_id,
                "bound_user": config.bound_user,
            }

        if not run_check(
            report,
            "message.manifest",
            load_manifest,
            template_mode=template_mode,
        ):
            return
        config = holder["config"]
        try:
            state = read_json_object(config.state_path, label="state.json")
            normalized = _normalize_state(state)
        except BaseException as exc:
            record_exception(report, "message.state", exc, template_mode=template_mode)
        else:
            report.passed(
                "message.state",
                "state.json 符合健康、计数和输入保活状态合同",
                health=normalized["health"],
                input_status=normalized["input_status"],
            )

        buffer_text = (
            config.buffer_path.read_text("utf-8-sig")
            if config.buffer_path.is_file()
            else ""
        )
        try:
            existing = parse_message_buffer(buffer_text)
        except BaseException as exc:
            report.failed("message.buffer_initial", f"初始消息缓冲不可消费：{exc}")
        else:
            report.passed(
                "message.buffer_initial",
                "初始 message.md 为空或包含可消费的完整消息",
                messages=len(existing),
            )

        synthetic = (
            "---\n"
            f"machine_id: {config.machine_id}\n"
            "message_id: contract-message-1\n"
            "chat_type: private\n"
            "external_user_id: contract-user\n"
            "external_chat_id: contract-chat\n"
            "timestamp: 2026-07-28T00:00:00+00:00\n"
            "---\n"
            "contract payload\n"
        )
        try:
            parsed = parse_message_buffer(synthetic)
            if len(parsed) != 1 or parsed[0].text != "contract payload":
                raise ContractValidationError("合成消息解析结果与输入不一致")
        except BaseException as exc:
            report.failed("message.buffer_roundtrip", str(exc))
        else:
            report.passed("message.buffer_roundtrip", "YAML front matter 入站消息可转换为统一消息对象")

        import_ready = True
        for role, required in (
            ("input", {"start": 4, "stop": 0}),
            ("output", {"send": 1}),
            ("detect", {"check": 2}),
        ):
            check_id = f"message.{role}_entry"
            try:
                result = probe_python_entry(
                    config.module_path(role),
                    required=required,
                    sandbox_root=root,
                    repository_root=repository_root,
                    timeout=timeout,
                )
            except BaseException as exc:
                import_ready = False
                record_exception(report, check_id, exc, template_mode=template_mode)
            else:
                report.passed(
                    check_id,
                    f"{role} 入口可隔离导入且生命周期签名正确",
                    functions=result.get("functions", []),
                )
        if not import_ready:
            report.skipped(
                "message.discovery",
                "部分平台依赖未就绪，未在主进程重复导入 Transport；静态配置仍已验证",
            )
            return

        import message as message_package

        message_path = str(root / "message")
        package_path = message_package.__path__
        package_path.insert(0, message_path)
        try:
            transports, issues = discover_message_plugins(root)
        finally:
            try:
                package_path.remove(message_path)
            except ValueError:
                pass
        if issues or len(transports) != 1 or transports[0].name != config.platform:
            report.failed(
                "message.discovery",
                "真实 Transport 发现结果异常：" + "; ".join(issue.error for issue in issues),
            )
        else:
            report.passed(
                "message.discovery",
                "真实 Transport 发现器完成平台加载；未启动网络、发送消息或在线检测",
            )

