"""User package configuration, prompt, memory, and isolation checks."""

from __future__ import annotations

from pathlib import Path

from run.config import load_config
from run.memory import MEMORY_SCHEMA_VERSION, MemoryStore, TEMPORARY_TIERS
from run.prompt import build_prompt_bundle
from run.users import list_users

from tests.template_tests.base import begin_report
from tests.template_tests.common import (
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
    read_json_object,
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
    del timeout, runtime_probe
    report, directory = begin_report("user", target)
    if directory is None:
        return report
    try:
        _validate(
            report,
            directory,
            repository_root=repository_root.resolve(),
            template_mode=bool(template_mode),
        )
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        report.failed("user.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__)
    return report


def _validate(
    report: ContractReport,
    target: Path,
    *,
    repository_root: Path,
    template_mode: bool,
) -> None:
    user = "contract_user"
    with sandbox(repository_root=repository_root) as root:
        destination = copy_candidate(target, root / "users" / user)
        required_dirs = (
            "agents",
            "avatar",
            "download",
            "expand",
            "file_upload",
            "history",
            "improve/permanent",
            "knowledge",
            "task_cron",
            "task_plan",
            "user_skills",
        )
        missing_dirs = [
            relative
            for relative in required_dirs
            if not (destination / relative).is_dir()
        ]
        if missing_dirs:
            report.failed("user.directories", "用户骨架缺少目录：" + ", ".join(missing_dirs))
        else:
            report.passed("user.directories", "用户资源、记忆、知识与工作目录均已初始化")
        prepare_user(root, user)

        try:
            raw_config = read_json_object(
                destination / "user_config.json",
                label="user_config.json",
            )
            config = load_config(user, root)
        except BaseException as exc:
            report.failed("user.config", str(exc))
            return
        report.passed(
            "user.config",
            "用户配置可与全局配置合并且保留用户隔离标识",
            schema_version=raw_config.get("schema_version"),
            user=config.get("user"),
        )
        provider = (
            raw_config.get("provider")
            if isinstance(raw_config.get("provider"), dict)
            else {}
        )
        inline_key = bool(str(provider.get("api_key") or "").strip())
        env_name = str(provider.get("api_key_env") or "").strip() or (
            "KEMO_API_KEY"
            if str(provider.get("type") or "").casefold() == "kemo"
            else "OPENAI_API_KEY"
        )
        model = str(provider.get("model") or "").strip()
        if inline_key:
            report.warning(
                "user.provider_credentials",
                "配置包含内联 API Key；验收报告不会输出密钥，部署时更建议环境变量",
            )
        else:
            report.skipped(
                "user.provider_credentials",
                f"Provider 连通性需要部署环境变量 {env_name}，合同测试不读取或发送密钥",
                environment=env_name,
            )
        if model:
            report.passed("user.provider_model", "用户已声明默认文本模型", model=model)
        elif template_mode:
            report.warning("user.provider_model", "参考模板的 provider.model 等待部署者填写")
        else:
            report.skipped("user.provider_model", "未在用户配置中固定模型；需由部署环境提供模型名")

        soul = (destination / "user_soul.md").read_text("utf-8-sig").strip()
        if not soul:
            report.failed("user.prompt", "user_soul.md 不能为空")
        else:
            try:
                bundle = build_prompt_bundle(root, user, config)
            except BaseException as exc:
                report.failed("user.prompt", f"用户 Prompt 构建失败：{exc}")
            else:
                if soul in bundle.text:
                    report.passed("user.prompt", "user_soul.md 可通过真实 Prompt 管线注入")
                else:
                    report.failed("user.prompt", "构建后的 Prompt 未包含 user_soul.md")

        store = MemoryStore(root, user, config)
        invalid_indexes: list[str] = []
        for tier in TEMPORARY_TIERS:
            try:
                raw_index = read_json_object(store.path(tier), label=f"{tier}/data.json")
                if raw_index.get("schema_version") != MEMORY_SCHEMA_VERSION:
                    invalid_indexes.append(tier)
                    continue
                store.load_index(tier)
            except BaseException:
                invalid_indexes.append(tier)
        storage = read_json_object(
            destination / "improve" / "storage.json",
            label="storage.json",
        )
        if storage.get("schema_version") != MEMORY_SCHEMA_VERSION:
            invalid_indexes.append("storage")
        if invalid_indexes:
            report.failed("user.memory", "记忆初始化结构无效：" + ", ".join(invalid_indexes))
        else:
            report.passed("user.memory", "三层临时记忆索引、永久记忆目录和存储版本均可读取")

        if (destination / "knowledge").is_dir():
            report.passed("user.knowledge", "用户知识目录存在，允许任意嵌套知识文件")
        else:
            report.failed("user.knowledge", "用户知识目录不存在")

        prepare_user(root, "contract_other_user", config={"schema_version": 1})
        users = list_users(root)
        if user in users and "contract_other_user" in users:
            report.passed("user.isolation", "用户发现与目录解析保持每用户隔离")
        else:
            report.failed("user.isolation", "用户目录隔离检查失败")

