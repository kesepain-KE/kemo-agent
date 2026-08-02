"""User package configuration, prompt, memory, and isolation checks."""

from __future__ import annotations

from pathlib import Path

from run.config import load_config
from run.memory import MemoryStore
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
        report.failed(
            "user.validator", f"验收器未能完成：{exc}", exception=type(exc).__name__
        )
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
            "improve",
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
            report.failed(
                "user.directories", "用户骨架缺少目录：" + ", ".join(missing_dirs)
            )
        else:
            report.passed(
                "user.directories", "用户资源、记忆、知识与工作目录均已初始化"
            )
        if template_mode:
            history_entries = sorted(
                item.name
                for item in (destination / "history").iterdir()
                if item.name not in {".gitignore", ".gitkeep"}
            )
            if history_entries:
                report.failed(
                    "user.history_template",
                    "参考模板不得携带数据库或旧历史内容：" + ", ".join(history_entries),
                )
            else:
                report.passed(
                    "user.history_template",
                    "history 仅保留目录标记，SQLite 将由运行时按 schema 创建",
                )
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
            report.warning(
                "user.provider_model", "参考模板的 provider.model 等待部署者填写"
            )
        else:
            report.skipped(
                "user.provider_model", "未在用户配置中固定模型；需由部署环境提供模型名"
            )

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
                    report.passed(
                        "user.prompt", "user_soul.md 可通过真实 Prompt 管线注入"
                    )
                else:
                    report.failed("user.prompt", "构建后的 Prompt 未包含 user_soul.md")

        improve = destination / "improve"
        legacy_files = sorted(
            path.relative_to(improve).as_posix()
            for path in improve.rglob("*")
            if path.is_file() and path.name not in {".gitkeep", "memory.sqlite3"}
        )
        prebuilt_database = (
            template_mode and (target / "improve" / "memory.sqlite3").exists()
        )
        store = MemoryStore(root, user, config)
        issues = store.integrity_issues()
        if prebuilt_database:
            report.failed("user.memory", "参考模板不得预置二进制 memory.sqlite3")
        elif legacy_files:
            report.failed(
                "user.memory", "记忆目录仍包含旧文件结构：" + ", ".join(legacy_files)
            )
        elif issues:
            report.failed(
                "user.memory", "SQLite 记忆库完整性异常：" + ", ".join(issues)
            )
        elif store.database_path().is_file():
            report.passed("user.memory", "每用户 SQLite 记忆库可初始化并通过完整性检查")
        else:
            report.failed("user.memory", "memory.sqlite3 未能初始化")

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
