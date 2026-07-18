"""无命令黑名单的本地命令执行工具。"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SESSION_HISTORY_LIMIT = 2000
_SESSION_MAX_COUNT = 500
_SESSION_TTL_SECONDS = 86400
_OUTPUT_MAX_CHARS = 100_000
_SESSION_LOCK = threading.RLock()
_SESSION_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def _decode_output(data: bytes) -> str:
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(value: str) -> tuple[str, bool]:
    if len(value) <= _OUTPUT_MAX_CHARS:
        return value, False
    return value[:_OUTPUT_MAX_CHARS] + "\n…(输出已截断)", True


def _session_key(context: dict[str, Any], root: Path, session_id: str) -> tuple[str, str, str, str]:
    return (
        str(root).casefold(),
        str(context.get("user") or ""),
        str(context.get("source") or ""),
        session_id,
    )


def _cleanup_expired_sessions() -> None:
    deadline = _now() - _SESSION_TTL_SECONDS
    expired = [
        key for key, session in _SESSION_CACHE.items()
        if not isinstance(session, dict) or float(session.get("last_used", 0)) < deadline
    ]
    for key in expired:
        _SESSION_CACHE.pop(key, None)
    if len(_SESSION_CACHE) > _SESSION_MAX_COUNT:
        oldest = sorted(_SESSION_CACHE, key=lambda key: float(_SESSION_CACHE[key].get("last_used", 0)))
        for key in oldest[: len(_SESSION_CACHE) - (_SESSION_MAX_COUNT // 2)]:
            _SESSION_CACHE.pop(key, None)


def _get_session(key: tuple[str, str, str, str], root: Path) -> dict[str, Any]:
    with _SESSION_LOCK:
        _cleanup_expired_sessions()
        session = _SESSION_CACHE.get(key)
        if session is None:
            session = {
                "cwd": str(root),
                "env": {},
                "history": [],
                "last_used": _now(),
                "lock": threading.RLock(),
            }
            _SESSION_CACHE[key] = session
        session["last_used"] = _now()
        return session


def _reset_session(key: tuple[str, str, str, str]) -> None:
    with _SESSION_LOCK:
        _SESSION_CACHE.pop(key, None)


def _split_chain(command: str) -> tuple[list[str], list[str]]:
    """按未被引号包裹的 &&、||、; 拆分，并保留操作符。"""
    commands: list[str] = []
    operators: list[str] = []
    current: list[str] = []
    single = False
    double = False
    index = 0
    while index < len(command):
        char = command[index]
        if char == "\\" and index + 1 < len(command):
            current.extend((char, command[index + 1]))
            index += 2
            continue
        if char == "'" and not double:
            single = not single
            current.append(char)
            index += 1
            continue
        if char == '"' and not single:
            double = not double
            current.append(char)
            index += 1
            continue
        operator = ""
        if not single and not double:
            if command[index:index + 2] in {"&&", "||"}:
                operator = command[index:index + 2]
            elif char == ";":
                operator = ";"
        if operator:
            segment = "".join(current).strip()
            if not segment:
                raise ValueError("命令链包含空命令")
            commands.append(segment)
            operators.append(operator)
            current.clear()
            index += len(operator)
            continue
        current.append(char)
        index += 1
    if single or double:
        raise ValueError("命令包含未闭合的引号")
    segment = "".join(current).strip()
    if not segment:
        raise ValueError("command 不能为空或不能以链操作符结尾")
    commands.append(segment)
    return commands, operators


def _resolve_cwd(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser() if value else root
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {candidate}")
    return candidate


def _builtin(command: str, session: dict[str, Any], cwd: Path) -> dict[str, Any] | None:
    parts = command.strip().split(maxsplit=1)
    name = parts[0].casefold()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if name in {"cd", "chdir"}:
        target_text = argument.strip('"\'') if argument else str(Path.home())
        target = Path(target_text).expanduser()
        if not target.is_absolute():
            target = cwd / target
        target = target.resolve()
        if not target.is_dir():
            return {"ok": False, "output": f"cd: 目录不存在: {target}", "exit_code": 1}
        session["cwd"] = str(target)
        return {"ok": True, "output": str(target), "exit_code": 0, "cwd": str(target)}

    if name == "pwd":
        return {"ok": True, "output": str(cwd), "exit_code": 0}

    if name in {"export", "set", "env"}:
        if not argument:
            values = session.get("env", {})
            output = "\n".join(f"{key}={values[key]}" for key in sorted(values)) or "(empty)"
            return {"ok": True, "output": output, "exit_code": 0}
        if "=" not in argument:
            return {"ok": False, "output": f"{name}: 需要 KEY=VALUE", "exit_code": 1}
        key, value = argument.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            return {"ok": False, "output": f"{name}: 无效变量名: {key}", "exit_code": 1}
        value = value.strip().strip('"\'')
        session.setdefault("env", {})[key] = value
        return {"ok": True, "output": f"{key}={value}", "exit_code": 0}

    if name == "unset":
        if not argument or not _ENV_KEY_RE.fullmatch(argument):
            return {"ok": False, "output": "unset: 需要有效变量名", "exit_code": 1}
        session.setdefault("env", {}).pop(argument, None)
        return {"ok": True, "output": "", "exit_code": 0}

    if name == "history":
        history = session.get("history", [])
        output = "\n".join(f"[{index}] {item}" for index, item in enumerate(history[-50:], 1))
        return {"ok": True, "output": output or "(empty)", "exit_code": 0}

    return None


def _run_process(
    command: str,
    *,
    cwd: Path,
    env_extra: dict[str, str],
    stdin: str,
    timeout: float,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment.update(env_extra)
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            env=environment,
            input=stdin.encode("utf-8") if stdin else None,
            timeout=timeout,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout or b"")
        stderr = _decode_output(exc.stderr or b"")
        output, truncated = _truncate("\n".join(value for value in (stdout, stderr) if value).strip())
        return {
            "ok": False,
            "output": output or f"命令超时 ({timeout:g}s)",
            "exit_code": -1,
            "timed_out": True,
            "truncated": truncated,
        }
    stdout = _decode_output(completed.stdout).strip()
    stderr = _decode_output(completed.stderr).strip()
    if stdout and stderr:
        output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    elif stderr:
        output = f"STDERR:\n{stderr}"
    else:
        output = stdout or "(无输出)"
    output, truncated = _truncate(output)
    return {
        "ok": completed.returncode == 0,
        "output": output,
        "exit_code": completed.returncode,
        "timed_out": False,
        "truncated": truncated,
    }


def _execute(
    command: str,
    *,
    cwd: Path,
    environment: dict[str, str],
    stdin: str,
    timeout: float,
    session: dict[str, Any] | None,
) -> dict[str, Any]:
    commands, operators = _split_chain(command)
    deadline = time.monotonic() + timeout
    results: list[dict[str, Any]] = []
    current_cwd = cwd
    stdin_used = False
    last: dict[str, Any] | None = None

    for index, segment in enumerate(commands):
        if index:
            operator = operators[index - 1]
            if (operator == "&&" and last is not None and not last["ok"]) or (
                operator == "||" and last is not None and last["ok"]
            ):
                results.append({"command": segment, "skipped": True, "operator": operator})
                continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            last = {"ok": False, "output": f"命令链超时 ({timeout:g}s)", "exit_code": -1, "timed_out": True}
        else:
            builtin = _builtin(segment, session, current_cwd) if session is not None else None
            if builtin is not None:
                last = builtin
                current_cwd = Path(str(builtin.get("cwd") or current_cwd))
                environment.update(session.get("env", {}))
            else:
                last = _run_process(
                    segment,
                    cwd=current_cwd,
                    env_extra=environment,
                    stdin=stdin if not stdin_used else "",
                    timeout=remaining,
                )
                stdin_used = True
        results.append({"command": segment, **last})

    assert last is not None
    if len(commands) == 1:
        return {**last, "cwd": str(current_cwd)}
    rendered = []
    for index, result in enumerate(results, 1):
        if result.get("skipped"):
            rendered.append(f"[{index}] skipped ({result['operator']}): {result['command']}")
        else:
            rendered.append(f"[{index}] {result['command']}\n{result.get('output', '')}")
    output, truncated = _truncate("\n\n".join(rendered))
    return {
        "ok": bool(last["ok"]),
        "output": output,
        "exit_code": int(last.get("exit_code", 0)),
        "timed_out": bool(last.get("timed_out", False)),
        "truncated": truncated,
        "chain": results,
        "cwd": str(current_cwd),
    }


def run(
    action: str,
    command: str,
    working_dir: str = "",
    timeout: int = 0,
    stdin: str = "",
    env: dict[str, Any] | None = None,
    session_id: str = "",
    reset_session: bool = False,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action != "run_command":
        raise ValueError("shell 仅支持 action=run_command")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 不能为空")
    root = Path(context.get("root") or Path.cwd()).resolve()
    effective_timeout = float(timeout or context.get("tool_timeout") or 120)
    effective_timeout = max(1.0, min(effective_timeout, 3600.0))
    key = _session_key(context, root, session_id) if session_id else None
    if reset_session:
        if key is None:
            raise ValueError("reset_session 需要 session_id")
        _reset_session(key)
    session = _get_session(key, root) if key is not None else None

    def invoke() -> dict[str, Any]:
        base_cwd = Path(str(session["cwd"])) if session is not None else root
        cwd = _resolve_cwd(working_dir, root) if working_dir else _resolve_cwd(str(base_cwd), root)
        environment = dict(session.get("env", {})) if session is not None else {}
        for name, value in (env or {}).items():
            if not _ENV_KEY_RE.fullmatch(str(name)):
                raise ValueError(f"无效环境变量名: {name}")
            environment[str(name)] = str(value)
            if session is not None:
                session.setdefault("env", {})[str(name)] = str(value)
        result = _execute(
            command.strip(),
            cwd=cwd,
            environment=environment,
            stdin=stdin,
            timeout=effective_timeout,
            session=session,
        )
        if session is not None:
            history = session.setdefault("history", [])
            history.append(command.strip())
            del history[:-_SESSION_HISTORY_LIMIT]
            session["last_used"] = _now()
        return {**result, "session_id": session_id}

    if session is None:
        return invoke()
    with session["lock"]:
        return invoke()
