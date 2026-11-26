"""Per-segment budgets, safe-point reminders and bounded public diagnostics."""
from __future__ import annotations

import copy
import math
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace

from provider.protocol.enums import IncompleteReason, MessageRole, ResponseStatus
from provider.protocol.models import KemoResponse, MessageItem
from provider.retry import RetryCancelled, RetryManager
from provider.token_usage import complete_usage
from run.agents.contracts import AgentCancelledError, AgentProviderError, AgentRunError, AgentToolLimitError

BUDGET_STOPS = {"timed_out", "tool_limit"}


def _summary_terminal_error(response):
    if response.status == ResponseStatus.CANCELLED:
        return AgentCancelledError("Provider 已取消子代理摘要请求")
    if response.incomplete_details and response.incomplete_details.reason == IncompleteReason.OUTPUT_TRUNCATED:
        return None  # ProviderSummarizer owns bounded output-truncation recovery.
    if response.error is not None:
        from run.conversation import ProviderResponseError, response_terminal_error
        return ProviderResponseError(response_terminal_error(response))
    return None


def validate_input_budget(context, system, tools, tail, *, provider, runtime, capabilities,
                          configured_limit, input_limit, usage, retry_state,
                          force_compact=False, skip_preventative=False):
    """Prepare the exact native request, with a read-only summary when needed."""
    from run.cards import CardCapacityError, CardCompactionError, ProviderSummarizer, prepare_request
    from run.context import CompressionCancelledError
    from run.infra import merge_usage_dict
    from run.tools import limit_tool_result_items

    stream = context.card_stream
    store = stream.store
    config = context.runner.config
    settings = config.get("agents") or {}
    fresh_input = stream.head <= max(stream.initial_seq, stream.retry_after)
    tool_context = {"root": str(store.root), "user": store.user, "session_id": context.session_id}

    def check_cancel():
        if context.cancel_event.is_set():
            raise AgentCancelledError(f"子代理 {context.definition.name} 已取消")

    def project(request):
        visible = stream.visible_history(request.history)
        items = stream.request_items(replace(request, history=visible))
        items.extend(tail)
        limit_tool_result_items(items, tool_context)
        return items, visible.history_tokens

    def account(summary_usage):
        if not summary_usage or not summary_usage.get("provider_request_count"):
            return
        merge_usage_dict(usage, summary_usage)
        for key in ("input_tokens", "output_tokens", "provider_request_count"):
            count = summary_usage.get(key, 0)
            if count:
                name = "compression_" + key
                usage[name] = usage.get(name, 0) + count
        retry_state.usage = copy.deepcopy(usage)
        with context.continuation.lock:
            context.continuation.payload["usage"] = copy.deepcopy(usage)

    def summarize(batch):
        check_cancel()
        if fresh_input:
            raise CardCapacityError("首次交付的任务输入不能用摘要替代")
        if stream.retry_after:
            # Same-attempt recovery cannot summarize history intentionally
            # hidden from this attempt's business request.
            visible = stream.visible_history(batch.snapshot)
            batch = replace(batch, snapshot=visible, cards=tuple(
                card for card in visible.cards if card.seq <= batch.cards[-1].seq))
        summarizer = ProviderSummarizer(
            provider, config, model=runtime["model"], system_prompt=system,
            tools=tools, input_limit=input_limit, cancel_event=context.cancel_event,
            timeout=float(settings.get("compression_timeout_seconds", 180)), meter=store.meter,
            capabilities=capabilities, configured_token_limit=configured_limit,
            calibrate=store.calibrated_tokens, terminal_error=_summary_terminal_error,
            retry=RetryManager(max_retries=(config.get("provider_runtime") or {}).get("max_retries", 5)),
        )
        try:
            candidate = summarizer(batch)
        except Exception as error:
            account(getattr(error, "compression_usage", None))
            if failed_usage := getattr(error, "failed_usage", None):
                account({"provider_request_count": 1, **failed_usage})
            raise
        account(candidate.usage)
        check_cancel()
        return candidate

    check_cancel()
    try:
        prepared = prepare_request(
            store, "subagent", stream.task_id, system_prompt=system, dynamic_tail=dict,
            tools=[tool.model_dump(mode="json", exclude_none=True) for tool in tools],
            input_limit=input_limit, summarize=summarize,
            ratio=float(settings.get("token_compression_ratio", .3)),
            model=runtime["model"], transport=runtime["type"], project=project,
            check_cancel=check_cancel, force_compact=force_compact,
            skip_preventative=skip_preventative or fresh_input,
        )
    except (CompressionCancelledError, RetryCancelled) as error:
        raise AgentCancelledError(
            "子代理摘要已请求取消", process_terminated=not getattr(error, "still_running", False),
            completion_future=getattr(error, "completion_future", None),
        ) from error
    except (CardCapacityError, CardCompactionError) as error:
        raise AgentProviderError(
            f"子代理完整请求超过输入预算：{error}", category="context_length_exceeded",
            code="summary_input_budget_exceeded", retryable=False,
        ) from error
    check_cancel()
    if prepared.compaction is not None:
        stream.prune_reasoning(prepared.snapshot.history)
    return prepared


def legacy_tool_stop(record):
    return (record.get("status") == "failed"
            and (record.get("error") or {}).get("exception_type") == "AgentToolLimitError"
            and not record.get("execution_live")
            and (record.get("error") or {}).get("process_terminated") is not False)


def upgrade_legacy_budget(restored):
    if restored.get("budget"):
        return
    from run.agents.continuation import decode_legacy_items
    from provider.protocol.models import ToolResultItem, JsonContent
    count = sum(1 for item in decode_legacy_items(restored)
                if isinstance(item, ToolResultItem) and item.name != "submit_structured_output"
                and not any(isinstance(c, JsonContent) and isinstance(c.data, dict)
                            and c.data.get("not_executed") for c in item.content))
    restored["budget"] = {"total_tool_calls": count}


def overridable(definition):
    return (definition.executor == "builtin:llm"
            and definition.execution != "background_serial"
            and definition.capabilities.exposure == "tool"
            and "main_agent" in definition.capabilities.allowed_callers)


def effective_limit(definition, requested=None):
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
            raise AgentRunError("max_tool_calls 必须是非负整数；0 表示不限工具次数")
        if not overridable(definition):
            raise AgentRunError("此子代理不支持覆盖工具预算")
        return requested
    return definition.capabilities.max_tool_iterations


def requested_timeout(value, *, field="子代理 timeout") -> float | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        try:
            seconds = float(value)
        except (TypeError, ValueError, OverflowError):
            pass
        else:
            if math.isfinite(seconds) and seconds >= 0:
                return seconds
    raise AgentRunError(f"{field} 必须是非负有限数")


def execution_timeout(definition, value, *, managed: bool) -> float | None:
    timeout = requested_timeout(value)
    if managed and definition.executor == "builtin:llm":
        return None
    # A compatibility zero cannot turn a Python or unowned job into an
    # unlimited one. Direct runner calls still require a positive override.
    if timeout == 0:
        if not managed:
            raise AgentRunError("此执行器的 timeout 必须是正数")
        timeout = None
    seconds = requested_timeout(definition.timeout if timeout is None else timeout)
    if not seconds:
        raise AgentRunError("子代理 timeout 必须是正数")
    return seconds


class SegmentBudget:
    def __init__(self, definition, requested, timeout: float | None, resume=None):
        self.limit = effective_limit(definition, requested)
        self.timeout = timeout
        self.started = time.monotonic()
        self._deadline_lock = threading.Lock()
        self._deadline: float | None = None
        self._model_started: float | None = None
        self.used = 0
        self.previous = ((resume or {}).get("budget") or {}).get("total_tool_calls", 0)
        if isinstance(self.previous, bool) or not isinstance(self.previous, int) or self.previous < 0:
            raise AgentRunError("续跑快照累计工具次数无效")
        self.warned = False
        self.announced = False
        self.last_tool = ""

    def start_deadline(self):
        if self.timeout is not None:
            self._deadline = time.monotonic() + self.timeout

    def time_left(self) -> float | None:
        with self._deadline_lock:
            if self._deadline is None or self._model_started is not None:
                return None
            return self._deadline - time.monotonic()

    @contextmanager
    def model_scope(self):
        # Python entry points can delegate to the model loop. Only that
        # portion is unlimited; their own Python code still has a deadline.
        with self._deadline_lock:
            started = time.monotonic()
            deadline = self._deadline
            if deadline is not None and started >= deadline:
                from run.agents.contracts import AgentTimeoutError
                raise AgentTimeoutError("子代理进入模型循环前已超过执行时限", process_terminated=True)
            self._model_started = started
        try:
            yield
        finally:
            with self._deadline_lock:
                if deadline is not None:
                    self._deadline = deadline + (time.monotonic() - started)
                self._model_started = None

    def snapshot(self):
        return {
            "max_tool_calls": self.limit, "unlimited": self.limit == 0,
            "tool_calls_used": self.used,
            "tool_calls_remaining": max(0, self.limit - self.used) if self.limit else None,
            "total_tool_calls": self.previous + self.used,
            "timeout": self.timeout if self._model_started is None else None,
            "elapsed_seconds": round(max(0, time.monotonic() - self.started), 3),
            "last_tool": self.last_tool,
        }

    def announce(self):
        warning = bool(self.limit and self.used >= math.ceil(self.limit * .8))
        if self.announced and (self.warned or not warning):
            return
        initial = not self.announced
        self.announced = True
        self.warned = warning
        remaining = max(0, self.limit - self.used) if self.limit else "不限次数"
        left = self.time_left()
        duration = ("本段不设总时限。" if left is None else
                    f"本段运行时长 {self.timeout:g} 秒，约剩余 {max(0, left):.0f} 秒。")
        message = MessageItem.text(
            MessageRole.USER,
            f"[框架运行预算] 本段工具额度 {self.limit or '不限次数'}，已用 {self.used}，"
            f"剩余 {remaining}；{duration}"
            "额度按每个调用计数，不按模型请求计数。"
            + ("接近额度，请优先整理已得结论、必要证据和未完成项，避免启动新长任务。"
               if warning else "按任务范围执行；需更大预算或拆分时向主智能体说明。"),
            item_id=f"msg_budget_{uuid.uuid4().hex}",
        )
        return message, initial

    def consume(self, context, call):
        state = context.continuation
        if self.limit and self.used >= self.limit:
            if state is None or not state.enabled:
                raise AgentToolLimitError(
                    f"子代理 {context.definition.name} 已达到最大工具调用次数 {self.limit}")
            state.stop_reason = "tool_budget_exhausted"
            state.wrap_up.set()
            return False
        self.used += 1
        self.last_tool = call.name
        return True


def capture_budget(context):
    state = context.continuation
    if state is not None:
        with state.lock:
            state.payload["budget"] = context.budget.snapshot()


def provider_attempt(context, usage, retry_state, provider, request):
    if context.compression_budget is not None:
        context.compression_budget.claim_attempt()
    usage["provider_attempt_count"] = usage.get("provider_attempt_count", 0) + 1
    retry_state.usage = copy.deepcopy(usage)
    if context.continuation is not None:
        with context.continuation.lock:
            context.continuation.payload["usage"] = copy.deepcopy(usage)
    response = provider.create(request)
    return complete_usage(request, response) if isinstance(response, KemoResponse) else response


def stopped_result(context, usage, response_ids, *, conclusion=None):
    """Retain the latest paired effects even when wrap-up output is invalid."""
    state = context.continuation
    capture_budget(context)
    if state is not None:
        state.capture(usage, response_ids, conclusion=conclusion)
        if state.stop_reason == "tool_budget_exhausted":
            raise AgentToolLimitError(
                f"子代理 {context.definition.name} 已达到最大工具调用次数 {context.budget.limit}",
                partial_result=state.partial(context.definition.name), continuation=state)


def execution_error_detail(exc, context):
    """All exits retain known usage; only budget stops gain resume authority."""
    from provider.protocol.diagnostics import safe_provider_message
    from run.agents.contracts import AgentCancelledError, AgentTimeoutError
    reason = ("tool_budget_exhausted" if isinstance(exc, AgentToolLimitError)
              else "timeout" if isinstance(exc, AgentTimeoutError)
              else "cancelled" if isinstance(exc, AgentCancelledError)
              else "execution_error")
    state = context.continuation
    if state is not None:
        capture_budget(context)
        if getattr(exc, "partial_result", None) is None:
            setattr(exc, "partial_result", state.partial(context.definition.name))
    detail = {"error": safe_provider_message(str(exc), "子代理运行失败"),
              "exception_type": type(exc).__name__, "stop_reason": reason,
              "budget": context.budget.snapshot()}
    for key in ("category", "code", "status_code", "retryable", "retry_after_ms",
                "retry_attempts", "retry_max_attempts", "retry_exhausted", "retry_budget_exhausted"):
        value = getattr(exc, key, None)
        if isinstance(value, (bool, int, float, str)):
            detail[key] = value[:160] if isinstance(value, str) else value
    if isinstance(exc, AgentTimeoutError):
        detail.update(cancel_requested=True, process_terminated=exc.process_terminated)
        if not exc.process_terminated:
            detail["action_required"] = "inspect_runtime_logs"
    setattr(exc, "stop_reason", reason)
    setattr(exc, "budget", copy.deepcopy(detail["budget"]))
    return detail
