"""系统命令执行工具 — 会话模式 + 命令链 + 跨平台。kemo-agent 原生插件。"""

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SESSION_ID_RE = re.compile(r"^[^\x00-\x1f\x7f]+$")
_SESSION_HISTORY_LIMIT = 2000
_SESSION_MAX_COUNT = 500
_SESSION_LOCK = threading.RLock()
_SESSION_CACHE: dict[str, dict[str, Any]] = {}


def _utc_timestamp() -> float:
    return time.time()


def _decode_output(data: bytes) -> str:
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _decide_encoding() -> str:
    if os.name == "nt":
        return "utf-8"
    return "utf-8"


# ── 会话管理 ──────────────────────────────────────────────────────

def _cleanup_expired_sessions() -> None:
    expired: list[str] = []
    for sid, session in _SESSION_CACHE.items():
        if not isinstance(session, dict):
            expired.append(sid)
            continue
        if session.get("schema_version") != 2:
            expired.append(sid)
            continue
        last = session.get("last_used", 0)
        if isinstance(last, (int, float)) and last > 0 and _utc_timestamp() - last > 86400:
            expired.append(sid)
    for sid in expired:
        _SESSION_CACHE.pop(sid, None)
    if len(_SESSION_CACHE) > _SESSION_MAX_COUNT:
        oldest = sorted(_SESSION_CACHE.items(),
                        key=lambda x: x[1].get("last_used", 0) if isinstance(x[1], dict) else 0)
        _SESSION_CACHE.clear()
        _SESSION_CACHE.update(dict(oldest[-(_SESSION_MAX_COUNT // 2):]))


def _get_session(session_id: str, root: Path) -> dict[str, Any]:
    with _SESSION_LOCK:
        _cleanup_expired_sessions()
        session = _SESSION_CACHE.get(session_id)
        if not isinstance(session, dict):
            session = {
                "cwd": str(root),
                "env": {},
                "history": [],
                "schema_version": 2,
                "created": _utc_timestamp(),
                "last_used": _utc_timestamp(),
            }
            _SESSION_CACHE[session_id] = session
        session["last_used"] = _utc_timestamp()
        return session


def _reset_session(session_id: str) -> None:
    with _SESSION_LOCK:
        _SESSION_CACHE.pop(session_id, None)


# ── 命令链解析 ────────────────────────────────────────────────────

def _parse_chain(command: str) -> list[list[str]]:
    """解析 &&、||、; 分隔的命令链。"""
    segments: list[list[str]] = [[]]
    current = ""
    in_double = False
    in_single = False
    i = 0
    while i < len(command):
        ch = command[i]
        if ch == "\\" and i + 1 < len(command):
            current += command[i + 1]
            i += 2
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current += ch
            i += 1
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            current += ch
            i += 1
            continue
        if not in_double and not in_single:
            if command[i:i+2] == "&&":
                segments[-1].append(current.strip())
                segments.append([])
                current = ""
                i += 2
                continue
            if command[i:i+2] == "||":
                segments[-1].append(current.strip())
                segments.append([])
                current = ""
                i += 2
                continue
            if ch == ";":
                segments[-1].append(current.strip())
                segments.append([])
                current = ""
                i += 1
                continue
        current += ch
        i += 1
    segments[-1].append(current.strip())
    return [s for s in segments if s and any(s)]


def _chain_operator(command: str) -> str:
    """检测链操作符类型。"""
    if "&&" in command:
        return "and"
    if "||" in command:
        return "or"
    if ";" in command:
        return "semi"
    return "none"


# ── 会话内建命令 ──────────────────────────────────────────────────

def _handle_builtin(cmd: str, session: dict[str, Any], working_dir: str | None,
                    env: dict[str, str] | None) -> tuple[bool, str | None]:
    """处理会话内建命令，返回 (是否已处理, 输出)。"""
    parts = cmd.strip().split(maxsplit=1)
    name = parts[0].lower()

    if name in ("cd", "chdir"):
        target = parts[1].strip().strip('"').strip("'") if len(parts) > 1 else str(Path.home())
        new_cwd = Path(target)
        if not new_cwd.is_absolute():
            cwd = Path(working_dir or session.get("cwd", str(Path.home())))
            new_cwd = (cwd / target).resolve()
        if not new_cwd.is_dir():
            return True, f"cd: 目录不存在: {new_cwd}"
        session["cwd"] = str(new_cwd)
        return True, str(new_cwd)

    if name == "pwd":
        return True, working_dir or session.get("cwd", str(Path.home()))

    if name in ("export", "set", "env"):
        if len(parts) == 1:
            env_items = [f"{k}={v}" for k, v in sorted(session.get("env", {}).items())]
            return True, "\n".join(env_items) if env_items else "(empty)"
        arg = parts[1].strip()
        if "=" in arg:
            kv = arg.split("=", 1)
            k, v = kv[0].strip(), kv[1].strip().strip('"').strip("'")
            if _ENV_KEY_RE.match(k):
                session.setdefault("env", {})[k] = v
                return True, f"{k}={v}"
            return True, f"export: 无效的变量名: {k}"
        return True, f"{arg}=?"

    if name == "unset":
        if len(parts) > 1:
            session.get("env", {}).pop(parts[1].strip(), None)
        return True, ""

    if name == "history":
        history = session.get("history", [])
        if not history:
            return True, "(empty)"
        return True, "\n".join(f"[{i+1}] {h}" for i, h in enumerate(history[-50:]))

    return False, None


# ── 执行 ──────────────────────────────────────────────────────────

def _run_one(command: str, *, working_dir: str, env_extra: dict[str, str] | None,
             timeout: int, encoding: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    if env_extra:
        for k, v in env_extra.items():
            if _ENV_KEY_RE.match(k):
                env[k] = v

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=working_dir,
            env=env,
            timeout=timeout or None,
            capture_output=True,
        )
        stdout = _decode_output(proc.stdout).strip()
        stderr = _decode_output(proc.stderr).strip()
        exit_code = proc.returncode

        if stdout and stderr:
            output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
        elif stdout:
            output = stdout
        elif stderr:
            output = f"STDERR:\n{stderr}"
        else:
            output = "(无输出)"

        if exit_code != 0:
            output += f"\n(exit={exit_code})"

        return {"ok": exit_code == 0, "output": output, "exit_code": exit_code}

    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"命令超时 ({timeout}s)", "exit_code": -1}
    except Exception as exc:
        return {"ok": False, "output": f"命令执行失败: {exc}", "exit_code": -1}


def run(
    command: str,
    working_dir: str = "",
    timeout: int = 0,
    stdin: str = "",
    env: dict[str, str] | None = None,
    session_id: str = "",
    reset_session: bool = False,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    root = Path(context.get("root", str(Path(__file__).resolve().parent.parent.parent)))
    timeout = timeout or 120
    encoding = _decide_encoding()

    if reset_session and session_id:
        _reset_session(session_id)

    session = _get_session(session_id, root) if session_id else None
    effective_cwd = working_dir or (session["cwd"] if session else str(root))
    effective_env = dict(session["env"]) if session else {}
    if env:
        for k, v in env.items():
            if _ENV_KEY_RE.match(k):
                effective_env[k] = v
                if session:
                    session["env"][k] = v

    # 先检查是否为会话内建命令
    if session:
        handled, output = _handle_builtin(command, session, effective_cwd, effective_env)
        if handled:
            session["history"].append(command)
            if len(session["history"]) > _SESSION_HISTORY_LIMIT:
                session["history"] = session["history"][-_SESSION_HISTORY_LIMIT:]
            return {"ok": True, "output": output or "", "session_id": session_id, "cwd": effective_cwd}

    # 命令链
    op = _chain_operator(command)
    if op != "none":
        segments = _parse_chain(command)
        all_outputs: list[str] = []
        for seg_idx, cmd_parts in enumerate(segments):
            cmd = " ".join(cmd_parts)
            result = _run_one(cmd, working_dir=effective_cwd, env_extra=effective_env,
                              timeout=timeout, encoding=encoding)
            all_outputs.append(f"[{seg_idx}] {result['output']}")
            if op == "and" and not result["ok"]:
                break
            if op == "or" and result["ok"]:
                break
            # 更新 cwd（会话模式下）
            if session:
                effective_cwd = session.get("cwd", effective_cwd)
        if session:
            session["history"].append(command)
        return {"ok": True, "output": "\n".join(all_outputs), "chain": op, "session_id": session_id,
                "cwd": effective_cwd}

    # 普通单命令
    result = _run_one(command, working_dir=effective_cwd, env_extra=effective_env,
                      timeout=timeout, encoding=encoding)
    if session:
        session["history"].append(command)
        if len(session["history"]) > _SESSION_HISTORY_LIMIT:
            session["history"] = session["history"][-_SESSION_HISTORY_LIMIT:]

    return {**result, "session_id": session_id, "cwd": effective_cwd}
