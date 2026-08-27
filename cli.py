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
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


VERSION = "1.2.3"
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
        raise CLIError("运行核心尚未提供共享会话解析器") from exc
    resolver = getattr(module, "resolve_interactive_context", None)
    if not callable(resolver):
        raise CLIError("运行核心尚未提供共享会话解析器")
    value = resolver(user, root=root)
    if not isinstance(value, dict):
        raise CLIError("运行核心返回了无效的共享会话")
    source = str(value.get("source") or "").strip()
    session_id = str(value.get("session_id") or "").strip()
    if not source or not session_id:
        raise CLIError("运行核心返回了空的共享会话")
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
    from run.config import load_config
    from run.history import clear_session, list_sessions, session_messages
    from run.memory import MemoryStore

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
                        # 保持传输级别状态在最小/测试工作空间中有用
                        # 尚未包含全局配置。
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
    if command == "/memory":
        store = MemoryStore(root, user, load_config(user, root))
        items = store.list_items()
        if not items:
            print("暂无记忆。", file=stdout)
        for item in items:
            print(
                f"{item['filename']} | {item['tier']} | weight={item['weight']} | "
                f"{item['content']}",
                file=stdout,
            )
        return True, session_id
    if command == "/remember":
        if not argument:
            print("用法：/remember <内容>", file=stdout)
            return True, session_id
        store = MemoryStore(root, user, load_config(user, root))
        result = store.upsert_candidates(
            [{
                "content": argument,
                "explicit": True,
                "action": "upsert",
            }],
            source={"source": source, "session_id": session_id, "explicit": True},
        )
        if result["rejected"]:
            print("记忆内容为空或包含敏感凭据，未保存。", file=stdout)
        else:
            filename = (result["created"] or result["updated"])[0]
            print(f"已保存永久记忆：{filename}", file=stdout)
        return True, session_id
    if command == "/forget":
        if not argument:
            print("用法：/forget <记忆ID或关键词>", file=stdout)
            return True, session_id
        store = MemoryStore(root, user, load_config(user, root))
        removed = store.forget(argument)
        print(f"已删除 {len(removed)} 条记忆。", file=stdout)
        return True, session_id

        # --- 任务计划命令 ---
    if command == "/plans":
        from run.tasks import list_plans
        plans = list_plans(root, user)
        if not plans:
            print("暂无任务计划。", file=stdout)
        for item in plans:
            done = sum(1 for s in item.get("steps", []) if s.get("status") == "completed")
            total = len(item.get("steps", []))
            print(
                f"{item['plan_id']} | {item.get('status', '?')} | "
                f"{done}/{total} | {item.get('title', '')}",
                file=stdout,
            )
        return True, session_id
    if command == "/plan":
        if not argument:
            print("用法：/plan <目标描述>", file=stdout)
            return True, session_id
        from run.tasks import generate_plan, PlanGenerationError, PlanSkipped
        from run.tasks import PlanStore
        try:
            plan = generate_plan(
                root=root,
                user=user,
                goal=argument,
                source=source,
                session_id=session_id,
            )
        except PlanSkipped as exc:
            print(f"不需要创建计划：{exc}", file=stdout)
            return True, session_id
        except PlanGenerationError as exc:
            print(f"计划生成失败：{exc}", file=stdout)
            return True, session_id
        store = PlanStore(root, user)
        created = store.create(plan)
        print(
            f"已创建计划：{created['plan_id']} | {created['title']} | "
            f"{len(created['steps'])} 步 | 状态：{created['status']}",
            file=stdout,
        )
        for step in created["steps"]:
            deps = ", ".join(step.get("depends_on") or []) or "无"
            print(
                f"  {step['step_id']} | {step.get('tool_name', '无工具')} | "
                f"deps={deps} | {step['title']}",
                file=stdout,
        )
        if created.get("auto_accept"):
            print(
                "auto_accept 已开启，计划已批准；使用 /plan-approve 进入正式计划执行器。",
                file=stdout,
            )
        else:
            if created.get("reminder"):
                print(created["reminder"], file=stdout)
            print("使用 /plan-approve 批准执行。", file=stdout)
        return True, session_id
    if command == "/plan-show":
        if not argument:
            print("用法：/plan-show <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import get_plan
        try:
            plan = get_plan(root, user, argument)
        except Exception as exc:
            print(f"读取计划失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"{plan['plan_id']} | {plan['status']} | {plan['title']}",
            file=stdout,
        )
        print(f"描述：{plan.get('description', '')}", file=stdout)
        for step in plan["steps"]:
            deps = ", ".join(step.get("depends_on") or []) or "无"
            print(
                f"  {step['step_id']} | {step['status']} | "
                f"{step.get('tool_name', '无工具')} | deps={deps} | "
                f"{'关键' if step.get('critical') else '非关键'} | {step['title']}",
                file=stdout,
            )
            if step.get("error"):
                print(f"    错误：{step['error'].get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-approve":
        if not argument:
            print("用法：/plan-approve <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import approve_plan, execute_plan, get_plan
        from run.config import load_config
        try:
            current = get_plan(root, user, argument)
            plan = (
                current
                if current.get("status") == "approved"
                else approve_plan(root, user, argument)
            )
        except Exception as exc:
            print(f"批准失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"计划 {argument} 已批准，进入正式计划执行器...",
            file=stdout,
        )
        config = load_config(user, root)
        for event in execute_plan(
            root=root, user=user, plan_id=argument, config=config,
        ):
            if event.type == "tool_call_start":
                print(f"  [{event.metadata.get('step_id', '')}] 开始：{event.tool_name}", file=stdout)
            elif event.type == "tool_call_result":
                status = event.metadata.get("status", "?")
                print(f"  [{event.metadata.get('step_id', '')}] {status}", file=stdout)
            elif event.type == "done":
                status = event.metadata.get("status", "")
                if status:
                    print(f"计划 {argument} → {status}", file=stdout)
            elif event.type == "error":
                detail = event.error or {}
                print(f"  错误：{detail.get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-pause":
        if not argument:
            print("用法：/plan-pause <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import pause_plan
        try:
            pause_plan(root, user, argument)
            print(f"已暂停计划 {argument}。", file=stdout)
        except Exception as exc:
            print(f"暂停失败：{exc}", file=stdout)
        return True, session_id
    if command == "/plan-resume":
        if not argument:
            print("用法：/plan-resume <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import resume_plan, execute_plan
        from run.config import load_config
        try:
            resume_plan(root, user, argument)
        except Exception as exc:
            print(f"恢复失败：{exc}", file=stdout)
            return True, session_id
        print(f"已恢复计划 {argument}，继续执行...", file=stdout)
        config = load_config(user, root)
        for event in execute_plan(
            root=root, user=user, plan_id=argument, config=config,
        ):
            if event.type == "tool_call_start":
                print(f"  [{event.metadata.get('step_id', '')}] 开始：{event.tool_name}", file=stdout)
            elif event.type == "tool_call_result":
                status = event.metadata.get("status", "?")
                print(f"  [{event.metadata.get('step_id', '')}] {status}", file=stdout)
            elif event.type == "done":
                status = event.metadata.get("status", "")
                if status:
                    print(f"计划 {argument} → {status}", file=stdout)
            elif event.type == "error":
                detail = event.error or {}
                print(f"  错误：{detail.get('message', '')}", file=stdout)
        return True, session_id
    if command == "/plan-cancel":
        if not argument:
            print("用法：/plan-cancel <计划ID>", file=stdout)
            return True, session_id
        from run.tasks import cancel_plan
        try:
            cancel_plan(root, user, argument)
            print(f"已取消计划 {argument}。", file=stdout)
        except Exception as exc:
            print(f"取消失败：{exc}", file=stdout)
        return True, session_id

        # --- cron 命令 ---
    if command == "/crons":
        from run.scheduler import CronStore
        store = CronStore(root, user)
        tasks = store.list_tasks()
        if not tasks:
            print("暂无定时任务。", file=stdout)
        for item in tasks:
            print(
                f"{item['task_id']} | {item.get('status', '?')} | "
                f"{item.get('type', '?')} | "
                f"next={item.get('next_run_at', '')} | {item.get('title', '')}",
                file=stdout,
            )
        return True, session_id
    if command == "/cron":
        if not argument:
            print("用法：/cron <自然语言定时要求>", file=stdout)
            return True, session_id
        from cron.service import generate_cron_task, CronGenerationError, CronSkipped
        from run.scheduler import CronStore
        try:
            task = generate_cron_task(
                root=root, user=user, user_request=argument,
                source=source, session_id=session_id,
            )
        except CronSkipped as exc:
            print(f"不需要创建定时任务：{exc}", file=stdout)
            return True, session_id
        except CronGenerationError as exc:
            print(f"定时任务生成失败：{exc}", file=stdout)
            return True, session_id
        store = CronStore(root, user)
        created = store.create(task)
        print(
            f"已创建定时任务：{created['task_id']} | {created['title']} | "
            f"{created['type']} | next={created['next_run_at']}",
            file=stdout,
        )
        return True, session_id
    if command == "/cron-show":
        if not argument:
            print("用法：/cron-show <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            task = store.read(argument)
        except Exception as exc:
            print(f"读取任务失败：{exc}", file=stdout)
            return True, session_id
        print(
            f"{task['task_id']} | {task['status']} | {task['title']}",
            file=stdout,
        )
        detail = task.get("time") if task.get("type") == "daily" else task.get("interval_seconds", "")
        print(f"调度：{task.get('type', '')} {detail}", file=stdout)
        print(f"下一次执行：{task.get('next_run_at', '')}", file=stdout)
        print(f"最近执行：{task.get('latest_run_at', '')}", file=stdout)
        return True, session_id
    if command == "/cron-pause":
        if not argument:
            print("用法：/cron-pause <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            store.update(argument, lambda t: {**t, "status": "paused"})
            print(f"已暂停定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"暂停失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-resume":
        if not argument:
            print("用法：/cron-resume <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        from cron.schedule import compute_next_run
        store = CronStore(root, user)
        def _resume(t):
            t["status"] = "enabled"
            t["next_run_at"] = compute_next_run(t)
            return t
        try:
            store.update(argument, _resume)
            print(f"已恢复定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"恢复失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-cancel":
        if not argument:
            print("用法：/cron-cancel <任务ID>", file=stdout)
            return True, session_id
        from run.scheduler import CronStore
        store = CronStore(root, user)
        try:
            store.update(argument, lambda t: {**t, "status": "cancelled"})
            print(f"已取消定时任务 {argument}。", file=stdout)
        except Exception as exc:
            print(f"取消失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-run":
        if not argument:
            print("用法：/cron-run <任务ID>", file=stdout)
            return True, session_id
        from cron.executor import execute_cron_task
        from run.config import load_config
        config = load_config(user, root)
        print(f"立即执行定时任务 {argument}...", file=stdout)
        try:
            result = execute_cron_task(
                root=root, user=user, task_id=argument, config=config,
            )
            print(f"完成：{result.get('status', '?')}", file=stdout)
        except Exception as exc:
            print(f"执行失败：{exc}", file=stdout)
        return True, session_id
    if command == "/cron-start":
        from cron.scheduler import CronScheduler
                # 使用模块级单例
        if not hasattr(run_interactive, '_cron_scheduler'):
            run_interactive._cron_scheduler = CronScheduler(root)
        sched = run_interactive._cron_scheduler
        if sched.running:
            print("调度器已在运行。", file=stdout)
        else:
            sched.start()
            print("调度器已启动。", file=stdout)
        return True, session_id
    if command == "/cron-stop":
        if hasattr(run_interactive, '_cron_scheduler'):
            sched = run_interactive._cron_scheduler
            sched.stop()
            print("调度器已停止。", file=stdout)
        else:
            print("调度器未运行。", file=stdout)
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

        if prompt is None:
            run_interactive(
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
