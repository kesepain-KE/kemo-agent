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
import errno
import os
import signal
import socket
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_PATH = ROOT / "tmp" / "restart.log"


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
    except OSError as exc:
        if isinstance(exc, ProcessLookupError) or exc.errno == errno.ESRCH:
            return
        if sys.platform == "win32" and getattr(exc, "winerror", None) == 87:
            return
        raise


def _force_terminate_process(pid: int) -> None:
    """Force termination after a graceful Unix shutdown timed out."""

    if sys.platform == "win32":
        _terminate_process(pid)
        return
    try:
        os.kill(pid, getattr(signal, "SIGKILL", 9))
    except ProcessLookupError:
        return


def _wait_for_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _port_is_free(port):
            return
        time.sleep(0.2)
    raise TimeoutError(f"等待端口 {port} 释放超时")


def _port_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_started_process(
    process: subprocess.Popen[bytes],
    port: int,
    timeout: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise RuntimeError(f"新实例启动失败，退出码 {return_code}")
        if _port_is_listening(port):
            return
        time.sleep(0.2)
    raise TimeoutError(f"等待新实例监听端口 {port} 超时")


def _write_log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _start_web(port: int) -> subprocess.Popen[bytes]:
    command = [sys.executable, str(ROOT / "start_web.py"), f"--port={port}"]
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log_handle = LOG_PATH.open("ab", buffering=0)
    kwargs: dict[str, object] = {
        "cwd": str(ROOT),
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(command, **kwargs)
    finally:
        log_handle.close()


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
        _write_log(
            f"收到重启请求 platform={sys.platform} parent_pid={args.parent_pid} port={args.port}"
        )
        _terminate_process(args.parent_pid)
        try:
            _wait_for_port(args.port, timeout=15.0)
        except TimeoutError:
            _write_log(f"旧端口 {args.port} 未及时释放，升级为强制终止")
            _force_terminate_process(args.parent_pid)
            _wait_for_port(args.port, timeout=10.0)
    else:
        _wait_for_port(args.port)
    process = _start_web(args.port)
    _wait_for_started_process(process, args.port)
    message = f"新实例已启动 pid={process.pid} port={args.port}"
    _write_log(message)
    print(f"[restart] {message}")
    return 0


def _entrypoint() -> int:
    try:
        return main()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        try:
            _write_log(
                f"重启失败：{type(exc).__name__}: {exc}\n{traceback.format_exc().rstrip()}"
            )
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(_entrypoint())
