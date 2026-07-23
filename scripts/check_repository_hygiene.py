#!/usr/bin/env python3
"""Reject runtime data and credential files that must never be tracked."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath


ALLOWED_RUNTIME_FILES = {
    "message/out/.gitkeep",
    "tmp/.gitignore",
    "tmp/.gitkeep",
    "users/.gitkeep",
}

BLOCKED_PREFIXES = {
    ".backups/": "更新备份",
    ".playwright-cli/": "浏览器测试运行数据",
    ".pytest_cache/": "Python 测试缓存",
    ".ruff_cache/": "Python 检查缓存",
    "cron/task_cron_system/log/": "Cron 运行日志",
    "开发临时目录/": "本地开发工作区",
}

PRIVATE_KEY_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
    )
    return [
        item.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    ]


def blocked_reason(path: str) -> str | None:
    normalized = path.casefold()
    name = PurePosixPath(path).name.casefold()

    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return "环境变量凭据文件"
    if normalized == "config/message_config.json":
        return "外部消息身份映射"
    if PurePosixPath(path).suffix.casefold() in PRIVATE_KEY_SUFFIXES:
        return "私钥或证书容器"

    for prefix, reason in BLOCKED_PREFIXES.items():
        if path.startswith(prefix):
            return reason

    if path.startswith("users/") and not (
        path == "users/.gitkeep" or path.startswith("users/_template/")
    ):
        return "用户运行数据"
    if path.startswith("message/out/") and path not in ALLOWED_RUNTIME_FILES:
        return "外部消息运行数据"
    if path.startswith("tmp/") and path not in ALLOWED_RUNTIME_FILES:
        return "临时运行数据"
    return None


def main() -> int:
    blocked = [
        (path, reason)
        for path in tracked_files()
        if (reason := blocked_reason(path)) is not None
    ]
    if blocked:
        print("仓库卫生检查失败：以下本地或敏感文件已被 Git 跟踪：", file=sys.stderr)
        for path, reason in blocked:
            print(f"- {path}（{reason}）", file=sys.stderr)
        return 1

    print("仓库卫生检查通过：未跟踪环境凭据、用户数据或运行日志。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
