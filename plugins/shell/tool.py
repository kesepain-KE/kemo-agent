"""无命令黑名单的本地命令执行工具。"""

from __future__ import annotations

import locale
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from run.process_utils import (
    cancellable_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_process_tree,
)


_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SESSION_HISTORY_LIMIT = 2000
_SESSION_MAX_COUNT = 500
_SESSION_TTL_SECONDS = 86400
_OUTPUT_MAX_CHARS = 100_000
_BUILTIN_NAMES = frozenset(
    {
        "cat",
        "cd",
        "chdir",
        "del",
        "dir",
        "echo",
        "env",
        "export",
        "history",
        "ls",
        "mkdir",
        "pwd",
        "rm",
        "set",
        "type",
        "unset",
    }
)
_SESSION_LOCK = threading.RLock()
_SESSION_CACHE: dict[tuple[str, str, str, str], dict[str, Any]] = {}
_WINDOWS_HEAD_PIPE_RE = re.compile(r"\|\s*head(?:\.exe)?(?:\s|$)", re.IGNORECASE)


def _failure_hint(command: str, shell_type: str, result: dict[str, Any]) -> str:
    if os.name != "nt" or result.get("ok"):
        return ""
    output = str(result.get("output") or "")
    lowered = output.casefold()
    if _WINDOWS_HEAD_PIPE_RE.search(command):
        return (
            "当前为 Windows 环境，head 通常不可用。PowerShell 可使用 "
            "Select-Object -First；按字符截断时可使用 Out-String 后 Substring，"
            "读取文件内容优先使用 file.read_range。"
        )
    if (
        re.search(r"\bget-filehash\b", command, re.IGNORECASE)
        and shell_type in {"auto", "powershell", "pwsh"}
        and any(
            marker in lowered
            for marker in (
                "commandnotfoundexception",
                "is not recognized",
                "not recognized",
                "无法将",
                "识别为 cmdlet",
            )
        )
    ):
        return (
            "当前 PowerShell 无法使用 Get-FileHash。优先调用 file 工具的 hash action；"
            "必须使用系统命令时可运行 certutil -hashfile <path> SHA256。"
        )
    return ""


def _now() -> float:
    return time.time()


def _decode_output(data: bytes) -> str:
    candidates = ["utf-8"]
    try:
        preferred = locale.getpreferredencoding(False)
        if preferred and preferred.casefold() not in {"utf-8", "utf8"}:
            candidates.append(preferred)
    except Exception:
        pass
    for encoding in candidates:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _truncate(value: str) -> tuple[str, bool]:
    if len(value) <= _OUTPUT_MAX_CHARS:
        return value, False
    return value[:_OUTPUT_MAX_CHARS] + "\n…(输出已截断)", True


def _session_key(
    context: dict[str, Any], root: Path, session_id: str
) -> tuple[str, str, str, str]:
    return (
        str(root).casefold(),
        str(context.get("user") or ""),
        str(context.get("source") or ""),
        session_id,
    )


def _cleanup_expired_sessions() -> None:
    deadline = _now() - _SESSION_TTL_SECONDS
    expired = [
        key
        for key, session in _SESSION_CACHE.items()
        if not isinstance(session, dict)
        or float(session.get("last_used", 0)) < deadline
    ]
    for key in expired:
        _SESSION_CACHE.pop(key, None)
    if len(_SESSION_CACHE) > _SESSION_MAX_COUNT:
        oldest = sorted(
            _SESSION_CACHE,
            key=lambda key: float(_SESSION_CACHE[key].get("last_used", 0)),
        )
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
    """拆分仅由框架内置命令组成的简单命令链。"""
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
            if command[index : index + 2] in {"&&", "||"}:
                operator = command[index : index + 2]
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


def _is_builtin_command(command: str) -> bool:
    parts = command.strip().split(maxsplit=1)
    return bool(parts) and parts[0].casefold() in _BUILTIN_NAMES


def _powershell_script(command: str, *, modern: bool) -> str:
    safeguards = [
        "$ErrorActionPreference = 'Stop'",
        "Set-StrictMode -Version Latest",
    ]
    if modern:
        safeguards.append("$PSNativeCommandUseErrorActionPreference = $true")
    return "; ".join((*safeguards, command))


def _resolve_cwd(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser() if value else root
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_dir():
        raise NotADirectoryError(f"工作目录不存在: {candidate}")
    return candidate


def _resolve_path(value: str, cwd: Path) -> Path:
    """解析相对 cwd 的文件系统路径，并兼容成对引号。"""
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1]
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve()


def _builtin(command: str, session: dict[str, Any], cwd: Path) -> dict[str, Any] | None:
    parts = command.strip().split(maxsplit=1)
    name = parts[0].casefold()
    argument = parts[1].strip() if len(parts) > 1 else ""

    if name in {"cd", "chdir"}:
        target_text = argument.strip("\"'") if argument else str(Path.home())
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
            output = (
                "\n".join(f"{key}={values[key]}" for key in sorted(values)) or "(empty)"
            )
            return {"ok": True, "output": output, "exit_code": 0}
        if "=" not in argument:
            return {"ok": False, "output": f"{name}: 需要 KEY=VALUE", "exit_code": 1}
        key, value = argument.split("=", 1)
        key = key.strip()
        if not _ENV_KEY_RE.fullmatch(key):
            return {"ok": False, "output": f"{name}: 无效变量名: {key}", "exit_code": 1}
        value = value.strip().strip("\"'")
        session.setdefault("env", {})[key] = value
        return {"ok": True, "output": f"{key}={value}", "exit_code": 0}

    if name == "unset":
        if not argument or not _ENV_KEY_RE.fullmatch(argument):
            return {"ok": False, "output": "unset: 需要有效变量名", "exit_code": 1}
        session.setdefault("env", {}).pop(argument, None)
        return {"ok": True, "output": "", "exit_code": 0}

    if name == "history":
        history = session.get("history", [])
        output = "\n".join(
            f"[{index}] {item}" for index, item in enumerate(history[-50:], 1)
        )
        return {"ok": True, "output": output or "(empty)", "exit_code": 0}

    if name in {"cat", "type"}:
        if not argument:
            return {"ok": False, "output": f"{name}: 需要文件路径", "exit_code": 1}
        target = _resolve_path(argument, cwd)
        if not target.is_file():
            return {
                "ok": False,
                "output": f"{name}: 文件不存在: {target}",
                "exit_code": 1,
            }
        try:
            content = target.read_text("utf-8")
        except UnicodeDecodeError:
            content = _decode_output(target.read_bytes())
        output, truncated = _truncate(content)
        return {"ok": True, "output": output, "exit_code": 0, "truncated": truncated}

    if name in {"ls", "dir"}:
        target = _resolve_path(argument, cwd) if argument else cwd
        if not target.is_dir():
            return {
                "ok": False,
                "output": f"{name}: 目录不存在: {target}",
                "exit_code": 1,
            }
        entries = [
            child.name + ("/" if child.is_dir() else "")
            for child in sorted(target.iterdir())
        ]
        output, truncated = _truncate("\n".join(entries) or "(空目录)")
        return {"ok": True, "output": output, "exit_code": 0, "truncated": truncated}

    if name == "mkdir":
        if not argument:
            return {"ok": False, "output": "mkdir: 需要目录路径", "exit_code": 1}
        target = _resolve_path(argument, cwd)
        if target.exists():
            return {
                "ok": False,
                "output": f"mkdir: 路径已存在: {target}",
                "exit_code": 1,
            }
        target.mkdir(parents=True, exist_ok=False)
        return {"ok": True, "output": str(target), "exit_code": 0}

    if name == "echo":
        return {"ok": True, "output": argument, "exit_code": 0}

    if name in {"rm", "del"}:
        if not argument:
            return {"ok": False, "output": f"{name}: 需要文件路径", "exit_code": 1}
        target = _resolve_path(argument, cwd)
        if not target.exists():
            return {
                "ok": False,
                "output": f"{name}: 文件不存在: {target}",
                "exit_code": 1,
            }
        if target.is_dir():
            return {
                "ok": False,
                "output": f"{name}: 目标是目录，请用专用工具或 shell 递归删除: {target}",
                "exit_code": 1,
            }
        target.unlink()
        return {"ok": True, "output": f"已删除: {target}", "exit_code": 0}

    return None


def _run_process(
    command: str,
    *,
    cwd: Path,
    env_extra: dict[str, str],
    stdin: str,
    timeout: float,
    cancel_event: threading.Event | None,
    shell_type: str = "auto",
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    environment.update(env_extra)
    if shell_type == "cmd":
        process_command: str | list[str] = ["cmd", "/c", command]
        use_shell = False
    elif shell_type == "powershell":
        process_command = [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _powershell_script(command, modern=False),
        ]
        use_shell = False
    elif shell_type == "pwsh":
        process_command = [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _powershell_script(command, modern=True),
        ]
        use_shell = False
    elif shell_type == "bash":
        process_command = ["bash", "-c", command]
        use_shell = False
    elif shell_type == "bash_login":
        process_command = ["bash", "-l", "-c", command]
        use_shell = False
    else:
        process_command = command
        use_shell = True
    if cancel_event is None:
        try:
            completed = subprocess.run(
                process_command,
                shell=use_shell,
                cwd=str(cwd),
                env=environment,
                input=stdin.encode("utf-8") if stdin else None,
                timeout=timeout,
                capture_output=True,
                **hidden_subprocess_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            stdout = _decode_output(exc.stdout or b"")
            stderr = _decode_output(exc.stderr or b"")
            output, truncated = _truncate(
                "\n".join(value for value in (stdout, stderr) if value).strip()
            )
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
    process = subprocess.Popen(
        process_command,
        shell=use_shell,
        cwd=str(cwd),
        env=environment,
        stdin=subprocess.PIPE if stdin else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **cancellable_subprocess_kwargs(),
    )
    input_data = stdin.encode("utf-8") if stdin else None
    deadline = time.monotonic() + timeout
    cancelled = False
    timed_out = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            terminate_process_tree(process)
        remaining = deadline - time.monotonic()
        if remaining <= 0 and process.poll() is None:
            timed_out = True
            terminate_process_tree(process)
        try:
            stdout_data, stderr_data = process.communicate(
                input=input_data, timeout=max(0.01, min(0.1, max(0.0, remaining)))
            )
            break
        except subprocess.TimeoutExpired:
            input_data = None
            continue
    if cancelled or timed_out:
        stdout = _decode_output(stdout_data or b"")
        stderr = _decode_output(stderr_data or b"")
        output, truncated = _truncate(
            "\n".join(value for value in (stdout, stderr) if value).strip()
        )
        return {
            "ok": False,
            "output": output
            or (
                "命令因用户紧急停止而取消" if cancelled else f"命令超时 ({timeout:g}s)"
            ),
            "exit_code": -1,
            "timed_out": timed_out,
            "cancelled": cancelled,
            "truncated": truncated,
        }
    stdout = _decode_output(stdout_data).strip()
    stderr = _decode_output(stderr_data).strip()
    if stdout and stderr:
        output = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"
    elif stderr:
        output = f"STDERR:\n{stderr}"
    else:
        output = stdout or "(无输出)"
    output, truncated = _truncate(output)
    return {
        "ok": process.returncode == 0,
        "output": output,
        "exit_code": process.returncode,
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
    cancel_event: threading.Event | None,
    session: dict[str, Any] | None,
    shell_type: str = "auto",
    chain_timeout_mode: str = "total",
) -> dict[str, Any]:
    commands: list[str] = []
    operators: list[str] = []
    if shell_type == "auto":
        try:
            commands, operators = _split_chain(command)
        except ValueError:
            # Native interpreters own their full grammar. A framework parser must
            # not reject valid PowerShell/Bash constructs merely because it cannot
            # understand their quoting or escaping rules.
            commands = []

    if not commands or not all(_is_builtin_command(segment) for segment in commands):
        result = _run_process(
            command,
            cwd=cwd,
            env_extra=environment,
            stdin=stdin,
            timeout=timeout,
            cancel_event=cancel_event,
            shell_type=shell_type,
        )
        return {**result, "cwd": str(cwd)}

    deadline = time.monotonic() + timeout if chain_timeout_mode == "total" else None
    results: list[dict[str, Any]] = []
    current_cwd = cwd
    last: dict[str, Any] | None = None
    runtime_state = (
        session
        if session is not None
        else {"cwd": str(cwd), "env": environment, "history": []}
    )

    for index, segment in enumerate(commands):
        if cancel_event is not None and cancel_event.is_set():
            last = {
                "ok": False,
                "output": "命令因用户紧急停止而取消",
                "exit_code": -1,
                "cancelled": True,
            }
            results.append({"command": segment, **last})
            break
        if index:
            operator = operators[index - 1]
            if (operator == "&&" and last is not None and not last["ok"]) or (
                operator == "||" and last is not None and last["ok"]
            ):
                results.append(
                    {"command": segment, "skipped": True, "operator": operator}
                )
                continue
        remaining = timeout if deadline is None else deadline - time.monotonic()
        if remaining <= 0:
            last = {
                "ok": False,
                "output": f"命令链超时 ({timeout:g}s)",
                "exit_code": -1,
                "timed_out": True,
            }
        else:
            builtin = _builtin(segment, runtime_state, current_cwd)
            assert builtin is not None
            last = builtin
            current_cwd = Path(str(builtin.get("cwd") or current_cwd))
            environment.update(runtime_state.get("env", {}))
        results.append({"command": segment, **last})

    assert last is not None
    if len(commands) == 1:
        return {**last, "cwd": str(current_cwd)}
    rendered = []
    for index, result in enumerate(results, 1):
        if result.get("skipped"):
            rendered.append(
                f"[{index}] skipped ({result['operator']}): {result['command']}"
            )
        else:
            rendered.append(
                f"[{index}] {result['command']}\n{result.get('output', '')}"
            )
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
    command: str,
    working_dir: str = "",
    timeout: int = 0,
    stdin: str = "",
    env: dict[str, Any] | None = None,
    session_id: str = "",
    reset_session: bool = False,
    shell_type: str = "auto",
    chain_timeout_mode: str = "total",
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command 不能为空")
    if shell_type not in {"auto", "cmd", "powershell", "pwsh", "bash", "bash_login"}:
        raise ValueError(f"不支持的 shell_type: {shell_type}")
    if chain_timeout_mode not in {"total", "per_command"}:
        raise ValueError(f"不支持的 chain_timeout_mode: {chain_timeout_mode}")
    root = Path(context.get("root") or Path.cwd()).resolve()
    context_timeout = context.get("tool_timeout")
    if context_timeout is None:
        raise ValueError("tool_timeout 未在上下文中提供，请检查配置链路")
    effective_timeout = float(timeout or context_timeout)
    effective_timeout = max(1.0, min(effective_timeout, 3600.0))
    cancel_event = context.get("cancel_event")
    if cancel_event is not None and not isinstance(cancel_event, threading.Event):
        cancel_event = None
    key = _session_key(context, root, session_id) if session_id else None
    if reset_session:
        if key is None:
            raise ValueError("reset_session 需要 session_id")
        _reset_session(key)
    session = _get_session(key, root) if key is not None else None

    def invoke() -> dict[str, Any]:
        base_cwd = Path(str(session["cwd"])) if session is not None else root
        cwd = (
            _resolve_cwd(working_dir, root)
            if working_dir
            else _resolve_cwd(str(base_cwd), root)
        )
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
            cancel_event=cancel_event,
            session=session,
            shell_type=shell_type,
            chain_timeout_mode=chain_timeout_mode,
        )
        hint = _failure_hint(command.strip(), shell_type, result)
        if hint:
            result = {**result, "hint": hint}
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
