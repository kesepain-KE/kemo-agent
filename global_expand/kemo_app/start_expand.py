"""kemo app 桥接服务 — 守护进程管理（start/stop/status/restart）。

常驻机制：本模块被框架按需调用（120s 超时内），通过 subprocess.Popen 拉起
detached 的 server.py 常驻子进程后立即返回；服务生命周期由本文件管理，
进程崩溃后由下一次 start/status 调用检测并自动拉起。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SERVER_PATH = os.path.join(BASE_DIR, "daemon.py")
PID_PATH = os.path.join(BASE_DIR, "_server.pid")
LOG_PATH = os.path.join(BASE_DIR, "logs", "server.log")


def _load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"host": "127.0.0.1", "port": 8742, "token_sha256": ""}


def _port_open(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def _read_pid():
    try:
        with open(PID_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def _pid_alive(pid) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def status() -> dict:
    cfg = _load_config()
    pid = _read_pid()
    running = _pid_alive(pid)
    if not running:
        running = _port_open(int(cfg.get("port", 8742)))
    return {
        "ok": True,
        "running": running,
        "pid": pid if running else None,
        "port": int(cfg.get("port", 8742)),
        "host": cfg.get("host", "127.0.0.1"),
        "log": LOG_PATH,
    }


def start() -> dict:
    st = status()
    if st["running"]:
        return {"ok": True, "message": "already running", **st}
    cfg = _load_config()
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    flags = 0
    for name in ("CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS", "CREATE_NO_WINDOW"):
        flags |= getattr(subprocess, name, 0)
    with open(LOG_PATH, "ab") as lf:
        proc = subprocess.Popen(
            [sys.executable, SERVER_PATH],
            cwd=BASE_DIR,
            stdout=lf,
            stderr=lf,
            creationflags=flags,
            close_fds=True,
        )
    with open(PID_PATH, "w") as f:
        f.write(str(proc.pid))
    # 等待端口就绪（最多 6 秒）
    ready = False
    for _ in range(20):
        if _port_open(int(cfg.get("port", 8742))):
            ready = True
            break
        time.sleep(0.3)
    return {
        "ok": True,
        "message": "started" if ready else "started but port not ready (check log)",
        "pid": proc.pid,
        "port": int(cfg.get("port", 8742)),
        "log": LOG_PATH,
    }


def stop() -> dict:
    st = status()
    pid = st.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        time.sleep(0.6)
    if os.path.exists(PID_PATH):
        try:
            os.remove(PID_PATH)
        except OSError:
            pass
    after = status()
    return {"ok": True, "message": "stopped" if not after["running"] else "still running", **after}


def restart() -> dict:
    stop()
    time.sleep(0.5)
    return start()


_COMMANDS = {"start": start, "stop": stop, "status": status, "restart": restart}


def execute(command: str, params: dict | None = None) -> dict:
    """框架操作层入口：execute(command, params)。"""
    cmd = str(command or "").strip().casefold()
    handler = _COMMANDS.get(cmd)
    if not handler:
        return {"ok": False, "error": f"unknown command: {command}"}
    try:
        return handler()
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    result = execute(cmd)
    print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    raise SystemExit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
