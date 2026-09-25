"""kemo-agent 命令行入口点。

CLI 有意成为一个薄传输层。  它建立了一个稳定的请求
对象并将所有代理行为、持久性和提供者访问委托给
由“run”提供的处理程序。"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import inspect
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


VERSION = "1.3.0"
DEFAULT_SOURCE = "cli"
DEFAULT_SESSION = "default"


class CLIError(RuntimeError):
    """A user-facing CLI error."""


def _close_cli_session(root: Path, user: str, source: str, session_id: str) -> None:
    """Best-effort close hook; cleanup must never alter the CLI exit code."""
    if not str(session_id or "").strip():
        return
    # The desktop CLI may intentionally attach to the shared Web interactive
    # space.  That space is owned by Web leases and must not be closed merely
    # because this CLI process exits.  Only CLI-like sources use this hook.
    normalized_source = str(source or "").strip()
    if normalized_source in {"web", "app"} or normalized_source.startswith("message:"):
        return
    try:
        from run.history import close_session, queue_memory_extraction
        try:
            queue_memory_extraction(root, user, source, session_id)
        finally:
            close_session(root, user, source, session_id)
    except Exception:
        pass


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


def discover_user(explicit_user: str | None, root: Path | None = None, *, interactive: bool = False) -> str:
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
            if interactive and sys.stdin.isatty():
                print()
                print("请选择用户：")
                for i, name in enumerate(candidates, 1):
                    print(f"  {i}) {name}")
                while True:
                    try:
                        choice = input("> ").strip()
                        idx = int(choice) - 1
                        if 0 <= idx < len(candidates):
                            print()
                            return candidates[idx]
                    except (ValueError, EOFError, KeyboardInterrupt):
                        pass
                    print("请输入数字序号。")
            raise CLIError("检测到多个用户，请使用 --user 指定用户。")

    raise CLIError("未找到可用用户，请使用 --user 指定用户。")
def resolve_stream_handler() -> Callable[[dict[str, str]], Any] | None:
    try:
        module = importlib.import_module("run.infra")
    except ModuleNotFoundError:
        return None
    handler = getattr(module, "stream_cli_request", None)
    return handler if callable(handler) else None


def resolve_interactive_context(user: str, root: Path) -> dict[str, str]:
    try:
        module = importlib.import_module("run.infra")
    except ModuleNotFoundError as exc:
        raise CLIError("运行核心尚未提供 CLI 会话解析器") from exc
    resolver = getattr(module, "resolve_interactive_context", None)
    if not callable(resolver):
        raise CLIError("运行核心尚未提供 CLI 会话解析器")
    value = resolver(user, root=root)
    if not isinstance(value, dict):
        raise CLIError("运行核心返回了无效的 CLI 会话")
    source = str(value.get("source") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    if not source or not session_id:
        raise CLIError("运行核心返回了空的 CLI 会话")
    return {"source": source, "session_id": session_id}




def resolve_handler() -> Callable[[dict[str, str]], Any]:
    """Resolve the run-core bridge without coupling the CLI to its internals."""

    candidates = (
        ("run.infra", "handle_cli_request"),
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


def _truncate_args(args: Any, max_len: int = 120) -> str:
    """将参数/结果转为紧凑字符串并截断。"""
    if args is None:
        return ""
    text = json.dumps(args, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _truncate_result(result: Any, max_len: int = 100) -> str:
    """将工具结果转为摘要字符串并截断。"""
    if result is None:
        return ""
    if isinstance(result, dict):
        if result.get("ok") is True:
            text = result.get("message") or result.get("text") or json.dumps(result, ensure_ascii=False, default=str)
        elif result.get("ok") is False:
            text = result.get("error") or json.dumps(result, ensure_ascii=False, default=str)
        else:
            text = json.dumps(result, ensure_ascii=False, default=str)
    elif isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


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
            elif event_type == "tool_call_start":
                name = _event_value(event, "tool_name", "")
                args = _event_value(event, "arguments")
                args_str = _truncate_args(args)
                if args_str:
                    print(f"\n  ⚙ {name} {args_str}", file=stdout, flush=True)
                else:
                    print(f"\n  ⚙ {name}", file=stdout, flush=True)
            elif event_type == "tool_call_result":
                name = _event_value(event, "tool_name", "")
                result = _event_value(event, "result")
                metadata = _event_value(event, "metadata", {}) or {}
                status = metadata.get("status", "")
                if status == "completed":
                    print(f"  ✓ {name}", file=stdout, flush=True)
                elif status == "failed":
                    print(f"  ✗ {name}", file=stdout, flush=True)
                elif isinstance(result, dict) and result.get("ok") is True:
                    print(f"  ✓ {name}", file=stdout, flush=True)
                elif isinstance(result, dict) and result.get("ok") is False:
                    print(f"  ✗ {name}", file=stdout, flush=True)
                else:
                    summary = _truncate_result(result)
                    if summary:
                        print(f"  ✓ {name}: {summary}", file=stdout, flush=True)
                    else:
                        print(f"  ✓ {name}", file=stdout, flush=True)
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
def _interactive_command(*args: Any, **kwargs: Any):
    from run.infra import cli_interactive as _interactive
    _interactive.configure(globals())
    return _interactive.invoke(*args, **kwargs)



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
) -> str:
    error_stream = stderr or sys.stderr
    base = (root or _project_root()).resolve()
    if getattr(stdin, "isatty", lambda: False)():
        print(f"kemo-agent 交互模式 | 用户: {user} | 会话: {session_id}")
        print("─" * 50)
        print("命令：")
        print("  /new [名称]          新建会话")
        print("  /sessions            列出所有会话")
        print("  /use <会话ID>        切换会话")
        print("  /history             查看当前会话历史")
        print("  /clear               清空当前会话")
        print("  /status              查看上下文占用")
        print("  /compress            压缩上下文")
        print("  /memory              列出记忆")
        print("  /remember <内容>     保存永久记忆")
        print("  /forget <关键词>     删除记忆")
        print("  /plans               列出任务计划")
        print("  /plan <目标>         创建任务计划")
        print("  /plan-show <计划ID>  查看计划详情")
        print("  /plan-approve <ID>   批准并执行计划")
        print("  /plan-pause <ID>     暂停计划")
        print("  /plan-resume <ID>    恢复计划")
        print("  /plan-cancel <ID>    取消计划")
        print("  /crons               列出定时任务")
        print("  /cron <要求>         创建定时任务")
        print("  /cron-show <任务ID>  查看任务详情")
        print("  /cron-pause <ID>     暂停定时任务")
        print("  /cron-resume <ID>    恢复定时任务")
        print("  /cron-cancel <ID>    取消定时任务")
        print("  /cron-run <ID>       立即执行定时任务")
        print("  /cron-start          启动调度器")
        print("  /cron-stop           停止调度器")
        print("  /exit                退出")
        print("─" * 50)

    while True:
        try:
            if getattr(stdin, "isatty", lambda: False)():
                print("> ", end="", flush=True, file=stdout)
            line = stdin.readline()
        except KeyboardInterrupt:
            print(file=stdout)
            return session_id
        if line == "":
            return session_id
        prompt = line.strip()
        if not prompt:
            continue
        if prompt.lower() in {"/exit", "/quit"}:
            return session_id
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

        user = discover_user(args.user, root, interactive=(prompt is None and not args.stdin))
        source = args.source.strip()
        session_id = args.session.strip()
        if (
            handler is None
            and source == DEFAULT_SOURCE
            and session_id == DEFAULT_SESSION
        ):
            context = resolve_interactive_context(
                user,
                (root or _project_root()).resolve(),
            )
            source = context["source"]
            session_id = context["session_id"]
        active_handler = handler or resolve_handler()
        active_stream_handler = None if args.no_stream or handler is not None else resolve_stream_handler()

        try:
            if prompt is None:
                session_id = run_interactive(
                    active_handler,
                    user,
                    source,
                    session_id,
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
                    source,
                    session_id,
                    args.output,
                    output_stream,
                    stderr=error_stream,
                    stream_handler=active_stream_handler,
                    show_reasoning=args.show_reasoning,
                )
        finally:
            _close_cli_session((root or _project_root()).resolve(), user, source, session_id)
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
