"""Process execution pipeline used by the shell tool facade."""

from __future__ import annotations

from typing import Any

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


_OWNED = {name for name in (
    "_process_command", "_prepare_process_command", "_process_runtime_dependencies",
    "_run_process", "_start_background", "_execute",
)}
_IMPLEMENTATIONS = {name: globals()[name] for name in _OWNED}

def configure(context: dict[str, Any]) -> None:
    """Bind facade dependencies while retaining local implementation functions."""
    for name, value in context.items():
        if name.startswith("__"):
            continue
        if name in _OWNED and getattr(value, "__shell_execution_wrapper__", False):
            globals()[name] = _IMPLEMENTATIONS[name]
        else:
            globals()[name] = value

def invoke(name: str, *args: Any, **kwargs: Any) -> Any:
    return _IMPLEMENTATIONS[name](*args, **kwargs)
