"""kemo-agent command line entry point.

The CLI is intentionally a thin transport layer.  It builds a stable request
object and delegates all agent behaviour, persistence and provider access to a
handler supplied by ``run``.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


VERSION = "0.1.0-dev"
DEFAULT_SOURCE = "cli"
DEFAULT_SESSION = "default"


class CLIError(RuntimeError):
    """A user-facing CLI error."""


@dataclass(frozen=True, slots=True)
class CLIRequest:
    """Transport contract between ``cli.py`` and the run core."""

    user: str
    prompt: str
    source: str = DEFAULT_SOURCE
    session_id: str = DEFAULT_SESSION

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kemo-agent",
        description="Run kemo-agent from a terminal.",
    )
    parser.add_argument("message", nargs="*", help="single-turn prompt")
    parser.add_argument("-p", "--prompt", help="single-turn prompt")
    parser.add_argument("-i", "--interactive", action="store_true", help="interactive chat mode")
    parser.add_argument("--stdin", action="store_true", help="read the prompt from standard input")
    parser.add_argument("-u", "--user", help="user directory name; defaults to KEMO_USER or the only local user")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="request source identifier")
    parser.add_argument("--session", default=DEFAULT_SESSION, help="context-window/session identifier")
    parser.add_argument(
        "--output",
        choices=("text", "json"),
        default="text",
        help="response output format",
    )
    parser.add_argument("--show-reasoning", action="store_true", help="stream reasoning deltas to stderr")
    parser.add_argument("--no-stream", action="store_true", help="wait for the complete response")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def discover_user(explicit_user: str | None, root: Path | None = None) -> str:
    try:
        from run.config import load_dotenv

        load_dotenv((root or _project_root()) / ".env")
    except ModuleNotFoundError:
        pass
    if explicit_user and explicit_user.strip():
        return explicit_user.strip()

    env_user = os.getenv("KEMO_USER", "").strip()
    if env_user:
        return env_user

    users_dir = (root or _project_root()) / "users"
    if users_dir.is_dir():
        candidates = sorted(
            entry.name
            for entry in users_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith("_")
        )
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise CLIError("检测到多个用户，请使用 --user 指定用户。")

    raise CLIError("未找到可用用户，请使用 --user 指定用户。")
def resolve_stream_handler() -> Callable[[dict[str, str]], Any] | None:
    try:
        module = importlib.import_module("run.cli")
    except ModuleNotFoundError:
        return None
    handler = getattr(module, "stream_cli_request", None)
    return handler if callable(handler) else None




def resolve_handler() -> Callable[[dict[str, str]], Any]:
    """Resolve the run-core bridge without coupling the CLI to its internals."""

    candidates = (
        ("run.cli", "handle_cli_request"),
        ("run", "handle_cli_request"),
    )
    errors: list[str] = []
    for module_name, attribute in candidates:
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name not in {module_name, module_name.split(".")[0]}:
                raise CLIError(f"加载运行核心失败：{exc}") from exc
            errors.append(module_name)
            continue
        handler = getattr(module, attribute, None)
        if callable(handler):
            return handler
        errors.append(f"{module_name}.{attribute}")

    checked = ", ".join(errors)
    raise CLIError(f"运行核心尚未提供 CLI 处理器；已检查：{checked}")


async def _await_result(value: Any) -> Any:
    return await value


def invoke_handler(handler: Callable[[dict[str, str]], Any], request: CLIRequest) -> Any:
    result = handler(request.to_dict())
    if inspect.isawaitable(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_await_result(result))
        raise CLIError("当前线程已有异步事件循环，无法从同步 CLI 嵌套运行处理器。")
    return result


def response_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, Mapping):
        for key in ("text", "content", "response"):
            value = response.get(key)
            if value is not None:
                return str(value)
    return str(response)
def _event_value(event: Any, name: str, default: Any = None) -> Any:
    if isinstance(event, Mapping):
        return event.get(name, default)
    return getattr(event, name, default)


def emit_event_stream(
    events: Any,
    *,
    output: str,
    stdout: Any,
    stderr: Any,
    show_reasoning: bool,
) -> None:
    wrote_text = False
    iterator = iter(events)
    try:
        for event in iterator:
            event_type = _event_value(event, "type", "")
            if output == "json":
                payload = event.to_dict() if hasattr(event, "to_dict") else dict(event)
                print(json.dumps(payload, ensure_ascii=False, default=str), file=stdout, flush=True)
                continue
            if event_type == "text_delta":
                print(_event_value(event, "content", ""), end="", file=stdout, flush=True)
                wrote_text = True
            elif event_type == "reasoning_delta" and show_reasoning:
                print(_event_value(event, "content", ""), end="", file=stderr, flush=True)
            elif event_type == "tool_call_start":
                print(f"\n[工具] {_event_value(event, 'tool_name', '')}：开始", file=stderr, flush=True)
            elif event_type == "tool_call_result":
                print(f"[工具] {_event_value(event, 'tool_name', '')}：完成", file=stderr, flush=True)
            elif event_type == "error":
                error = _event_value(event, "error", {}) or {}
                raise CLIError(str(error.get("message") or "运行失败"))
        if wrote_text:
            print(file=stdout)
    except BaseException:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
        raise




def emit_response(response: Any, output: str, stream: Any = None) -> None:
    target = stream or sys.stdout
    if output == "json":
        payload = response if isinstance(response, Mapping) else {"response": response_text(response)}
        print(json.dumps(payload, ensure_ascii=False, default=str), file=target)
    else:
        print(response_text(response), file=target)


def _single_prompt(args: argparse.Namespace, stdin: Any) -> str | None:
    sources = int(bool(args.prompt)) + int(bool(args.message)) + int(args.stdin)
    if sources > 1:
        raise CLIError("--prompt、位置参数和 --stdin 只能选择一种输入方式。")
    if args.stdin:
        prompt = stdin.read()
    elif args.prompt is not None:
        prompt = args.prompt
    elif args.message:
        prompt = " ".join(args.message)
    else:
        return None
    prompt = prompt.strip()
    if not prompt:
        raise CLIError("输入内容不能为空。")
    return prompt


def run_single(
    handler: Callable[[dict[str, str]], Any],
    user: str,
    prompt: str,
    source: str,
    session_id: str,
    output: str,
    stdout: Any,
    *,
    stderr: Any = None,
    stream_handler: Callable[[dict[str, str]], Any] | None = None,
    show_reasoning: bool = False,
) -> None:
    request = CLIRequest(user=user, prompt=prompt, source=source, session_id=session_id)
    if stream_handler is not None:
        payload = request.to_dict()
        payload["stream"] = True
        emit_event_stream(
            stream_handler(payload),
            output=output,
            stdout=stdout,
            stderr=stderr or sys.stderr,
            show_reasoning=show_reasoning,
        )
        return
    emit_response(invoke_handler(handler, request), output, stdout)
def _interactive_command(
    prompt: str,
    *,
    root: Path,
    user: str,
    source: str,
    session_id: str,
    stdout: Any,
) -> tuple[bool, str]:
    from run.engine import compress_context, context_status
    from run.history import clear_session, list_sessions, session_messages

    command, _, argument = prompt.partition(" ")
    command = command.lower()
    argument = argument.strip()
    if command == "/new":
        new_session = argument or f"session-{uuid.uuid4().hex[:8]}"
        print(f"已新建并切换会话：{new_session}", file=stdout)
        return True, new_session
    if command == "/use":
        if not argument:
            print("用法：/use <session>", file=stdout)
            return True, session_id
        print(f"已切换会话：{argument}", file=stdout)
        return True, argument
    if command == "/sessions":
        sessions = list_sessions(root, user, source)
        if not sessions:
            print("暂无已提交会话。", file=stdout)
        for item in sessions:
            marker = "*" if item["session_id"] == session_id else " "
            print(f"{marker} {item['session_id']} | rounds={item['rounds']} | {item['updated_at']}", file=stdout)
        return True, session_id
    if command == "/clear":
        clear_session(root, user, source, session_id)
        print(f"已清空会话：{session_id}", file=stdout)
        return True, session_id
    if command == "/history":
        messages = session_messages(root, user, source, session_id)
        if not messages:
            print("当前会话暂无历史。", file=stdout)
        for message in messages:
            print(f"{message.get('role', '?')}: {message.get('content', '')}", file=stdout)
        return True, session_id
    if command == "/status":
        try:
            status = context_status(
                {"user": user, "source": source, "session_id": session_id}, root=root
            )
        except Exception:
            # Keep the transport-level status useful in minimal/test workspaces
            # that do not yet contain a global configuration.
            sessions = {
                item["session_id"]: item for item in list_sessions(root, user, source)
            }
            rounds = sessions.get(session_id, {}).get("rounds", 0)
            print(
                f"user={user} | source={source} | session={session_id} | rounds={rounds}",
                file=stdout,
            )
            return True, session_id
        context = status["context"]
        print(
            f"user={user} | source={source} | session={session_id} | "
            f"rounds={status['rounds']} | context≈{context['estimated_tokens_before']}/"
            f"{context['input_budget']} | kept={context['rounds_kept']} | "
            f"removed={context['rounds_removed']} | summary={status['summary_cache_exists']}",
            file=stdout,
        )
        return True, session_id
    if command == "/compress":
        result = compress_context(
            {"user": user, "source": source, "session_id": session_id}, root=root
        )
        context = result["context"]
        summary = context["summary"]
        print(
            f"上下文整理完成：removed={context['rounds_removed']} | "
            f"kept={context['rounds_kept']} | cache_hit={summary['cache_hit']} | "
            f"generated={summary['generated']} | failed={summary['failed']}",
            file=stdout,
        )
        return True, session_id
    return False, session_id


def run_interactive(
    handler: Callable[[dict[str, str]], Any],
    user: str,
    source: str,
    session_id: str,
    output: str,
    stdin: Any,
    stdout: Any,
    *,
    stderr: Any = None,
    stream_handler: Callable[[dict[str, str]], Any] | None = None,
    show_reasoning: bool = False,
    root: Path | None = None,
) -> None:
    error_stream = stderr or sys.stderr
    base = (root or _project_root()).resolve()
    if getattr(stdin, "isatty", lambda: False)():
        print(f"kemo-agent CLI | user={user} | session={session_id}", file=stdout)
        print("命令：/new /sessions /use <session> /clear /history /status /compress /exit", file=stdout)

    while True:
        try:
            if getattr(stdin, "isatty", lambda: False)():
                print("> ", end="", flush=True, file=stdout)
            line = stdin.readline()
        except KeyboardInterrupt:
            print(file=stdout)
            return
        if line == "":
            return
        prompt = line.strip()
        if not prompt:
            continue
        if prompt.lower() in {"/exit", "/quit"}:
            return
        if prompt.startswith("/"):
            handled, session_id = _interactive_command(
                prompt, root=base, user=user, source=source,
                session_id=session_id, stdout=stdout,
            )
            if handled:
                continue
        try:
            run_single(
                handler, user, prompt, source, session_id, output, stdout,
                stderr=error_stream, stream_handler=stream_handler,
                show_reasoning=show_reasoning,
            )
        except KeyboardInterrupt:
            print("本轮已取消。", file=stdout)
        except Exception as exc:
            print(f"错误：{exc}", file=error_stream)


def main(
    argv: Sequence[str] | None = None,
    *,
    handler: Callable[[dict[str, str]], Any] | None = None,
    stdin: Any = None,
    stdout: Any = None,
    stderr: Any = None,
    root: Path | None = None,
) -> int:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    error_stream = stderr or sys.stderr
    parser = build_parser()

    try:
        args = parser.parse_args(argv)
        prompt = _single_prompt(args, input_stream)
        if args.interactive and prompt is not None:
            raise CLIError("交互模式不能同时提供单次 prompt。")
        if not args.source.strip() or not args.session.strip():
            raise CLIError("--source 和 --session 不能为空。")

        user = discover_user(args.user, root)
        active_handler = handler or resolve_handler()
        active_stream_handler = None if args.no_stream or handler is not None else resolve_stream_handler()

        if args.interactive or prompt is None:
            run_interactive(
                active_handler,
                user,
                args.source.strip(),
                args.session.strip(),
                args.output,
                input_stream,
                output_stream,
                stderr=error_stream,
                stream_handler=active_stream_handler,
                show_reasoning=args.show_reasoning,
                root=root,
            )
        else:
            run_single(
                active_handler,
                user,
                prompt,
                args.source.strip(),
                args.session.strip(),
                args.output,
                output_stream,
                stderr=error_stream,
                stream_handler=active_stream_handler,
                show_reasoning=args.show_reasoning,
            )
        return 0
    except KeyboardInterrupt:
        print("已取消。", file=error_stream)
        return 130
    except CLIError as exc:
        print(f"错误：{exc}", file=error_stream)
        return 2
    except Exception as exc:
        print(f"运行失败：{exc}", file=error_stream)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
