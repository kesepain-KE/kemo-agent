"""Event-driven kemo-agent conversation engine."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from events import RunEvent, error_event
from provider.factory import create_provider
from provider.schema import ChatRequest, ChatResponse, ToolCall, Usage
from run.config import load_config, project_root, provider_runtime_config
from run.history import commit_window, prepare_window
from run.prompt import build_system_prompt
from run.tools import ToolError, ToolRegistry, discover_tools, execute_tool


class EngineError(RuntimeError):
    """The run core rejected or failed a conversation request."""


_SESSION_LOCKS: dict[tuple[str, str, str, str], threading.RLock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()


def _session_lock(root: Path, user: str, source: str, session_id: str) -> threading.RLock:
    key = (str(root.resolve()), user, source, session_id)
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(key, threading.RLock())


def _required_text(request: dict[str, Any], name: str) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value.strip():
        raise EngineError(f"请求字段 {name!r} 必须是非空字符串")
    return value.strip()


def _usage_from_dict(value: dict[str, Any] | None) -> Usage:
    raw = value or {}
    known = {"prompt_tokens", "completion_tokens", "total_tokens", "estimated", "source"}
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens") or 0),
        completion_tokens=int(raw.get("completion_tokens") or 0),
        total_tokens=int(raw.get("total_tokens") or 0),
        estimated=bool(raw.get("estimated", False)),
        source=str(raw.get("source") or "provider"),
        extra={key: item for key, item in raw.items() if key not in known},
    )


def _merge_usage(total: dict[str, Any], usage: Usage) -> None:
    total["prompt_tokens"] = int(total.get("prompt_tokens", 0)) + usage.prompt_tokens
    total["completion_tokens"] = int(total.get("completion_tokens", 0)) + usage.completion_tokens
    total["total_tokens"] = int(total.get("total_tokens", 0)) + usage.total_tokens
    total["estimated"] = bool(total.get("estimated", False) or usage.estimated)


def _usage_total() -> dict[str, Any]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "estimated": False,
    }


def _history_messages(window: dict[str, Any]) -> list[dict[str, Any]]:
    messages = window["text"].get("messages", [])
    return [
        dict(message)
        for message in messages
        if isinstance(message, dict)
        and message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
    ]


def _events_for_response(response: ChatResponse) -> Iterator[RunEvent]:
    if response.reasoning:
        yield RunEvent(type="reasoning_delta", content=response.reasoning)
    if response.text:
        yield RunEvent(type="text_delta", content=response.text)
    for call in response.tool_calls:
        yield RunEvent(
            type="tool_call_start",
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
        )
    usage = response.usage.to_dict()
    yield RunEvent(type="usage", usage=usage)
    yield RunEvent(
        type="done",
        usage=usage,
        metadata={
            "finish_reason": response.finish_reason,
            "model": response.model,
            "response_id": response.response_id,
        },
    )


def _provider_events(provider: Any, request: ChatRequest) -> Iterator[RunEvent]:
    if request.stream:
        yield from provider.chat_stream(request)
    else:
        yield from _events_for_response(provider.chat(request))


def _assistant_tool_message(text: str, calls: list[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in calls
        ],
    }


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def iter_request_events(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run one complete model/tool loop, committing only on successful done."""

    try:
        user = _required_text(request, "user")
        prompt = _required_text(request, "prompt")
        source = _required_text(request, "source")
        session_id = _required_text(request, "session_id")
        base = (root or project_root()).resolve()
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        yield error_event(exc, phase="request")
        return

    with _session_lock(base, user, source, session_id):
        try:
            config = load_config(user, base)
            runtime_provider = provider_runtime_config(config)
            provider = provider_factory(runtime_provider)
            window_path, window, _ = prepare_window(base, user, source, session_id)
            tool_config = config.get("tools") or {}
            tools_enabled = bool(tool_config.get("enabled", True))
            registry = tool_registry_factory(base, user) if tools_enabled else ToolRegistry({})
            tool_schemas = registry.schemas() or None
            tool_timeout = float(tool_config.get("timeout", 60))
            max_iterations = max(1, int(tool_config.get("max_iterations", 8)))

            messages: list[dict[str, Any]] = []
            system_prompt = build_system_prompt(base, user, config)
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(_history_messages(window))
            messages.append({"role": "user", "content": prompt})

            stream = bool(request.get("stream", runtime_provider.get("stream", False)))
            all_text: list[str] = []
            all_reasoning: list[str] = []
            tool_records: list[dict[str, Any]] = []
            usage_total = _usage_total()
            seen_calls: dict[str, dict[str, Any]] = {}
            final_metadata: dict[str, Any] = {}
            completed = False

            for iteration in range(1, max_iterations + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return
                chat_request = ChatRequest(
                    model=runtime_provider["model"],
                    messages=messages,
                    stream=stream,
                    tools=tool_schemas,
                )
                iteration_text: list[str] = []
                iteration_reasoning: list[str] = []
                calls: list[ToolCall] = []
                iteration_done: RunEvent | None = None
                iteration_usage: Usage | None = None

                for event in _provider_events(provider, chat_request):
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    if event.type == "text_delta":
                        iteration_text.append(event.content)
                        all_text.append(event.content)
                        yield event
                    elif event.type == "reasoning_delta":
                        iteration_reasoning.append(event.content)
                        all_reasoning.append(event.content)
                        yield event
                    elif event.type == "tool_call_start":
                        calls.append(
                            ToolCall(
                                id=event.tool_call_id,
                                name=event.tool_name,
                                arguments=event.arguments or {},
                            )
                        )
                        yield event
                    elif event.type == "usage":
                        iteration_usage = _usage_from_dict(event.usage)
                        yield RunEvent(
                            type="usage",
                            usage=event.usage,
                            metadata={"iteration": iteration},
                        )
                    elif event.type == "error":
                        yield event
                        return
                    elif event.type == "done":
                        iteration_done = event

                if iteration_done is None:
                    yield error_event(EngineError("Provider 事件流缺少 done 终态"), phase="provider")
                    return
                if iteration_usage is None:
                    iteration_usage = _usage_from_dict(iteration_done.usage)
                _merge_usage(usage_total, iteration_usage)
                final_metadata = dict(iteration_done.metadata)

                if not calls:
                    completed = True
                    break
                if iteration >= max_iterations:
                    yield error_event(
                        EngineError(f"工具调用超过最大循环次数 {max_iterations}"),
                        phase="tool_loop",
                    )
                    return

                assistant_text = "".join(iteration_text)
                messages.append(_assistant_tool_message(assistant_text, calls))
                for call in calls:
                    if cancel_event is not None and cancel_event.is_set():
                        return
                    signature = f"{call.name}:{json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)}"
                    duplicate = signature in seen_calls
                    if duplicate:
                        result_payload = seen_calls[signature]
                        status = "duplicate_reused"
                    else:
                        try:
                            definition = registry.get(call.name)
                            result = execute_tool(
                                definition,
                                call.arguments,
                                context={
                                    "root": str(base),
                                    "user": user,
                                    "source": source,
                                    "session_id": session_id,
                                    "window": window_path.name,
                                },
                                timeout=tool_timeout,
                                cancel_event=cancel_event,
                            )
                            result_payload = {"ok": True, "result": result}
                            status = "completed"
                        except BaseException as exc:
                            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                                raise
                            result_payload = {
                                "ok": False,
                                "error": {
                                    "message": str(exc),
                                    "exception_type": type(exc).__name__,
                                },
                            }
                            status = "failed"
                        seen_calls[signature] = result_payload
                    record = {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": status,
                        "duplicate": duplicate,
                        "result": result_payload,
                        "iteration": iteration,
                    }
                    tool_records.append(record)
                    yield RunEvent(
                        type="tool_call_result",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        result=result_payload,
                        metadata={"status": status, "duplicate": duplicate, "iteration": iteration},
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": _json_result(result_payload),
                        }
                    )

            if not completed:
                yield error_event(EngineError("模型工具循环未完成"), phase="tool_loop")
                return
            if cancel_event is not None and cancel_event.is_set():
                return

            round_number = int(window["data"].get("rounds", 0)) + 1
            text = "".join(all_text)
            reasoning = "".join(all_reasoning)
            window["text"]["messages"].extend(
                [
                    {"role": "user", "content": prompt},
                    {"role": "assistant", "content": text},
                ]
            )
            window["think"]["rounds"].append({"round": round_number, "content": reasoning})
            window["tool"]["rounds"].append({"round": round_number, "calls": tool_records})
            window["data"]["rounds"] = round_number
            _merge_usage(window["data"]["token_usage"], _usage_from_dict(usage_total))
            commit_window(window_path, window)

            final_metadata.update(
                {
                    "text": text,
                    "reasoning": reasoning,
                    "usage": usage_total,
                    "model": final_metadata.get("model") or runtime_provider["model"],
                    "user": user,
                    "source": source,
                    "session_id": session_id,
                    "window": window_path.name,
                    "tool_calls": len(tool_records),
                    "committed": True,
                }
            )
            yield RunEvent(type="done", usage=usage_total, metadata=final_metadata)
        except (KeyboardInterrupt, GeneratorExit):
            raise
        except BaseException as exc:
            yield error_event(exc, phase="run")


async def stream_request(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> AsyncIterator[RunEvent]:
    stopped = cancel_event or threading.Event()
    iterator = iter_request_events(
        request,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=stopped,
    )
    try:
        while True:
            event = await asyncio.to_thread(next, iterator, None)
            if event is None:
                break
            yield event
    except asyncio.CancelledError:
        stopped.set()
        await asyncio.to_thread(iterator.close)
        raise


def handle_request(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
) -> dict[str, Any]:
    final: RunEvent | None = None
    for event in iter_request_events(
        request,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
    ):
        if event.type == "error":
            detail = event.error or {}
            raise EngineError(str(detail.get("message") or "运行失败"))
        if event.type == "done":
            final = event
    if final is None:
        raise EngineError("运行在完成前被取消")
    return dict(final.metadata)
