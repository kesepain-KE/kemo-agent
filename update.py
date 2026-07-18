#!/usr/bin/env python3
"""kemo-agent 全平台更新脚本

比对本地 version.json 与 GitHub main 分支上的 version.json，
将最新源码克隆到临时目录，备份当前应用，同步框架文件，保留用户数据，
随后构建 web 前端并刷新依赖。

全平台通用（Linux / macOS / Windows），不依赖 rsync。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterable


APP_NAME = "kemo-agent"
DEFAULT_REPO_URL = "https://github.com/kesepain-KE/kemo-agent.git"
DEFAULT_BRANCH = "main"
VERSION_URL_TEMPLATE = (
    f"https://raw.githubusercontent.com/kesepain-KE/kemo-agent/{{branch}}/version.json"
)
BACKUP_KEEP = 2

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web" / "frontend"


# ═══════════════════════════════════════════════════════════════════
# 排除规则
# ═══════════════════════════════════════════════════════════════════

# 同步时排除（不覆盖）
MAIN_EXCLUDES = [
    ".git/",
    ".venv/",
    "venv/",
    # 用户数据 —— 绝对保护
    "users/",
    "skills/",
    # 共享用户层 —— 不覆盖
    "shared_knowledge/",
    "shared_expand/",
    "shared_skills/",
    # 构建产物
    "web/node_modules/",
    "web/dist/",
    "__pycache__/",
    "tmp/",
    ".backups/",
    # 运行时配置 —— 用户专属
    ".env",
    ".session_secret",
    # 交互式处理（走单独逻辑）
    "config/",
    "global_knowledge/",
]

BACKUP_EXCLUDES = [
    ".git/", ".venv/", "venv/", ".backups/",
    "users/", "skills/",
    "web/node_modules/", "web/dist/",
    "tmp/", "__pycache__/",
]


# ═══════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════


class UpdateError(RuntimeError):
    pass


def green(text: str) -> str:
    return f"\033[0;32m{text}\033[0m"


def yellow(text: str) -> str:
    return f"\033[1;33m{text}\033[0m"


def red(text: str) -> str:
    return f"\033[0;31m{text}\033[0m"


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    dry_run: bool = False,
    capture: bool = False,
) -> subprocess.CompletedProcess:
    print("+ " + " ".join(cmd))
    if dry_run and not capture:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    kwargs: dict = {"text": True}
    if capture:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    return subprocess.run(cmd, cwd=str(cwd or ROOT), check=True, **kwargs)


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def require_commands(names: Iterable[str]) -> None:
    missing = [name for name in names if not command_exists(name)]
    if missing:
        raise UpdateError(f"缺少必需命令: {', '.join(missing)}")


def read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise UpdateError(f"{path} 不是 JSON 对象")
    return data


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kemo-agent-updater"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise UpdateError(f"远程 JSON 不是对象: {url}")
    return data


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value).strip()
    if not re.fullmatch(r"\d+(?:\.\d+)*", text):
        raise UpdateError(f"无效版本号: {value!r}")
    return tuple(int(part) for part in text.split("."))


def compare_versions(left: str, right: str) -> int:
    a = parse_version(left)
    b = parse_version(right)
    n = max(len(a), len(b))
    a = a + (0,) * (n - len(a))
    b = b + (0,) * (n - len(b))
    if a < b:
        return -1
    if a > b:
        return 1
    return 0


def ask_yes_no(prompt: str, *, default: bool, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not is_interactive():
        return default
    suffix = " [Y/n] " if default else " [y/N] "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def ask_choice(
    prompt: str,
    choices: dict[str, str],
    *,
    default: str,
    assume_yes: bool,
) -> str:
    if assume_yes or not is_interactive():
        return default
    print(prompt)
    for key, label in choices.items():
        mark = " (默认)" if key == default else ""
        print(f"  {key}) {label}{mark}")
    while True:
        answer = input("> ").strip().lower()
        if not answer:
            return default
        if answer in choices:
            return answer
        print("请选择: " + ", ".join(choices))


def _resolve_npm_command() -> str | None:
    resolved = shutil.which("npm")
    if not resolved:
        return None
    if os.name == "nt" and not resolved.lower().endswith(".exe"):
        cmd_path = resolved + ".cmd"
        if os.path.isfile(cmd_path):
            return cmd_path
    return resolved


def tree_digest(path: Path) -> str:
    if not path.exists():
        return ""
    if path.is_file():
        h = hashlib.sha256()
        h.update(path.read_bytes())
        return h.hexdigest()
    h = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        rel = item.relative_to(path).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(item.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def paths_differ(left: Path, right: Path) -> bool:
    return tree_digest(left) != tree_digest(right)


# ═══════════════════════════════════════════════════════════════════
# 纯 Python 目录同步
# ═══════════════════════════════════════════════════════════════════


def _parse_excludes(excludes: Iterable[str]) -> tuple[set[str], set[str]]:
    dir_patterns: set[str] = set()
    file_patterns: set[str] = set()
    for pattern in excludes:
        p = pattern.replace("\\", "/")
        if p.endswith("/"):
            dir_patterns.add(p.rstrip("/"))
        else:
            file_patterns.add(p)
    return dir_patterns, file_patterns


def _is_excluded(rel: str, dir_patterns: set[str], file_patterns: set[str]) -> bool:
    rel = rel.replace("\\", "/")
    if rel in file_patterns:
        return True
    parts = rel.split("/")
    for pattern in dir_patterns:
        if "/" in pattern:
            if rel == pattern or rel.startswith(pattern + "/"):
                return True
        elif pattern in parts:
            return True
    return False


def _walk_sync(
    source: Path,
    target: Path,
    *,
    dir_patterns: set[str],
    file_patterns: set[str],
    dry_run: bool,
) -> None:
    for root, dirs, files in os.walk(str(source)):
        root_path = Path(root)
        rel = root_path.relative_to(source).as_posix()
        if rel == ".":
            rel = ""
        if rel and _is_excluded(rel, dir_patterns, file_patterns):
            dirs.clear()
            continue
        target_dir = target / rel if rel else target
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            rel_file = f"{rel}/{f}" if rel else f
            if _is_excluded(rel_file, dir_patterns, file_patterns):
                continue
            src_file = root_path / f
            dst_file = target_dir / f
            if dry_run:
                print(f"[dry-run]  复制  {_short(src_file)}")
            else:
                shutil.copy2(src_file, dst_file, follow_symlinks=False)


def _walk_delete(
    source: Path,
    target: Path,
    *,
    dir_patterns: set[str],
    file_patterns: set[str],
    dry_run: bool,
) -> None:
    for root, dirs, files in os.walk(str(target), topdown=False):
        root_path = Path(root)
        rel = root_path.relative_to(target).as_posix()
        if rel == ".":
            rel = ""
        if rel and _is_excluded(rel, dir_patterns, file_patterns):
            continue
        for f in files:
            rel_file = f"{rel}/{f}" if rel else f
            if _is_excluded(rel_file, dir_patterns, file_patterns):
                continue
            src_file = source / rel_file
            if not src_file.exists():
                if dry_run:
                    print(f"[dry-run]  删除  {_short(root_path / f)}")
                else:
                    (root_path / f).unlink()
        if rel and not _is_excluded(rel, dir_patterns, file_patterns):
            src_dir = source / rel
            if not src_dir.exists():
                try:
                    if dry_run:
                        print(f"[dry-run]  删除目录  {_short(root_path)}")
                    else:
                        root_path.rmdir()
                except OSError:
                    pass


def _short(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def sync_directory(
    source: Path,
    target: Path,
    *,
    delete: bool = False,
    excludes: Iterable[str] = (),
    dry_run: bool = False,
) -> None:
    dir_patterns, file_patterns = _parse_excludes(excludes)
    if not source.is_dir():
        raise UpdateError(f"源目录不存在: {source}")
    action = "同步 (含删除)" if delete else "同步"
    print(f"  {action}  {_short(source)} -> {_short(target)}")
    target.mkdir(parents=True, exist_ok=True)
    _walk_sync(source, target, dir_patterns=dir_patterns, file_patterns=file_patterns, dry_run=dry_run)
    if delete:
        _walk_delete(source, target, dir_patterns=dir_patterns, file_patterns=file_patterns, dry_run=dry_run)


# ═══════════════════════════════════════════════════════════════════
# 核心流程
# ═══════════════════════════════════════════════════════════════════


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
        [p for p in backups_root.iterdir() if p.is_dir() and p.name.startswith("update-")],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[BACKUP_KEEP:]:
        shutil.rmtree(old, ignore_errors=True)
        print(yellow(f"已移除旧备份: {old}"))


def clone_latest(repo_url: str, branch: str, work_dir: Path) -> Path:
    target = work_dir / "source"
    run(["git", "clone", "--depth", "1", "--branch", branch, repo_url, str(target)])
    return target


def sync_main_source(source: Path, *, dry_run: bool) -> None:
    print(green("正在同步框架文件..."))
    sync_directory(source, ROOT, delete=True, excludes=MAIN_EXCLUDES, dry_run=dry_run)


def handle_config(source: Path, *, assume_yes: bool, dry_run: bool) -> None:
    new_config = source / "config"
    local_config = ROOT / "config"
    if not new_config.exists():
        return
    if not local_config.exists():
        print(green("正在安装 config/ 目录"))
        sync_directory(new_config, local_config, delete=True, dry_run=dry_run)
        return
    if not paths_differ(new_config, local_config):
        print("config/ 无变化")
        return
    choice = ask_choice(
        "config/ 与最新版本不同，请选择:",
        {"o": "用最新版本覆盖 config/", "k": "保留本地 config/"},
        default="o",
        assume_yes=assume_yes,
    )
    if choice == "o":
        sync_directory(new_config, local_config, delete=True, dry_run=dry_run)
        print(green("config/ 已更新"))
    else:
        print(yellow("跳过 config/"))


def handle_global_knowledge(source: Path, *, assume_yes: bool, dry_run: bool) -> None:
    new_gk = source / "global_knowledge"
    local_gk = ROOT / "global_knowledge"
    if not new_gk.exists():
        return
    choice = ask_choice(
        "global_knowledge/ 的更新方式:",
        {"1": "合并最新文件，保留本地额外文件", "2": "跳过", "3": "完全替换"},
        default="1",
        assume_yes=assume_yes,
    )
    if choice == "1":
        local_gk.mkdir(parents=True, exist_ok=True)
        sync_directory(new_gk, local_gk, dry_run=dry_run)
        print(green("global_knowledge/ 已合并"))
    elif choice == "2":
        print(yellow("跳过 global_knowledge/"))
    else:
        if not dry_run and local_gk.exists():
            shutil.rmtree(local_gk)
        local_gk.mkdir(parents=True, exist_ok=True)
        sync_directory(new_gk, local_gk, dry_run=dry_run)
        print(green("global_knowledge/ 已替换"))


def migrate_user_skeletons(*, dry_run: bool) -> None:
    users_dir = ROOT / "users"
    if not users_dir.is_dir():
        return
    candidates = [
        path for path in sorted(users_dir.iterdir())
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
    npm_cmd = _resolve_npm_command()
    dist_dir = WEB_DIR / "dist"
    if not npm_cmd:
        if dist_dir.is_dir() and any(dist_dir.iterdir()):
            print(yellow("未找到 npm，但 web/dist 已存在，跳过前端构建"))
            return
        raise UpdateError(
            "未找到 npm 且 web/dist 不存在。"
            "请安装 Node.js（https://nodejs.org/），"
            "然后手动执行: cd web/frontend && npm install && npm run build"
        )
    print(green("正在构建 Web 前端..."))
    run([npm_cmd, "install"], cwd=WEB_DIR)
    run([npm_cmd, "run", "build"], cwd=WEB_DIR)
    if not dist_dir.is_dir() or not any(dist_dir.iterdir()):
        raise UpdateError("Web 前端构建失败：web/frontend/dist 为空")
    print(green("Web 前端已构建"))


def refresh_dependencies(*, dry_run: bool) -> None:
    requirements = ROOT / "requirements.txt"
    if not requirements.is_file():
        print(yellow("未找到 requirements.txt，跳过依赖刷新"))
        return
    if dry_run:
        print(f"[dry-run] 将执行 pip install -r requirements.txt")
        return
    print(green("正在刷新 Python 依赖..."))
    run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])
    print(green("依赖已刷新"))


# ═══════════════════════════════════════════════════════════════════
# 版本比对与入口
# ═══════════════════════════════════════════════════════════════════


def load_versions(remote_url: str) -> tuple[str, str]:
    local_path = ROOT / "version.json"
    if not local_path.is_file():
        raise UpdateError(f"未找到本地版本文件: {local_path}")
    local = read_json(local_path)
    remote = fetch_json(remote_url)
    local_version = str(local.get("version", "")).strip()
    remote_version = str(remote.get("version", "")).strip()
    if not local_version or not remote_version:
        raise UpdateError("本地和远程 version.json 都必须包含 version 字段")
    parse_version(local_version)
    parse_version(remote_version)
    return local_version, remote_version


def should_update(
    local_version: str,
    remote_version: str,
    *,
    force: bool,
    assume_yes: bool,
) -> bool:
    cmp_result = compare_versions(local_version, remote_version)
    print(f"本地版本  : {local_version}")
    print(f"远程版本  : {remote_version}")
    if cmp_result == 0:
        if force:
            print(yellow("版本相同，强制重新安装 main 分支。"))
            return True
        return ask_yes_no("版本相同，仍要从 main 重新安装？", default=False, assume_yes=assume_yes)
    if cmp_result < 0:
        print(green(f"发现更新: {local_version} -> {remote_version}"))
        return ask_yes_no("是否继续更新？", default=True, assume_yes=assume_yes)
    print(yellow(f"本地版本更新于远程: {local_version} > {remote_version}"))
    print(yellow("这可能导致降级。"))
    return ask_yes_no("是否继续？", default=False, assume_yes=assume_yes)


def _print_platform_warning() -> None:
    system = platform.system().lower()
    if system == "windows":
        if not command_exists("git"):
            print(yellow("⚠ Windows 上未检测到 git 命令"))
            print(yellow("  请安装 Git for Windows: https://git-scm.com/download/win"))
            raise SystemExit(1)
        print(yellow("ℹ Windows 模式 — 确保 git 命令可从当前终端访问。"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="更新 kemo-agent（全平台）")
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
    args = parse_args(argv or sys.argv[1:])

    try:
        _print_platform_warning()

        if args.check:
            remote_url = args.remote_version_url or VERSION_URL_TEMPLATE.format(branch=args.branch)
            local_version, remote_version = load_versions(remote_url)
            cmp_result = compare_versions(local_version, remote_version)
            if cmp_result == 0:
                print(green(f"已是最新: {local_version}"))
            elif cmp_result < 0:
                print(yellow(f"发现更新: {local_version} -> {remote_version}"))
            else:
                print(yellow(f"本地版本更新于远程: {local_version} > {remote_version}"))
            return 0

        print(green("正在检查版本..."))
        remote_url = args.remote_version_url or VERSION_URL_TEMPLATE.format(branch=args.branch)
        local_version, remote_version = load_versions(remote_url)

        if not should_update(local_version, remote_version, force=args.force, assume_yes=args.yes):
            print(yellow("更新已取消"))
            return 0

        if not args.dry_run:
            require_commands(["git"])

        with tempfile.TemporaryDirectory(prefix="kemo-agent-update-") as tmp:
            source = clone_latest(args.repo_url, args.branch, Path(tmp))
            make_backup(args.dry_run)
            sync_main_source(source, dry_run=args.dry_run)
            handle_config(source, assume_yes=args.yes, dry_run=args.dry_run)
            handle_global_knowledge(source, assume_yes=args.yes, dry_run=args.dry_run)
            migrate_user_skeletons(dry_run=args.dry_run)
            migrate_user_memories(dry_run=args.dry_run)

        if not args.skip_web_build:
            build_web_frontend(dry_run=args.dry_run)
        if not args.skip_deps:
            refresh_dependencies(dry_run=args.dry_run)

        print(green("update complete"))
        return 0

    except subprocess.CalledProcessError as exc:
        print(red(f"命令失败: {' '.join(exc.cmd)}"), file=sys.stderr)
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1
    except UpdateError as exc:
        print(red(str(exc)), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        print(yellow("更新已中断"))
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
