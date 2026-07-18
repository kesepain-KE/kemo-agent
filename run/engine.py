"""事件驱动的 kemo-agent 对话引擎。"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterator

from events import RunEvent, error_event
from provider.factory import create_provider
from provider.schema import ChatRequest, ChatResponse, ToolCall, Usage
from run.agent_runner import AgentRunner
from run.config import load_config, project_root, provider_runtime_config
from run.context import ContextPolicy, estimate_messages_tokens, estimate_tools_tokens, select_context
from run.context_summary import build_summary_message, get_or_create_summary
from run.history import commit_window, prepare_window
from run.memory import MemoryStore
from run.memory_pipeline import submit_memory_extraction
from run.prompt import PromptBundle, build_prompt_bundle
from run.source_policy import MainAgentSourcePolicy
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


def _drain_guidance(channel: Any) -> list[str]:
    if channel is None or not callable(getattr(channel, "get_nowait", None)):
        return []
    values: list[str] = []
    while True:
        try:
            value = channel.get_nowait()
        except queue.Empty:
            break
        if isinstance(value, str) and value.strip():
            values.append(value.strip())
    return values


def _append_guidance(messages: list[dict[str, Any]], values: list[str]) -> None:
    if values:
        messages.append(
            {
                "role": "user",
                "content": "[运行中引导]\n" + "\n".join(f"- {item}" for item in values),
            }
        )


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
    cached: int | None = None
    for key in ("cached_prompt_tokens", "cache_hit_tokens", "cached_tokens"):
        value = usage.extra.get(key)
        if value is not None:
            cached = max(0, int(value))
            break
    details = usage.extra.get("prompt_tokens_details")
    if cached is None and isinstance(details, dict) and details.get("cached_tokens") is not None:
        cached = max(0, int(details["cached_tokens"]))
    missed_raw = usage.extra.get("cache_miss_tokens")
    missed = max(0, int(missed_raw)) if missed_raw is not None else None
    if cached is not None:
        missed = max(0, usage.prompt_tokens - cached) if missed is None else missed
        total["cached_prompt_tokens"] = int(total.get("cached_prompt_tokens", 0)) + cached
        total["cache_miss_tokens"] = int(total.get("cache_miss_tokens", 0)) + missed
        denominator = total["cached_prompt_tokens"] + total["cache_miss_tokens"]
        total["cache_hit_rate"] = (
            round(total["cached_prompt_tokens"] / denominator, 6) if denominator else 0.0
        )


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


def _ensure_fixed_content_fits(
    selection: Any,
    *,
    system_message: dict[str, Any] | None,
) -> None:
    if not selection.fixed_content_over_budget:
        return
    system_tokens = estimate_messages_tokens([system_message]) if system_message else 0
    raise EngineError(
        "固定提示词和工具定义超过输入预算："
        f"system_prompt≈{system_tokens} tokens，"
        f"tool_schema≈{selection.tool_schema_tokens} tokens，"
        f"input_budget={selection.input_budget} tokens；"
        "请调小 prompt.file_limits 或 prompt.char_limits"
    )


def _memory_injected_chars(bundle: PromptBundle) -> int:
    return sum(
        section.injected_chars
        for section in bundle.sections
        if section.name == "permanent_memory" or section.name.startswith("temporary_memory:")
    )


def iter_request_events(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
) -> Iterator[RunEvent]:
    """Run one complete model/tool loop, committing only on successful done."""

    run_started = time.monotonic()
    try:
        user = _required_text(request, "user")
        prompt_value = request.get("prompt", "")
        prompt = prompt_value.strip() if isinstance(prompt_value, str) else ""
        if not prompt and not bool(request.get("compress_only", False)):
            raise EngineError("请求字段 'prompt' 必须是非空字符串")
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
            source_policy = MainAgentSourcePolicy.from_config(config)
            runtime_provider = provider_runtime_config(config)
            provider = provider_factory(runtime_provider)
            agent_runner = AgentRunner(
                base,
                user,
                config=config,
                provider_factory=provider_factory,
            )
            window_path, window, _ = prepare_window(base, user, source, session_id)
            tool_config = config.get("tools") or {}
            tools_enabled = bool(tool_config.get("enabled", True))
            registry = tool_registry_factory(base, user) if tools_enabled else ToolRegistry({})
            tool_schemas = registry.schemas() or None
            tool_timeout = float(tool_config.get("timeout", 60))
            max_iterations = max(1, int(tool_config.get("max_iterations", 8)))

            memory_config = config.get("memory") or {}
            memory_store = MemoryStore(base, user, config)
            memory_store.review_due()
            prompt_bundle = build_prompt_bundle(
                base,
                user,
                config,
                plugin_manifests=registry.plugin_manifests,
                memory_store=memory_store,
            )
            system_message = (
                {"role": "system", "content": prompt_bundle.text}
                if prompt_bundle.text
                else None
            )
            compress_only = bool(request.get("compress_only", False))
            current_user_message = (
                None if compress_only else {"role": "user", "content": prompt}
            )
            context_policy = ContextPolicy.from_config(config)
            force_compress = bool(request.get("compress", False) or compress_only)
            context_selection = select_context(
                window=window,
                policy=context_policy,
                system_message=system_message,
                current_user_message=current_user_message,
                tools=tool_schemas,
                force_compress=force_compress,
            )
            _ensure_fixed_content_fits(context_selection, system_message=system_message)
            summary_usage = _usage_total()
            subagent_events: list[RunEvent] = []
            summary_cache = None
            summary_diagnostics: dict[str, Any] = {
                "cache_hit": False,
                "generated": False,
                "failed": False,
                "covered_rounds": [],
            }
                        # 摘要也消耗输入标记。  重新选择，直至设置完毕
                        # 移除的整轮是稳定的，因此没有一轮可以被取代
                        # 摘要，但也未包含在该摘要中。
            max_summary_passes = len(context_selection.all_rounds) + 1
            for _ in range(max_summary_passes):
                removed_before = [item.number for item in context_selection.removed_rounds]
                if not removed_before:
                    break
                summary_agent = (
                    "token_condense"
                    if context_selection.token_limit_triggered
                    else "context_manage"
                )
                summary_trigger = (
                    "token_limit"
                    if context_selection.token_limit_triggered
                    else ("manual" if force_compress else "round_limit")
                )
                summary_cache, summary_diagnostics = get_or_create_summary(
                    cache_path=window_path / "context_summary.json",
                    groups=context_selection.removed_rounds,
                    agent_runner=agent_runner,
                    agent_name=summary_agent,
                    trigger=summary_trigger,
                    cancel_event=cancel_event,
                    chunk_token_budget=max(256, context_policy.input_budget // 2),
                    max_tokens=min(4096, max(256, context_policy.output_reserve)),
                    response_hook=lambda raw: _merge_usage(
                        summary_usage, _usage_from_dict(raw)
                    ),
                    event_callback=subagent_events.append,
                )
                next_selection = select_context(
                    window=window,
                    policy=context_policy,
                    system_message=system_message,
                    summary_message=build_summary_message(summary_cache),
                    current_user_message=current_user_message,
                    tools=tool_schemas,
                    force_compress=force_compress,
                )
                removed_after = [item.number for item in next_selection.removed_rounds]
                context_selection = next_selection
                _ensure_fixed_content_fits(context_selection, system_message=system_message)
                if removed_after == removed_before:
                    break
            if cancel_event is not None and cancel_event.is_set():
                return
            messages = context_selection.messages
            context_stats = context_selection.stats()
            context_stats["summary"] = summary_diagnostics
            context_stats["summary_usage"] = summary_usage
            for subagent_event in subagent_events:
                yield subagent_event
            if compress_only:
                yield RunEvent(
                    type="done",
                    usage=dict(summary_usage),
                    metadata={
                        "text": "",
                        "reasoning": "",
                        "usage": dict(summary_usage),
                        "model": runtime_provider["model"],
                        "user": user,
                        "source": source,
                        "session_id": session_id,
                        "window": window_path.name,
                        "context": context_stats,
                        "prompt": prompt_bundle.diagnostics,
                        "summary_cache": (
                            str(window_path / "context_summary.json")
                            if summary_cache is not None
                            else None
                        ),
                        "compressed": True,
                        "committed": False,
                    },
                )
                return

            stream = bool(request.get("stream", runtime_provider.get("stream", False)))
            all_text: list[str] = []
            all_reasoning: list[str] = []
            tool_records: list[dict[str, Any]] = []
            guidance_channel = request.get("_guidance_queue")
            consumed_guidance: list[str] = []
            run_id = str(request.get("run_id") or "")
            usage_total = dict(summary_usage)
            if summary_usage.get("total_tokens", 0):
                yield RunEvent(
                    type="usage",
                    usage=dict(summary_usage),
                    metadata={"phase": "context_summary"},
                )
            seen_calls: dict[str, dict[str, Any]] = {}
            final_metadata: dict[str, Any] = {}
            completed = False

            for iteration in range(1, max_iterations + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return
                if iteration > 1:
                    current_tokens = estimate_messages_tokens(messages) + estimate_tools_tokens(tool_schemas)
                    if current_tokens > context_policy.token_limit:
                        yield error_event(
                            EngineError(
                                "当前工具循环已超过上下文上限；为避免拆散工具消息组，本轮已停止"
                            ),
                            phase="context",
                        )
                        return
                configured_max_tokens = runtime_provider.get("max_tokens")
                request_max_tokens = (
                    min(
                        context_policy.output_reserve,
                        max(1, int(configured_max_tokens)),
                    )
                    if configured_max_tokens is not None
                    else None
                )
                chat_request = ChatRequest(
                    model=runtime_provider["model"],
                    messages=messages,
                    stream=stream,
                    tools=tool_schemas,
                    max_tokens=request_max_tokens,
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
                    pending_guidance = _drain_guidance(guidance_channel)
                    if pending_guidance and iteration < max_iterations:
                        messages.append(
                            {"role": "assistant", "content": "".join(iteration_text)}
                        )
                        _append_guidance(messages, pending_guidance)
                        consumed_guidance.extend(pending_guidance)
                        all_text.append("\n\n")
                        yield RunEvent(type="text_delta", content="\n\n")
                        continue
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
                    tool_started = time.monotonic()
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
                                    "tool_timeout": tool_timeout,
                                    "knowledge_enabled": source_policy.knowledge_enabled,
                                    "knowledge_scopes": list(source_policy.knowledge_scopes),
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
                    elapsed_ms = max(0, round((time.monotonic() - tool_started) * 1000))
                    record = {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "status": status,
                        "duplicate": duplicate,
                        "result": result_payload,
                        "iteration": iteration,
                        "elapsed_ms": elapsed_ms,
                    }
                    tool_records.append(record)
                    yield RunEvent(
                        type="tool_call_result",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        result=result_payload,
                        metadata={
                            "status": status,
                            "duplicate": duplicate,
                            "iteration": iteration,
                            "elapsed_ms": elapsed_ms,
                        },
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": _json_result(result_payload),
                        }
                    )
                pending_guidance = _drain_guidance(guidance_channel)
                _append_guidance(messages, pending_guidance)
                consumed_guidance.extend(pending_guidance)

            if not completed:
                yield error_event(EngineError("模型工具循环未完成"), phase="tool_loop")
                return
            if cancel_event is not None and cancel_event.is_set():
                return

            round_number = int(window["data"].get("rounds", 0)) + 1
            round_elapsed_ms = max(0, round((time.monotonic() - run_started) * 1000))
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
            round_metrics = window["data"].setdefault("round_metrics", [])
            if not isinstance(round_metrics, list):
                round_metrics = []
                window["data"]["round_metrics"] = round_metrics
            round_metrics.append(
                {
                    "round": round_number,
                    "usage": dict(usage_total),
                    "elapsed_ms": round_elapsed_ms,
                    "tool_calls": len(tool_records),
                    "guidance": list(consumed_guidance),
                }
            )
            window["data"]["context"] = {
                **context_stats,
                "summary_cache": (
                    "context_summary.json" if summary_cache is not None else None
                ),
            }
            _merge_usage(window["data"]["token_usage"], _usage_from_dict(usage_total))
            commit_window(window_path, window)

                        # 仅对选择并实际发送到的记忆进行加权
                        # 主模型运行成功。  取消/失败的回合永远不会得到
                        # 在这里，仅仅检索候选者被有意排除。
            memory_weighted_ids: list[str] = []
            memory_weight_error = None
            try:
                memory_weighted_ids = memory_store.mark_used(list(prompt_bundle.memory_ids))
            except Exception as exc:
                memory_weight_error = {
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                }
            memory_task_id = None
            memory_error = None
            if bool(memory_config.get("extraction_enabled", False)):
                try:
                    memory_task_id = submit_memory_extraction(
                        root=base,
                        user=user,
                        config=config,
                        user_text=prompt,
                        assistant_text=text,
                        tool_results=tool_records,
                        source={
                            "source": source,
                            "session_id": session_id,
                            "window": window_path.name,
                            "round": round_number,
                        },
                        provider_factory=provider_factory,
                    )
                except Exception as exc:
                                        # 内存是异步衍生的副作用。  主要
                                        # 四文件历史事务已提交并且
                                        # 不得因队列/子代理故障而回滚。
                    memory_error = {
                        "message": str(exc),
                        "exception_type": type(exc).__name__,
                    }

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
                    "elapsed_ms": round_elapsed_ms,
                    "run_id": run_id,
                    "guidance_count": len(consumed_guidance),
                    "context": context_stats,
                    "prompt": prompt_bundle.diagnostics,
                    "memory": {
                        "injected_ids": list(prompt_bundle.memory_ids),
                        "weighted_ids": memory_weighted_ids,
                        "weight_error": memory_weight_error,
                        "injected_chars": _memory_injected_chars(prompt_bundle),
                        "extraction_task_id": memory_task_id,
                        "extraction_error": memory_error,
                    },
                    "knowledge": {
                        "documents": prompt_bundle.diagnostics["knowledge_documents"],
                        "injected_chars": prompt_bundle.diagnostics["sections"]
                        .get("knowledge_index", {})
                        .get("injected_chars", 0),
                    },
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


def context_status(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
) -> dict[str, Any]:
    user = _required_text(request, "user")
    source = _required_text(request, "source")
    session_id = _required_text(request, "session_id")
    base = (root or project_root()).resolve()
    config = load_config(user, base)
    window_path, window, is_new = prepare_window(base, user, source, session_id)
    tool_config = config.get("tools") or {}
    registry = (
        tool_registry_factory(base, user)
        if bool(tool_config.get("enabled", True))
        else ToolRegistry({})
    )
    memory_store = MemoryStore(base, user, config)
    prompt_bundle = build_prompt_bundle(
        base,
        user,
        config,
        plugin_manifests=registry.plugin_manifests,
        memory_store=memory_store,
    )
    policy = ContextPolicy.from_config(config)
    selection = select_context(
        window=window,
        policy=policy,
        system_message=(
            {"role": "system", "content": prompt_bundle.text}
            if prompt_bundle.text
            else None
        ),
        current_user_message=None,
        tools=registry.schemas() or None,
    )
    cache_path = window_path / "context_summary.json"
    persisted = window.get("data", {}).get("context")
    return {
        "user": user,
        "source": source,
        "session_id": session_id,
        "window": None if is_new else window_path.name,
        "rounds": int(window.get("data", {}).get("rounds", 0)),
        "context": selection.stats(),
        "prompt": prompt_bundle.diagnostics,
        "last_committed_context": persisted if isinstance(persisted, dict) else None,
        "summary_cache_exists": cache_path.is_file(),
        "policy": {
            "recent_tool_rounds": policy.recent_tool_rounds,
            "max_rounds": policy.max_rounds,
            "rounds_after_compression": policy.rounds_after_compression,
            "token_limit": policy.token_limit,
            "compression_ratio": policy.compression_ratio,
            "input_budget": policy.input_budget,
            "output_reserve": policy.output_reserve,
        },
    }


def compress_context(
    request: dict[str, Any],
    *,
    root: Path | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
) -> dict[str, Any]:
    payload = dict(request)
    payload["prompt"] = ""
    payload["compress_only"] = True
    payload["compress"] = True
    return handle_request(
        payload,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
    )
