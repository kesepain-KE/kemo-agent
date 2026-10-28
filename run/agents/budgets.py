"""Per-segment budgets, safe-point reminders and bounded public diagnostics."""
from __future__ import annotations

import copy
import math
import time
import uuid

from provider.protocol.enums import MessageRole
from provider.protocol.models import MessageItem
from run.agents.contracts import AgentRunError, AgentToolLimitError

BUDGET_STOPS = {"timed_out", "tool_limit"}


def validate_input_budget(context, system, items, tools):
    """Include native framing and repair additions in the last safety gate."""
    if context.input_token_limit is None:
        return
    from run.context import estimate_messages_tokens, estimate_tools_tokens
    from run.agents.contracts import AgentProviderError
    measured = estimate_messages_tokens([
        {"role": "system", "content": system},
        *[item.model_dump(mode="json", exclude_none=True) for item in items],
    ]) + estimate_tools_tokens([
        tool.model_dump(mode="json", exclude_none=True) for tool in tools
    ])
    if measured > context.input_token_limit:
        raise AgentProviderError(
            "摘要完整请求超过输入预算", category="context_length_exceeded",
            code="summary_input_budget_exceeded", retryable=False,
        )


def legacy_tool_stop(record):
    return (record.get("status") == "failed"
            and (record.get("error") or {}).get("exception_type") == "AgentToolLimitError"
            and not record.get("execution_live")
            and (record.get("error") or {}).get("process_terminated") is not False)


def upgrade_legacy_budget(restored):
    if restored.get("budget"):
        return
    from run.agents.continuation import restore_items
    from provider.protocol.models import ToolResultItem, JsonContent
    count = sum(1 for item in restore_items(restored)
                if isinstance(item, ToolResultItem) and item.name != "submit_structured_output"
                and not any(isinstance(c, JsonContent) and isinstance(c.data, dict)
                            and c.data.get("not_executed") for c in item.content))
    restored["budget"] = {"total_tool_calls": count}


def overridable(definition):
    return (definition.executor == "builtin:llm"
            and definition.execution != "background_serial"
            and definition.capabilities.exposure == "tool"
            and "main_agent" in definition.capabilities.allowed_callers)


def effective_limit(definition, config, requested=None):
    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int) or requested < 0:
            raise AgentRunError("max_tool_calls 必须是非负整数；0 表示不限工具次数")
        if not overridable(definition):
            raise AgentRunError("此子代理不支持覆盖工具预算")
        return requested
    value = (config.get("tools") or {}).get("max_iterations", 80)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AgentRunError("tools.max_iterations 必须是正整数")
    return min(value, definition.capabilities.max_tool_iterations)


class SegmentBudget:
    def __init__(self, definition, config, requested, timeout, resume=None):
        self.limit = effective_limit(definition, config, requested)
        self.timeout = timeout
        self.started = time.monotonic()
        self.used = 0
        self.previous = ((resume or {}).get("budget") or {}).get("total_tool_calls", 0)
        if isinstance(self.previous, bool) or not isinstance(self.previous, int) or self.previous < 0:
            raise AgentRunError("续跑快照累计工具次数无效")
        self.warned = False
        self.announced = False
        self.last_tool = ""

    def snapshot(self):
        return {
            "max_tool_calls": self.limit, "unlimited": self.limit == 0,
            "tool_calls_used": self.used,
            "tool_calls_remaining": max(0, self.limit - self.used) if self.limit else None,
            "total_tool_calls": self.previous + self.used,
            "timeout": self.timeout,
            "elapsed_seconds": round(max(0, time.monotonic() - self.started), 3),
            "last_tool": self.last_tool,
        }

    def announce(self, items):
        warning = bool(self.limit and self.used >= math.ceil(self.limit * .8))
        if self.announced and (self.warned or not warning):
            return
        initial = not self.announced
        self.announced = True
        self.warned = warning
        remaining = max(0, self.limit - self.used) if self.limit else "不限次数"
        message = MessageItem.text(
            MessageRole.USER,
            f"[框架运行预算] 本段工具额度 {self.limit or '不限次数'}，已用 {self.used}，"
            f"剩余 {remaining}；本段运行时长 {self.timeout:g} 秒，"
            f"约剩余 {max(0, self.timeout - (time.monotonic() - self.started)):.0f} 秒。"
            "额度按每个调用计数，不按模型请求计数。"
            + ("接近额度，请优先整理已得结论、必要证据和未完成项，避免启动新长任务。"
               if warning else "按任务范围执行；需更大预算或拆分时向主智能体说明。"),
            item_id=f"msg_budget_{uuid.uuid4().hex}",
        )
        if initial:
            items.insert(0, message)
        else:
            items.append(message)

    def consume(self, context, call, items):
        state = context.continuation
        if self.limit and self.used >= self.limit:
            if state is None or not state.enabled:
                raise AgentToolLimitError(
                    f"子代理 {context.definition.name} 已达到最大工具调用次数 {self.limit}")
            state.stop_reason = "tool_budget_exhausted"
            state.wrap_up.set()
            from run.agents.continuation import pair_pending
            pair_pending(items)
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
    usage["provider_attempt_count"] = usage.get("provider_attempt_count", 0) + 1
    retry_state.usage = copy.deepcopy(usage)
    if context.continuation is not None:
        with context.continuation.lock:
            context.continuation.payload["usage"] = copy.deepcopy(usage)
    return provider.create(request)


def stopped_result(context, items, usage, response_ids, *, conclusion=None):
    """Retain the latest paired effects even when wrap-up output is invalid."""
    state = context.continuation
    capture_budget(context)
    if state is not None:
        state.capture(items, usage, response_ids, conclusion=conclusion)
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
