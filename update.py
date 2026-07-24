#!/usr/bin/env python3
"""Modular, cross-platform updater for kemo-agent."""

from __future__ import annotations

import argparse
import copy
import importlib
import importlib.util
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from update._utils import (
    UpdateError,
    _resolve_npm_command,
    ask_yes_no,
    command_exists,
    compare_versions,
    fetch_json,
    green,
    parse_version,
    read_json,
    red,
    require_commands,
    run,
    sync_directory,
    write_json_atomic,
    yellow,
)


APP_NAME = "kemo-agent"
DEFAULT_REPO_URL = "https://github.com/kesepain-KE/kemo-agent.git"
DEFAULT_BRANCH = "main"
VERSION_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/kesepain-KE/kemo-agent/{branch}/version.json"
)
BACKUP_KEEP = 2

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web" / "frontend"

BACKUP_EXCLUDES = (
    ".git/",
    ".venv/",
    "venv/",
    ".backups/",
    "users/",
    "shared_skills/",
    "web/node_modules/",
    "web/dist/",
    "web/frontend/node_modules/",
    "web/frontend/dist/",
    "tmp/",
    "__pycache__/",
)

MODULES = {
    "core": ("核心引擎", "update.core"),
    "agents": ("智能体系统", "update.agents"),
    "plugins": ("插件生态", "update.plugins"),
    "web": ("Web 服务", "update.web"),
}
REMOTE_UPDATE_PACKAGE = "_kemo_agent_remote_update"


def make_backup(dry_run: bool) -> Path | None:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / ".backups" / f"update-{timestamp}"
    if dry_run:
        print(f"[dry-run] 将创建备份: {backup_dir}")
        return None
    backup_dir.mkdir(parents=True, exist_ok=False)
    sync_directory(ROOT, backup_dir, delete=False, excludes=BACKUP_EXCLUDES)
    print(green(f"备份已创建: {backup_dir}"))
    prune_backups()
    return backup_dir


def prune_backups() -> None:
    backups_root = ROOT / ".backups"
    if not backups_root.is_dir():
        return
    backups = sorted(
        [path for path in backups_root.iterdir() if path.is_dir() and path.name.startswith("update-")],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in backups[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
        print(yellow(f"已移除旧备份: {old}"))


def clone_latest(repo_url: str, branch: str, work_dir: Path) -> Path:
    target = work_dir / "source"
    run(["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target)])
    return target


def migrate_user_skeletons(*, dry_run: bool) -> None:
    users_dir = ROOT / "users"
    if not users_dir.is_dir():
        return
    candidates = [
        path
        for path in sorted(users_dir.iterdir())
        if path.is_dir() and (path / "user_config.json").is_file()
    ]
    if not candidates:
        return
    if dry_run:
        print(f"[dry-run] 将补齐 {len(candidates)} 个用户的目录骨架")
        return
    try:
        sys.path.insert(0, str(ROOT))
        from run.users import ensure_user

        for user_dir in candidates:
            ensure_user(user_dir.name, ROOT)
        print(green(f"用户目录骨架已补齐: {len(candidates)} 个用户"))
    except Exception as exc:
        print(yellow(f"用户目录补齐跳过: {exc}"))


def migrate_user_memories(*, dry_run: bool) -> None:
    users_dir = ROOT / "users"
    if not users_dir.is_dir():
        return
    candidates = [
        path
        for path in sorted(users_dir.iterdir())
        if path.is_dir()
        and not path.name.startswith("_")
        and (path / "user_config.json").is_file()
        and (path / "improve").is_dir()
        and not (path / "improve" / "storage.json").is_file()
    ]
    if not candidates:
        return
    if dry_run:
        print(f"[dry-run] 将迁移 {len(candidates)} 个用户的文件型记忆到 schema v2")
        return
    try:
        sys.path.insert(0, str(ROOT))
        from run.memory_migrate import migrate_user_memory

        reports = [migrate_user_memory(ROOT, path.name) for path in candidates]
        migrated = sum(1 for report in reports if report.migrated)
        print(green(f"用户记忆已迁移到 schema v2: {migrated} 个用户"))
    except Exception as exc:
        raise UpdateError(f"用户记忆迁移失败，旧数据已保留：{exc}") from exc


def build_web_frontend(*, dry_run: bool) -> None:
    package_json = WEB_DIR / "package.json"
    if not package_json.is_file():
        raise UpdateError(f"未找到前端构建配置: {package_json}")
    if dry_run:
        print(f"[dry-run] 将在 {WEB_DIR} 执行 npm install && npm run build")
        return
    npm_command = _resolve_npm_command()
    dist_dir = WEB_DIR / "dist"
    if not npm_command:
        raise UpdateError(
            "未找到 npm，无法重新构建已更新的 Web 前端。"
            "请安装 Node.js（https://nodejs.org/），然后手动执行: "
            "cd web/frontend && npm install && npm run build"
        )
    print(green("正在构建 Web 前端..."))
    run([npm_command, "install"], cwd=WEB_DIR)
    run([npm_command, "run", "build"], cwd=WEB_DIR)
    if not dist_dir.is_dir() or not any(dist_dir.iterdir()):
        raise UpdateError("Web 前端构建失败：web/frontend/dist 为空")
    print(green("Web 前端已构建"))


def refresh_dependencies(*, dry_run: bool) -> None:
    requirements = ROOT / "requirements.txt"
    if not requirements.is_file():
        print(yellow("未找到 requirements.txt，跳过依赖刷新"))
        return
    if dry_run:
        print("[dry-run] 将执行 pip install -r requirements.txt")
        return
    print(green("正在刷新 Python 依赖..."))
    run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])
    print(green("依赖已刷新"))


def load_version_documents(remote_url: str) -> tuple[dict, dict]:
    local_path = ROOT / "version.json"
    if not local_path.is_file():
        raise UpdateError(f"未找到本地版本文件: {local_path}")
    return read_json(local_path), fetch_json(remote_url)


def validate_version_document(document: dict, label: str) -> None:
    try:
        version_for_module(document, "all")
        for module_name in MODULES:
            version_for_module(document, module_name)
    except UpdateError as exc:
        raise UpdateError(f"{label}版本文件无效: {exc}") from exc


def version_for_module(document: dict, module: str) -> str:
    if module == "all":
        value = document.get("version")
    else:
        components = document.get("components")
        if not isinstance(components, dict) or not isinstance(components.get(module), dict):
            raise UpdateError(f"version.json 缺少 components.{module}")
        value = components[module].get("version")
    version = str(value or "").strip()
    if not version:
        field = "version" if module == "all" else f"components.{module}.version"
        raise UpdateError(f"version.json 缺少 {field}")
    parse_version(version)
    return version


def load_versions(remote_url: str, module: str = "all") -> tuple[str, str]:
    local, remote = load_version_documents(remote_url)
    return version_for_module(local, module), version_for_module(remote, module)


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
        return ask_yes_no("版本相同，仍要重新安装？", default=False, assume_yes=assume_yes)
    if comparison < 0:
        print(green(f"发现更新: {local_version} -> {remote_version}"))
        return ask_yes_no("是否继续更新？", default=True, assume_yes=assume_yes)
    print(yellow(f"本地版本更新于远程: {local_version} > {remote_version}"))
    print(yellow("这可能导致降级。"))
    return ask_yes_no("是否继续？", default=False, assume_yes=assume_yes)


def run_modules(
    module_names: list[str],
    source_root: Path,
    target_root: Path,
    *,
    dry_run: bool,
    assume_yes: bool,
) -> list[dict]:
    results: list[dict] = []
    remote_package = _load_remote_update_package(source_root)
    try:
        for name in module_names:
            label, configured_import_path = MODULES[name]
            board_name = configured_import_path.rsplit(".", 1)[-1]
            import_path = f"{remote_package}.{board_name}"
            print(f"\n{'=' * 50}")
            print(f"  板块: {label}")
            print(f"{'=' * 50}")
            try:
                importlib.invalidate_caches()
                module = importlib.import_module(import_path)
                update_kwargs = {
                    "dry_run": dry_run,
                    "assume_yes": assume_yes,
                }
                if name == "web":
                    # The default remains enabled only for the 0.1.x dispatcher,
                    # which imports the refreshed web board after its old core pass.
                    update_kwargs["legacy_core_compat"] = False
                result = module.update(source_root, target_root, **update_kwargs)
                if not isinstance(result, dict):
                    raise UpdateError(f"{import_path}.update() 未返回字典")
                status = str(result.get("status", ""))
                if status not in {"ok", "skipped", "partial", "failed"}:
                    raise UpdateError(f"{import_path}.update() 返回无效状态: {status!r}")
                result.setdefault("module", name)
                result.setdefault("details", [])
                result.setdefault("warnings", [])
            except Exception as exc:
                result = {
                    "module": name,
                    "status": "failed",
                    "details": [],
                    "warnings": [str(exc)],
                }
            results.append(result)
    finally:
        _clear_remote_update_package()
    return results


def _clear_remote_update_package() -> None:
    prefix = REMOTE_UPDATE_PACKAGE + "."
    for module_name in list(sys.modules):
        if module_name == REMOTE_UPDATE_PACKAGE or module_name.startswith(prefix):
            sys.modules.pop(module_name, None)


def _load_remote_update_package(source_root: Path) -> str:
    update_dir = source_root / "update"
    init_path = update_dir / "__init__.py"
    if not init_path.is_file():
        raise UpdateError(f"远程源码缺少更新模块入口: {init_path}")
    _clear_remote_update_package()
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(
        REMOTE_UPDATE_PACKAGE,
        init_path,
        submodule_search_locations=[str(update_dir)],
    )
    if spec is None or spec.loader is None:
        raise UpdateError(f"无法加载远程更新模块: {init_path}")
    package = importlib.util.module_from_spec(spec)
    sys.modules[REMOTE_UPDATE_PACKAGE] = package
    try:
        spec.loader.exec_module(package)
    except Exception as exc:
        _clear_remote_update_package()
        raise UpdateError(f"远程更新模块入口加载失败: {exc}") from exc
    return REMOTE_UPDATE_PACKAGE


def version_document_after_update(
    local_document: dict,
    remote_document: dict,
    requested_module: str,
) -> dict:
    """Build the manifest committed after a successful update."""
    if requested_module == "all":
        return copy.deepcopy(remote_document)
    local_components = local_document.get("components")
    remote_components = remote_document.get("components")
    if not isinstance(local_components, dict):
        raise UpdateError("本地 version.json 缺少 components")
    if not isinstance(remote_components, dict) or not isinstance(
        remote_components.get(requested_module), dict
    ):
        raise UpdateError(f"远程 version.json 缺少 components.{requested_module}")
    result = copy.deepcopy(local_document)
    result_components = result.setdefault("components", {})
    result_components[requested_module] = copy.deepcopy(remote_components[requested_module])
    return result


def finalize_version_document(
    local_document: dict,
    remote_document: dict,
    requested_module: str,
    *,
    dry_run: bool,
) -> None:
    final_document = version_document_after_update(
        local_document,
        remote_document,
        requested_module,
    )
    target = ROOT / "version.json"
    final_version = version_for_module(final_document, requested_module)
    if dry_run:
        print(f"[dry-run] 将在全部步骤成功后写入 {requested_module} 版本: {final_version}")
        return
    try:
        write_json_atomic(target, final_document)
    except Exception as exc:
        raise UpdateError(f"版本文件写入失败: {exc}") from exc
    print(green(f"版本状态已提交: {requested_module} {final_version}"))


def _print_backup_hint(backup_dir: Path | None) -> None:
    if backup_dir is not None:
        print(yellow(f"更新前备份保留在: {backup_dir}"), file=sys.stderr)


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
            print(f"  - {detail}")
        for warning in result.get("warnings", []):
            print(yellow(f"  ! {warning}"))


def print_version_report(local: dict, remote: dict, requested_module: str) -> None:
    names = ["all", *MODULES] if requested_module == "all" else [requested_module]
    for name in names:
        local_version = version_for_module(local, name)
        remote_version = version_for_module(remote, name)
        comparison = compare_versions(local_version, remote_version)
        label = "全部" if name == "all" else MODULES[name][0]
        state = "最新" if comparison == 0 else "可更新" if comparison < 0 else "本地较新"
        print(f"{label:<10} 本地 {local_version:<12} 远程 {remote_version:<12} {state}")


def _print_platform_warning() -> None:
    if platform.system().lower() != "windows":
        return
    if not command_exists("git"):
        print(yellow("[warning] Windows 上未检测到 git 命令"))
        print(yellow("  请安装 Git for Windows: https://git-scm.com/download/win"))
        raise SystemExit(1)
    print(yellow("[info] Windows 模式 - 确保 git 命令可从当前终端访问。"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按板块更新 kemo-agent（全平台）")
    parser.add_argument("--module", choices=["all", *MODULES], default="all", help="更新板块，默认 all")
    parser.add_argument("--check", action="store_true", help="仅检查本地和远程版本")
    parser.add_argument("--force", action="store_true", help="版本相同时强制重新安装")
    parser.add_argument("--yes", "-y", action="store_true", help="使用默认选项确认所有提示")
    parser.add_argument("--dry-run", action="store_true", help="仅展示计划的操作，不实际修改")
    parser.add_argument("--skip-web-build", action="store_true", help="跳过 Web 前端构建")
    parser.add_argument("--skip-deps", action="store_true", help="跳过 pip install -r requirements.txt")
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="Git 仓库 URL")
    parser.add_argument("--branch", default=DEFAULT_BRANCH, help="Git 分支")
    parser.add_argument("--remote-version-url", default="", help="远程 version.json URL 覆盖")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    backup_dir: Path | None = None
    try:
        _print_platform_warning()
        remote_url = args.remote_version_url or VERSION_URL_TEMPLATE.format(branch=args.branch)
        local_document, remote_document = load_version_documents(remote_url)
        validate_version_document(local_document, "本地")
        validate_version_document(remote_document, "远程")

        if args.check:
            print_version_report(local_document, remote_document, args.module)
            return 0

        print(green(f"正在检查 {args.module} 板块版本..."))
        local_version = version_for_module(local_document, args.module)
        remote_version = version_for_module(remote_document, args.module)
        if not should_update(
            local_version,
            remote_version,
            force=args.force,
            assume_yes=args.yes,
        ):
            print(yellow("更新已取消"))
            return 0

        require_commands(["git"])
        selected_modules = list(MODULES) if args.module == "all" else [args.module]
        with tempfile.TemporaryDirectory(prefix="kemo-agent-update-") as temporary:
            source = clone_latest(args.repo_url, args.branch, Path(temporary))
            backup_dir = make_backup(args.dry_run)
            results = run_modules(
                selected_modules,
                source,
                ROOT,
                dry_run=args.dry_run,
                assume_yes=args.yes,
            )
            print_module_summary(results)
            if any(result.get("status") == "failed" for result in results):
                raise UpdateError("一个或多个更新板块失败，请根据汇总信息处理")
            if any(result.get("status") == "partial" for result in results):
                raise UpdateError("一个或多个更新板块不完整，版本号保持不变")

            if "core" in selected_modules:
                migrate_user_skeletons(dry_run=args.dry_run)
                migrate_user_memories(dry_run=args.dry_run)

        if "web" in selected_modules and not args.skip_web_build:
            build_web_frontend(dry_run=args.dry_run)
        if "core" in selected_modules and not args.skip_deps:
            refresh_dependencies(dry_run=args.dry_run)

        finalize_version_document(
            local_document,
            remote_document,
            args.module,
            dry_run=args.dry_run,
        )

        print(green("update complete"))
        return 0
    except subprocess.CalledProcessError as exc:
        print(red(f"命令失败: {' '.join(exc.cmd)}"), file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except UpdateError as exc:
        print(red(str(exc)), file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except OSError as exc:
        print(red(f"文件系统操作失败: {exc}"), file=sys.stderr)
        _print_backup_hint(backup_dir)
        return 1
    except KeyboardInterrupt:
        print()
        print(yellow("更新已中断"))
        _print_backup_hint(backup_dir)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
