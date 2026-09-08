"""无命令黑名单的本地命令执行工具。"""

from __future__ import annotations

import hashlib
import locale
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from run.infra import (
    cancellable_subprocess_kwargs,
    detached_subprocess_kwargs,
    hidden_subprocess_kwargs,
    terminate_process_tree,
    visible_subprocess_kwargs,
)
from run.infra import process_snapshot
from plugins.shell.process_runtime import ProcessRuntimeDependencies, run_process, start_background
from plugins.shell.shell_detection import (
    _WINDOWS_HEAD_PIPE_RE,
    _looks_like_powershell,
    _mask_quoted_shell_content,
)
from run.tools import (
    assert_background_job_access,
    cancel_background_job,
    prepare_background_job,
    public_background_job,
    read_background_job,
    reconcile_background_job,
    update_background_job,
    write_job_request,
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
_SHELL_TYPES = frozenset(
    {
        "auto",
        "cmd",
        "powershell",
        "pwsh",
        "sh",
        "bash",
        "bash_login",
        "zsh",
        "zsh_login",
        "fish",
        "fish_login",
    }
)
_SHELL_EXECUTABLE_NAMES: dict[str, tuple[str, ...]] = {
    "cmd": ("cmd.exe", "cmd"),
    "powershell": ("powershell.exe", "powershell"),
    "pwsh": ("pwsh.exe", "pwsh"),
    "sh": ("sh",),
    "bash": ("bash",),
    "bash_login": ("bash",),
    "zsh": ("zsh",),
    "zsh_login": ("zsh",),
    "fish": ("fish",),
    "fish_login": ("fish",),
}


def _is_windows() -> bool:
    return os.name == "nt"


def _auto_shell_priority(command: str) -> tuple[str, ...]:
    """Return deterministic, non-login interpreter priorities for this host."""

    if _is_windows():
        if _looks_like_powershell(command):
            return ("pwsh", "powershell", "cmd")
        return ("cmd", "pwsh", "powershell")
    if sys.platform == "darwin":
        return ("zsh", "bash", "sh")
    if sys.platform.startswith(("linux", "cygwin", "msys")):
        return ("bash", "sh", "zsh")
    return ("sh", "bash", "zsh")


def _environment_key_matches(left: str, right: str) -> bool:
    return left.casefold() == right.casefold() if _is_windows() else left == right


def _environment_value(environment: dict[str, str], name: str) -> str | None:
    for key, value in environment.items():
        if _environment_key_matches(key, name):
            return str(value)
    return None


def _set_environment_value(
    environment: dict[str, str], name: str, value: str
) -> None:
    for key in list(environment):
        if _environment_key_matches(key, name):
            environment.pop(key, None)
    environment[name] = value


def _remove_environment_value(environment: dict[str, str], name: str) -> None:
    for key in list(environment):
        if _environment_key_matches(key, name):
            environment.pop(key, None)


def _merged_shell_environment(env_extra: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    _set_environment_value(environment, "PYTHONUTF8", "1")
    for name, value in env_extra.items():
        _set_environment_value(environment, str(name), str(value))
    return environment


def _shell_search_directories(search_path: str | None, *, cwd: Path) -> tuple[Path, ...]:
    """Resolve PATH entries against the child cwd without implicit host-cwd search."""

    if not search_path:
        return ()
    directories: list[Path] = []
    seen: set[str] = set()
    for raw_entry in search_path.split(os.pathsep):
        entry = raw_entry
        if len(entry) >= 2 and entry[0] == entry[-1] == '"':
            entry = entry[1:-1]
        # PATH entries are literal OS search locations.  Do not shell-expand
        # '~' here: the child receives the same raw PATH and would not expand it.
        directory = Path(entry) if entry else cwd
        if not directory.is_absolute():
            directory = cwd / directory
        directory = Path(os.path.abspath(directory))
        identity = os.path.normcase(str(directory))
        if identity in seen:
            continue
        seen.add(identity)
        directories.append(directory)
    return tuple(directories)


def _find_shell_executable(
    shell_type: str,
    *,
    search_path: str | None,
    comspec: str = "",
    cwd: Path,
) -> str | None:
    if shell_type == "cmd" and _is_windows() and comspec.strip():
        comspec_path = Path(comspec.strip()).expanduser()
        if comspec_path.is_absolute() and comspec_path.is_file():
            return str(comspec_path.resolve())
    for directory in _shell_search_directories(search_path, cwd=cwd):
        for candidate in _SHELL_EXECUTABLE_NAMES[shell_type]:
            # Supplying an explicit directory component prevents Windows'
            # shutil.which from silently searching the Web service cwd first.
            resolved = shutil.which(str(directory / candidate), path="")
            if resolved:
                return str(Path(resolved).resolve())
    return None


def _resolve_shell(
    command: str,
    shell_type: str,
    *,
    environment: dict[str, str],
    cwd: Path,
) -> tuple[str, str]:
    candidates = (
        _auto_shell_priority(command) if shell_type == "auto" else (shell_type,)
    )
    search_path = _environment_value(environment, "PATH")
    comspec = _environment_value(environment, "COMSPEC") or ""
    for candidate in candidates:
        executable = _find_shell_executable(
            candidate,
            search_path=search_path,
            comspec=comspec,
            cwd=cwd,
        )
        if executable:
            return candidate, executable
    if shell_type == "auto":
        raise RuntimeError(
            "当前平台没有可用的命令解释器；已检查: " + ", ".join(candidates)
        )
    raise ValueError(f"指定的命令解释器不可用或不在 PATH 中: {shell_type}")


def _isolate_shell_environment(
    environment: dict[str, str],
    *,
    env_extra: dict[str, str],
    resolved_shell_type: str,
) -> dict[str, str]:
    isolated = environment.copy()
    # Non-login shells must not silently execute startup files inherited from
    # the Web service account.  A caller can still opt in explicitly through
    # env=..., while *_login remains the clear profile-loading mode.
    if resolved_shell_type in {"sh", "bash", "zsh", "fish"}:
        _remove_environment_value(isolated, "BASH_ENV")
        _remove_environment_value(isolated, "ENV")
        for name, value in env_extra.items():
            if any(
                _environment_key_matches(name, hook) for hook in ("BASH_ENV", "ENV")
            ):
                _set_environment_value(isolated, name, value)
    return isolated


def _shell_environment(
    env_extra: dict[str, str], *, resolved_shell_type: str
) -> dict[str, str]:
    return _isolate_shell_environment(
        _merged_shell_environment(env_extra),
        env_extra=env_extra,
        resolved_shell_type=resolved_shell_type,
    )


def _failure_hint(command: str, shell_type: str, result: dict[str, Any]) -> str:
    if not _is_windows() or result.get("ok"):
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
    if not parts or parts[0].casefold() not in _BUILTIN_NAMES:
        return False
    name = parts[0].casefold()
    argument = parts[1].strip() if len(parts) > 1 else ""
    if not argument:
        return True
    # Builtins intentionally cover only literal, single-operation forms.  If
    # shell expansion, a pipe/redirection, a glob or an interpreter option is
    # present, leave the whole command to the selected native interpreter.
    shell_markers = ("|", "<", ">", "&", "`", "$", "%", "*", "?")
    if any(marker in argument for marker in shell_markers):
        return False
    if argument.startswith("-"):
        return False
    if _is_windows() and re.match(r"^/[A-Za-z?](?:\s|$)", argument):
        return False
    if name in {"pwd", "history"}:
        return False
    if name in {"export", "set"} and "=" not in argument:
        return False
    if name == "unset" and not _ENV_KEY_RE.fullmatch(argument):
        return False
    return True


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
        _set_environment_value(session.setdefault("env", {}), key, value)
        return {"ok": True, "output": f"{key}={value}", "exit_code": 0}

    if name == "unset":
        if not argument or not _ENV_KEY_RE.fullmatch(argument):
            return {"ok": False, "output": "unset: 需要有效变量名", "exit_code": 1}
        _remove_environment_value(session.setdefault("env", {}), argument)
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


def _process_command(
    command: str,
    shell_type: str,
    *,
    environment: dict[str, str],
    cwd: Path,
) -> tuple[str | list[str], bool, str]:
    resolved_shell_type, executable = _resolve_shell(
        command,
        shell_type,
        environment=environment,
        cwd=cwd,
    )
    if resolved_shell_type == "cmd":
        if _is_windows():
            # list2cmdline escapes a quoted command for the C runtime, but
            # cmd.exe parses /c with different rules and sees the backslashes
            # as literal characters.  Build the one Windows command line that
            # cmd owns, with /d disabling per-user AutoRun scripts.
            executable_arg = subprocess.list2cmdline([executable])
            process_command: str | list[str] = (
                f'{executable_arg} /d /s /c "{command}"'
            )
        else:
            process_command = [executable, "/d", "/c", command]
    elif resolved_shell_type == "powershell":
        process_command = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _powershell_script(command, modern=False),
        ]
    elif resolved_shell_type == "pwsh":
        process_command = [
            executable,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            _powershell_script(command, modern=True),
        ]
    elif resolved_shell_type == "sh":
        process_command = [executable, "-c", command]
    elif resolved_shell_type == "bash":
        process_command = [executable, "--noprofile", "--norc", "-c", command]
    elif resolved_shell_type == "bash_login":
        process_command = [executable, "-l", "-c", command]
    elif resolved_shell_type == "zsh":
        process_command = [executable, "-f", "-c", command]
    elif resolved_shell_type == "zsh_login":
        process_command = [executable, "-l", "-c", command]
    elif resolved_shell_type == "fish":
        process_command = [executable, "--no-config", "-c", command]
    elif resolved_shell_type == "fish_login":
        process_command = [executable, "-l", "-c", command]
    else:
        raise AssertionError(f"未处理的命令解释器: {resolved_shell_type}")
    # The selected executable owns the command grammar.  Avoid shell=True so
    # Python never inserts a second implicit shell with platform-dependent
    # quoting, profile loading or process-tree behavior.
    return process_command, False, resolved_shell_type


def _prepare_process_command(
    command: str,
    shell_type: str,
    *,
    env_extra: dict[str, str],
    cwd: Path,
) -> tuple[str | list[str], bool, str, dict[str, str]]:
    """Resolve one interpreter from the same environment the child receives."""

    merged_environment = _merged_shell_environment(env_extra)
    process_command, use_shell, resolved_shell_type = _process_command(
        command,
        shell_type,
        environment=merged_environment,
        cwd=cwd,
    )
    environment = _isolate_shell_environment(
        merged_environment,
        env_extra=env_extra,
        resolved_shell_type=resolved_shell_type,
    )
    return process_command, use_shell, resolved_shell_type, environment


def _process_runtime_dependencies() -> ProcessRuntimeDependencies:
    return ProcessRuntimeDependencies(
        prepare_process_command=_prepare_process_command,
        subprocess=subprocess,
        time=time,
        hashlib=hashlib,
        sys=sys,
        decode_output=_decode_output,
        truncate=_truncate,
        visible_subprocess_kwargs=visible_subprocess_kwargs,
        hidden_subprocess_kwargs=hidden_subprocess_kwargs,
        cancellable_subprocess_kwargs=cancellable_subprocess_kwargs,
        detached_subprocess_kwargs=detached_subprocess_kwargs,
        terminate_process_tree=terminate_process_tree,
        process_snapshot=process_snapshot,
        prepare_background_job=prepare_background_job,
        write_job_request=write_job_request,
        update_background_job=update_background_job,
        read_background_job=read_background_job,
        cancel_background_job=cancel_background_job,
        reconcile_background_job=reconcile_background_job,
        public_background_job=public_background_job,
    )


def _run_process(
    command: str,
    *,
    cwd: Path,
    env_extra: dict[str, str],
    stdin: str,
    timeout: float,
    cancel_event: threading.Event | None,
    shell_type: str = "auto",
    show_terminal: bool = False,
) -> dict[str, Any]:
    return run_process(
        command, cwd=cwd, env_extra=env_extra, stdin=stdin, timeout=timeout,
        cancel_event=cancel_event, shell_type=shell_type, show_terminal=show_terminal,
        deps=_process_runtime_dependencies(),
    )


def _start_background(
    command: str,
    *,
    cwd: Path,
    environment: dict[str, str],
    shell_type: str,
    context: dict[str, Any],
    timeout: float,
    cancel_event: threading.Event | None,
    show_terminal: bool = False,
) -> dict[str, Any]:
    return start_background(
        command, cwd=cwd, environment=environment, shell_type=shell_type,
        context=context, timeout=timeout, cancel_event=cancel_event,
        show_terminal=show_terminal, deps=_process_runtime_dependencies(),
        source_root=Path(__file__).resolve().parents[2],
    )


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
    show_terminal: bool = False,
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
            show_terminal=show_terminal,
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
            for name, value in runtime_state.get("env", {}).items():
                _set_environment_value(environment, name, value)
        results.append({"command": segment, **last})

    assert last is not None
    if len(commands) == 1:
        return {**last, "cwd": str(current_cwd), "shell_type": "builtin"}
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
        "shell_type": "builtin",
    }


def run(
    command: str = "",
    working_dir: str = "",
    timeout: int = 0,
    stdin: str = "",
    env: dict[str, Any] | None = None,
    session_id: str = "",
    reset_session: bool = False,
    shell_type: str = "auto",
    chain_timeout_mode: str = "total",
    action: str = "run",
    background: bool = False,
    job_id: str = "",
    show_terminal: bool = False,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in {"run", "status", "cancel"}:
        raise ValueError("action 必须是 run/status/cancel")
    if not isinstance(background, bool):
        raise ValueError("background 必须是布尔值")
    if not isinstance(show_terminal, bool):
        raise ValueError("show_terminal 必须是布尔值")
    if shell_type not in _SHELL_TYPES:
        raise ValueError(f"不支持的 shell_type: {shell_type}")
    if chain_timeout_mode not in {"total", "per_command"}:
        raise ValueError(f"不支持的 chain_timeout_mode: {chain_timeout_mode}")
    root = Path(context.get("root") or Path.cwd()).resolve()
    user = str(context.get("user") or "").strip()
    if action in {"status", "cancel"}:
        if not user:
            raise ValueError("后台作业操作需要工具上下文 user")
        current = read_background_job(root, user, job_id)
        assert_background_job_access(
            current,
            source=str(context.get("source") or context.get("caller") or ""),
            session_id=str(context.get("session_id") or context.get("task_id") or ""),
        )
        current = (
            cancel_background_job(root, user, job_id)
            if action == "cancel"
            else reconcile_background_job(root, user, job_id)
        )
        operation_ok = (
            True
            if action == "status"
            else current.get("status") in {"cancelling", "cancelled"}
            or bool(current.get("cancel_requested"))
        )
        return {
            "ok": operation_ok,
            "operation_ok": operation_ok,
            "job_succeeded": current.get("status") == "completed",
            "operation": action,
            "output": f"后台作业状态：{current.get('status')}",
            **public_background_job(current, root=root),
        }
    if not isinstance(command, str) or not command.strip():
        raise ValueError("action=run 时 command 不能为空")
    if background and stdin:
        raise ValueError("后台模式不支持 stdin；请改用文件或命令参数传入")
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
            _set_environment_value(environment, str(name), str(value))
            if session is not None:
                _set_environment_value(
                    session.setdefault("env", {}), str(name), str(value)
                )
        result = (
            _start_background(
                command.strip(),
                cwd=cwd,
                environment=environment,
                shell_type=shell_type,
                context=context,
                timeout=effective_timeout,
                cancel_event=cancel_event,
                show_terminal=show_terminal,
            )
            if background
            else _execute(
                command.strip(),
                cwd=cwd,
                environment=environment,
                stdin=stdin,
                timeout=effective_timeout,
                cancel_event=cancel_event,
                session=session,
                shell_type=shell_type,
                chain_timeout_mode=chain_timeout_mode,
                show_terminal=show_terminal,
            )
        )
        hint = _failure_hint(
            command.strip(), str(result.get("shell_type") or shell_type), result
        )
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
