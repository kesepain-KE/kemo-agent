"""User package configuration, prompt, memory, and isolation checks."""

from __future__ import annotations

import re
from pathlib import Path

from run.config import load_config
from run.memory import MemoryStore
from run.config import build_prompt_bundle
from run.config import list_users
from run.config import MainAgentSourcePolicy
from run.config.user_skeleton import (
    REQUIRED_USER_DIRS,
    TEMPLATE_ONLY_EMPTY_DIRS,
    TEMPLATE_BACKGROUND_FILE,
)

from tests.template_tests.base import begin_report
from tests.template_tests.common import (
    PROJECT_ROOT,
    copy_candidate,
    prepare_user,
    read_json_object,
    sandbox,
)
from tests.template_tests.contracts import ContractReport


_MEMORY_BACKUP_RE = re.compile(
    r"^memory\.backup_\d{8}_\d{6}\.sqlite3(?:-(?:shm|wal))?$"
)


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
        required_dirs = REQUIRED_USER_DIRS
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
            from PIL import Image

            try:
                background = destination / TEMPLATE_BACKGROUND_FILE
                if background.stat().st_size > 100 * 1024 * 1024:
                    raise ValueError("默认背景超过 100 MB")
                with Image.open(background) as image:
                    if image.format != "WEBP":
                        raise ValueError("默认背景必须为有效 WebP")
                    image.verify()
                report.passed("user.background_template", "默认背景为静态 WebP，仅新用户复制、默认不启用")
            except (OSError, ValueError) as exc:
                report.failed("user.background_template", str(exc))
            for relative in TEMPLATE_ONLY_EMPTY_DIRS:
                entries = sorted(
                    item.name for item in (destination / relative).iterdir()
                )
                if entries != [".gitkeep"]:
                    report.failed(
                        "user.empty_template_directories",
                        f"{relative} 在参考模板中只能保留 .gitkeep："
                        + (", ".join(entries) or "为空"),
                    )
                else:
                    report.passed(
                        f"user.{relative.replace('/', '.')}.template",
                        f"{relative} 仅保留目录标记，不预置运行数据",
                    )
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
            completion_sound_files = sorted(
                item.name
                for item in destination.iterdir()
                if item.is_file()
                and item.name.casefold().startswith("completion_sound.")
            )
            completion_sound_dir = destination / "completion_sound"
            if completion_sound_dir.is_dir():
                report.failed(
                    "user.completion_sound_directory",
                    "用户模板不得创建单独的结束音效目录",
                )
            elif completion_sound_files:
                report.failed(
                    "user.completion_sound_template",
                    "用户模板不得预置结束音效文件：" + ", ".join(completion_sound_files),
                )
            else:
                report.passed(
                    "user.completion_sound_template",
                    "结束音效按需上传，模板不预置文件",
                )
            failure_sound_files = sorted(
                item.name
                for item in destination.iterdir()
                if item.is_file()
                and item.name.casefold().startswith("failure_sound.")
            )
            failure_sound_dir = destination / "failure_sound"
            if failure_sound_dir.is_dir():
                report.failed(
                    "user.failure_sound_directory",
                    "用户模板不得创建单独的失败音效目录",
                )
            elif failure_sound_files:
                report.failed(
                    "user.failure_sound_template",
                    "用户模板不得预置失败音效文件：" + ", ".join(failure_sound_files),
                )
            else:
                report.passed(
                    "user.failure_sound_template",
                    "失败音效按需上传，模板不预置文件",
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
        try:
            MainAgentSourcePolicy.from_config(raw_config)
        except BaseException as exc:
            report.failed("user.config_fields", f"用户来源配置字段无效：{exc}")
        else:
            report.passed(
                "user.config_fields",
                "skills、expand、perception、knowledge、plugins 字段通过真实来源策略校验",
            )
        invalid_policy = dict(raw_config)
        invalid_skills = dict(raw_config.get("skills") or {})
        invalid_skills["user_whitelist"] = []
        invalid_policy["skills"] = invalid_skills
        try:
            MainAgentSourcePolicy.from_config(invalid_policy)
        except BaseException:
            report.passed(
                "user.config_fields_removed",
                "已移除的用户技能白名单字段会被真实来源策略拒绝",
            )
        else:
            report.failed(
                "user.config_fields_removed",
                "来源策略意外接受已移除的 skills.user_whitelist 字段",
            )
        provider = (
            (checked_204 if isinstance(checked_204 := raw_config.get("provider"), dict) else {})
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
        all_legacy_files = sorted(
            path.relative_to(improve).as_posix()
            for path in improve.rglob("*")
            if path.is_file() and path.name not in {".gitkeep", "memory.sqlite3"}
        )
        preserved_backups = [
            relative
            for relative in all_legacy_files
            if _MEMORY_BACKUP_RE.fullmatch(Path(relative).name)
        ]
        legacy_files = (
            all_legacy_files
            if template_mode
            else [
                relative
                for relative in all_legacy_files
                if relative not in preserved_backups
            ]
        )
        if template_mode:
            preserved_backups = []
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
        else:
            if preserved_backups and not template_mode:
                report.warning(
                    "user.memory_backups",
                    "保留现有记忆库备份，不将其导入当前运行库："
                    + ", ".join(preserved_backups),
                )
            if issues:
                report.failed(
                    "user.memory", "SQLite 记忆库完整性异常：" + ", ".join(issues)
                )
            elif store.database_path().is_file():
                report.passed(
                    "user.memory", "每用户 SQLite 记忆库可初始化并通过完整性检查"
                )
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
