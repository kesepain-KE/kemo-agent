"""Read-only live-writer checks for the source updater (never stop processes)."""
from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

from ._utils import UpdateError


def _within(value: str, root: Path) -> bool:
    try:
        return Path(value).resolve().is_relative_to(root)
    except (OSError, ValueError):
        return False


def _find_database_writer(processes: list[tuple[int, float]], root: Path) -> int | None:
    """Contain native handle-enumeration faults and refuse unverifiable results."""
    if not processes:
        return None
    try:
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("runtime_file_probe.py"))],
            input=json.dumps({"root": str(root), "processes": processes}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=15,
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
        if completed.returncode != 0:
            raise ValueError("process inspection failed")
        receipt = json.loads(completed.stdout)
        writer = receipt["writer_pid"]
        if writer is not None and (
            type(writer) is not int or writer not in {pid for pid, _ in processes}
        ):
            raise ValueError("invalid process identity")
        return writer
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        raise UpdateError(
            "无法核实其他进程是否持有安装目录数据库（检查进程退出、超时或回执无效）；"
            "请先停止服务，并检查进程查询权限后重试。"
        ) from exc


def require_stopped_runtime(*, root: Path, dry_run: bool = False) -> None:
    """Refuse writes when a live interpreter belongs to this installation.

    Inspect live OS processes rather than trusting PID/status files. A Python
    process rooted in the installation may host Web, cron or message routing
    even when launched via ``-c`` or an operator's custom script, so treat it
    conservatively as a possible writer. This is a preflight/recheck, not a
    service-start exclusion lease: operators must keep services stopped until
    the update finishes.
    """

    if dry_run:
        return
    try:
        import psutil
    except ImportError as exc:
        raise UpdateError(
            "无法检查活跃写进程（缺少既有依赖 psutil）；请先停止服务，"
            "安装 requirements.txt 中的依赖后重试。"
        ) from exc

    root = root.resolve()
    candidates: list[tuple[int, float]] = []
    try:
        for process in psutil.process_iter(["pid", "name"]):
            if process.pid == os.getpid():
                continue
            name = str(process.info.get("name") or "").casefold()
            if not name.startswith(("python", "pypy", "uvicorn", "gunicorn", "kemo", "node")):
                continue
            try:
                directory = process.cwd()
                command = process.cmdline()
                scoped = bool(directory) and _within(directory, root)
                scoped = scoped or any(
                    Path(argument).is_absolute() and _within(argument, root)
                    for argument in command[1:]
                )
                if scoped and process.is_running():
                    raise UpdateError(
                        f"检测到活跃写进程或可能承载 Web/cron/消息路由的进程 PID={process.pid}；"
                        "请先停止服务（包括独立 worker/CLI），确认退出后重试更新。"
                    )
                if not scoped:
                    candidates.append((process.pid, process.create_time()))
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError, ValueError) as exc:
                raise UpdateError(
                    f"无法核实可能的写进程 PID={process.pid} 是否已停止；"
                    "请先停止服务，并以有进程查询权限的同一账号重试。"
                ) from exc
    except psutil.Error as exc:
        raise UpdateError("无法完成活跃写进程检查；请先停止服务并检查进程查询权限后重试。") from exc
    writer = _find_database_writer(candidates, root)
    if writer is not None:
        raise UpdateError(
            f"检测到持有安装目录数据库的活跃进程 PID={writer}；请先停止服务，确认退出后重试更新。"
        )
