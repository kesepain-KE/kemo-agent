"""The single application entrypoint for kemo-agent updates.

The repository-root ``update.py`` is intentionally only a compatibility shim;
all parsing, source validation, transaction control and board execution live
here so there is one update implementation for CLI, tests and ``python -m
update``.
"""

from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

from ._utils import (
    UpdateError,
    ask_yes_no,
    command_exists,
    compare_versions,
    format_command,
    green,
    redact_text,
    red,
    require_commands,
    yellow,
)
from .build import build_web_frontend, refresh_dependencies
from .constants import (
    DEFAULT_BRANCH,
    DEFAULT_REPO_URL,
    DEFAULT_REPOSITORY_SLUG,
    MODULES,
    ROOT,
    VERSION_URL_TEMPLATE,
)
from .dispatcher import run_modules
from .manifest import (
    ensure_no_downgrade,
    finalize_version_document,
    load_local_version_document,
    load_remote_version_document,
    load_source_version_document,
    print_version_report,
    verify_source_manifest,
    version_for_module,
)
from .migrations import (
    initialize_runtime_state_databases,
    initialize_user_memory_databases,
    migrate_user_skeletons,
)
from .source import clone_latest, source_revision
from .transaction import update_transaction


def should_update(
    local_version: str,
    remote_version: str,
    *,
    force: bool,
    assume_yes: bool,
) -> bool:
    comparison = compare_versions(local_version, remote_version)
    print(f"本地版本  : {local_version}")
    print(f"远程版本  : {remote_version}")
    if comparison == 0:
        if force:
            print(yellow("版本相同，强制重新安装指定板块。"))
            return True
        print(green("当前已经是目标版本；如需重新安装，请显式使用 --force。"))
        return False
    if comparison < 0:
        print(green(f"发现更新: {local_version} -> {remote_version}"))
        return ask_yes_no("是否继续更新？", default=True, assume_yes=assume_yes)
    print(yellow(f"本地版本更新于远程: {local_version} > {remote_version}"))
    print(yellow("自动降级已禁用；请使用经过验证的备份恢复旧版本。"))
    return False


def _print_platform_warning(*, require_git: bool = True) -> None:
    """Print platform guidance without blocking read-only version checks.

    ``--check`` against the default repository only reads the public manifest
    and does not need Git.  Keeping that path independent from an optional
    local Git installation makes health checks usable on a fresh deployment.
    Update operations still pass ``require_git=True`` and fail early with a
    clear message when cloning cannot be performed.
    """

    if platform.system().lower() != "windows":
        return
    if not command_exists("git"):
        if require_git:
            print(yellow("[warning] Windows 上未检测到 git 命令"))
            print(yellow("  请安装 Git for Windows。"))
            raise SystemExit(1)
        print(yellow("[info] Windows 模式 - 版本检查不需要 git，已跳过。"))
        return
    print(yellow("[info] Windows 模式 - 确保 git 命令可从当前终端访问。"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按板块更新 kemo-agent（全平台）")
    parser.add_argument(
        "--module", choices=["all", *MODULES], default="all", help="更新板块，默认 all"
    )
    parser.add_argument("--check", action="store_true", help="仅检查本地和远程版本")
    parser.add_argument("--force", action="store_true", help="版本相同时强制重新安装")
    parser.add_argument(
        "--yes", "-y", action="store_true", help="确认更新；全局配置仍默认保留本地版本"
    )
    parser.add_argument("--dry-run", action="store_true", help="仅展示计划的操作，不实际修改")
    parser.add_argument(
        "--skip-web-build",
        action="store_true",
        help="仅允许 dry-run；正式更新必须完成 Web 构建",
    )
    parser.add_argument(
        "--skip-deps",
        action="store_true",
        help="仅允许 dry-run；正式更新必须完成 Python 依赖刷新",
    )
    parser.add_argument(
        "--replace-global-config",
        action="store_true",
        help="明确允许用远程版本覆盖 config/global_config.json",
    )
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="Git 仓库 URL")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Git 分支")
    parser.add_argument(
        "--remote-version-url", default="", help="远程 version.json URL 覆盖"
    )
    return parser.parse_args(sys.argv[1:] if argv is None else argv)


def _default_remote_url(args: argparse.Namespace) -> str:
    return args.remote_version_url or VERSION_URL_TEMPLATE.format(branch=args.branch)


def _normalized_repo_url(value: str) -> str:
    text = value.strip().replace("\\", "/").rstrip("/").lower()
    if text.endswith(".git"):
        text = text[:-4]
    return text


def _is_default_repository(value: str) -> bool:
    normalized = _normalized_repo_url(value)
    slug = DEFAULT_REPOSITORY_SLUG.lower()
    return normalized == _normalized_repo_url(DEFAULT_REPO_URL) or normalized.endswith(
        "/" + slug
    ) or normalized.endswith(":" + slug)


def _print_backup_hint(backup_dir: Path | None) -> None:
    if backup_dir is not None:
        print(yellow(f"更新前备份仍保留在: {backup_dir}"), file=sys.stderr)


def print_module_summary(results: list[dict]) -> None:
    print(f"\n{'=' * 50}")
    print("  更新板块汇总")
    print(f"{'=' * 50}")
    for result in results:
        name = str(result.get("module", "unknown"))
        label = MODULES.get(name, (name, ""))[0]
        status = str(result.get("status", "failed"))
        print(f"[{status.upper():7}] {label}")
        for detail in result.get("details", []):
            print(f"  - {redact_text(detail)}")
        for warning in result.get("warnings", []):
            print(yellow(f"  ! {redact_text(warning)}"))


def _load_remote_for_run(
    args: argparse.Namespace,
    *,
    temporary_root: Path,
) -> tuple[dict, Path | None]:
    """Load one source snapshot and its manifest.

    A custom repository cannot safely use the default GitHub raw manifest, so
    it is cloned first and its own version.json becomes the source of truth.
    For the default repository we fetch the manifest early for a cheap version
    check, then verify it again against the exact clone before writing.
    """

    if not _is_default_repository(args.repo_url) and not args.remote_version_url:
        require_commands(["git"])
        source = clone_latest(args.repo_url, args.branch, temporary_root)
        return load_source_version_document(source), source
    return load_remote_version_document(_default_remote_url(args)), None


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    args = parse_args(argv)
    backup_dir: Path | None = None
    try:
        needs_git = (not args.check) or (
            not _is_default_repository(args.repo_url)
            and not args.remote_version_url
        )
        _print_platform_warning(require_git=needs_git)
        if args.force:
            print(yellow("--force 只允许同版本重新安装，不允许安装低于本地的版本。"))
        selected_modules = list(MODULES) if args.module == "all" else [args.module]
        if not args.dry_run and not args.check and (
            ("web" in selected_modules and args.skip_web_build)
            or ("core" in selected_modules and args.skip_deps)
        ):
            raise UpdateError(
                "正式更新不能跳过 Web 构建或 Python 依赖刷新；"
                "请先用 --dry-run 预览，或准备好构建环境后重新执行。"
            )

        local_document = load_local_version_document(root=root)
        if needs_git:
            require_commands(["git"])
        with tempfile.TemporaryDirectory(prefix="kemo-agent-update-") as temporary:
            temporary_root = Path(temporary)
            remote_document, source = _load_remote_for_run(
                args,
                temporary_root=temporary_root,
            )

            if args.check:
                print_version_report(local_document, remote_document, args.module)
                return 0

            local_version = version_for_module(local_document, args.module)
            remote_version = version_for_module(remote_document, args.module)
            ensure_no_downgrade(local_document, remote_document, args.module)
            if not should_update(
                local_version,
                remote_version,
                force=args.force,
                assume_yes=args.yes,
            ):
                print(yellow("更新已取消"))
                return 0

            if source is None:
                source = clone_latest(args.repo_url, args.branch, temporary_root)
            source_document = load_source_version_document(source)
            verify_source_manifest(remote_document, source_document)
            print(green(f"已锁定远程源码提交: {source_revision(source)}"))

            with update_transaction(root=root, dry_run=args.dry_run) as created_backup:
                backup_dir = created_backup
                results = run_modules(
                    selected_modules,
                    source,
                    root,
                    dry_run=args.dry_run,
                    assume_yes=args.yes,
                    replace_global_config=args.replace_global_config,
                    stop_on_failure=True,
                )
                print_module_summary(results)
                if any(result.get("status") == "failed" for result in results):
                    raise UpdateError("更新板块失败，已停止后续板块")
                if any(result.get("status") == "partial" for result in results):
                    raise UpdateError("更新板块不完整，版本号不会提交")

                if "web" in selected_modules and not args.skip_web_build:
                    build_web_frontend(root=root, dry_run=args.dry_run)
                if "core" in selected_modules and not args.skip_deps:
                    refresh_dependencies(root=root, dry_run=args.dry_run)

                # Migrations run only after source, frontend and dependency
                # work succeeds.  This keeps user/runtime state untouched
                # when an earlier build step fails and the transaction needs
                # to restore the source tree.
                if "core" in selected_modules:
                    migrate_user_skeletons(root=root, dry_run=args.dry_run)
                    initialize_user_memory_databases(root=root, dry_run=args.dry_run)
                    initialize_runtime_state_databases(root=root, dry_run=args.dry_run)

                finalize_version_document(
                    local_document,
                    remote_document,
                    args.module,
                    dry_run=args.dry_run,
                    root=root,
                )

        print(green("update complete"))
        _print_backup_hint(backup_dir)
        return 0
    except subprocess.CalledProcessError as exc:
        print(red(f"命令失败: {format_command(exc.cmd)}"), file=sys.stderr)
        if exc.stdout:
            print(redact_text(exc.stdout), file=sys.stderr)
        if exc.stderr:
            print(redact_text(exc.stderr), file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except UpdateError as exc:
        print(red(redact_text(exc)), file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except OSError as exc:
        print(red(f"文件系统操作失败: {redact_text(exc)}"), file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except KeyboardInterrupt:
        print()
        print(yellow("更新已中断；若已开始写入，更新器会先尝试自动恢复"))
        _print_backup_hint(backup_dir)
        return 130
