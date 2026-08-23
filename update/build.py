"""Dependency refresh and frontend build operations."""

from __future__ import annotations

import sys
from pathlib import Path

from ._utils import UpdateError, _resolve_npm_command, green, run, yellow
from .constants import ROOT


def build_web_frontend(*, root: Path = ROOT, dry_run: bool) -> None:
    web_dir = root / "web" / "frontend"
    package_json = web_dir / "package.json"
    if not package_json.is_file():
        raise UpdateError(f"未找到前端构建配置: {package_json}")
    install_command = "ci" if (web_dir / "package-lock.json").is_file() else "install"
    if dry_run:
        print(
            f"[dry-run] 将在 {web_dir} 执行 "
            f"npm {install_command} && npm run build"
        )
        return
    npm_command = _resolve_npm_command()
    if not npm_command:
        raise UpdateError(
            "未找到 npm，无法重新构建已更新的 Web 前端。"
            "请安装 Node.js，然后重新执行更新。"
        )
    print(green("正在构建 Web 前端..."))
    run([npm_command, install_command], cwd=web_dir)
    run([npm_command, "run", "build"], cwd=web_dir)
    dist_dir = web_dir / "dist"
    if not dist_dir.is_dir() or not any(dist_dir.iterdir()):
        raise UpdateError("Web 前端构建失败：web/frontend/dist 为空")
    print(green("Web 前端已构建"))


def refresh_dependencies(*, root: Path = ROOT, dry_run: bool) -> None:
    requirements = root / "requirements.txt"
    if not requirements.is_file():
        print(yellow("未找到 requirements.txt，跳过依赖刷新"))
        return
    if dry_run:
        print("[dry-run] 将执行 pip install -r requirements.txt")
        return
    print(green("正在刷新 Python 依赖..."))
    run([sys.executable, "-m", "pip", "install", "-r", str(requirements)])
    print(green("依赖已刷新"))
