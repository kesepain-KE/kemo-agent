"""Restart kemo-agent on a specific Web port.

The Web API launches this helper as a detached process and passes the PID of
the current server.  The helper lets the HTTP response finish, terminates the
old process, waits for the listening port to be released, and starts a new
``start_web.py`` instance on the same port.

Usage:
    python restart.py --port=1360 --parent-pid=1234
    python restart.py --port=1360  # only when the target port is already free
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _port_is_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _terminate_process(pid: int) -> None:
    if pid <= 1 or pid == os.getpid():
        raise ValueError("拒绝终止无效的父进程 PID")
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_process_exit(pid: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return
        time.sleep(0.1)
    raise TimeoutError(f"等待旧进程 {pid} 退出超时")


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_free(port):
            return
        time.sleep(0.2)
    raise TimeoutError(f"等待端口 {port} 释放超时")


def _start_web(port: int) -> subprocess.Popen[bytes]:
    command = [sys.executable, str(ROOT / "start_web.py"), f"--port={port}"]
    kwargs: dict[str, object] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restart kemo-agent")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, default=None)
    parser.add_argument("--response-delay", type=float, default=0.75)
    args = parser.parse_args(argv)
    if args.port < 1 or args.port > 65535:
        parser.error("--port 必须位于 1–65535")

    if args.parent_pid is not None:
        time.sleep(max(0.0, args.response_delay))
        _terminate_process(args.parent_pid)
        _wait_for_process_exit(args.parent_pid)

    _wait_for_port(args.port)
    process = _start_web(args.port)
    print(f"[restart] 新实例已启动 pid={process.pid} port={args.port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
