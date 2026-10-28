"""Model-loop continuation helpers, separate from the ordinary execution path."""
from __future__ import annotations

import uuid

from provider.protocol.enums import MessageRole
from provider.protocol.models import MessageItem
from run.agents.continuation import pair_pending
from run.agents.contracts import AgentRunError
from run.tools import _enforce_tool_result_limit, ToolResultTooLargeError
from run.retry import HOST_CONTROL_EXCEPTIONS


def wrap_request(context, items, tool_definitions):
    state = getattr(context, "continuation", None)
    if state is None or not state.wrap_up.is_set():
        return tool_definitions, context.max_tokens
    if state.wrap_injected:
        raise AgentRunError("收尾请求已结束，不再开启新的工具或模型循环")
    state.wrap_injected = True
    pair_pending(items)
    items.append(MessageItem.text(
        MessageRole.USER,
        ("[框架工具预算收尾指令] 本段工具额度已到。" if state.stop_reason == "tool_budget_exhausted"
         else "[框架超时收尾指令] 本轮时间已到。") + "不要再调用业务工具；基于已有信息，"
        "按当前 output_schema 提交简短的已有结论，不猜测。Schema 有 status 时填 partial；"
        "如有未完成事项字段则列出。只使用结构化提交工具（若有），否则直接输出最终 JSON。",
        item_id=f"msg_wrap_{uuid.uuid4().hex}",
    ))
    return [t for t in tool_definitions if t.name == "submit_structured_output"], min(context.max_tokens or 4000, 4000)


def drain_tool(exc, *, tool_name, arguments, root, user):
    """Keep the agent executor alive until its non-cooperative tool truly exits.

    Saving its actual return pairs the call and prevents resume replaying effects.
    This waits outside the Provider pool. Cancellation cannot kill Python threads.
    """
    future = getattr(exc, "completion_future", None)
    if future is None:
        return None
    try:
        value = future.result()
        value = _enforce_tool_result_limit(tool_name, arguments, value,
                                          context={"root": str(root), "user": user})
        return {"ok": True, "result": value}, "completed"
    except ToolResultTooLargeError as error:
        return {"ok": False, "error": error.error_payload()}, "result_too_large"
    except HOST_CONTROL_EXCEPTIONS:
        raise
    except Exception as error:
        from provider.protocol.diagnostics import safe_provider_message
        return {"ok": False, "execution_completed": True, "error": {
            "message": safe_provider_message(str(error), "工具已退出，执行失败；先核对副作用，不要盲目重试"),
            "exception_type": type(error).__name__,
        }}, "failed"
