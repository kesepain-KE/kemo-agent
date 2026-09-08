"""Tool-batch execution for the conversation provider loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ToolBatchContext:
    """Explicit boundary between provider response handling and tool execution."""

    shared: dict[str, Any]
    runtime: dict[str, Any]


@dataclass(slots=True)
class ToolBatchResult:
    task_plan_boundary: Any
    retryable_tool_failure: dict[str, Any] | None
    completed: bool
    stop: bool = False


def execute_tool_batch(context: ToolBatchContext):
    """Execute one validated batch while preserving event order and state lists."""

    values = {**context.shared, **context.runtime}
    RunEvent = values['RunEvent']
    ToolCancelledError = values['ToolCancelledError']
    ToolResultTooLargeError = values['ToolResultTooLargeError']
    _assistant_tool_message = values['_assistant_tool_message']
    _close_guidance = values['_close_guidance']
    _json_result = values['_json_result']
    _response_reasoning_item = values['_response_reasoning_item']
    _tool_error_payload = values['_tool_error_payload']
    _tool_failure_is_retryable = values['_tool_failure_is_retryable']
    _tool_result_reuse_allowed = values['_tool_result_reuse_allowed']
    agent_timeout = values['agent_timeout']
    all_text = values['all_text']
    base = values['base']
    blocked_recovery = values['blocked_recovery']
    calls = values['calls']
    cancel_event = values['cancel_event']
    commit_cancelled_round = values['commit_cancelled_round']
    commit_terminal_round = values['commit_terminal_round']
    copy = values['copy']
    defer_failure_commit = values['defer_failure_commit']
    detect_task_plan_creation_boundary = values['detect_task_plan_creation_boundary']
    execute_tool = values['execute_tool']
    failure_limit = values['failure_limit']
    failures = values['failures']
    flush_iteration_observed = values['flush_iteration_observed']
    guidance_channel = values['guidance_channel']
    identical_call_limit = values['identical_call_limit']
    identical_calls = values['identical_calls']
    iteration = values['iteration']
    iteration_reasoning = values['iteration_reasoning']
    iteration_text = values['iteration_text']
    max_tool_calls = values['max_tool_calls']
    messages = values['messages']
    observed_text = values['observed_text']
    pending_tool_calls = values['pending_tool_calls']
    provider_response = values['provider_response']
    registry = values['registry']
    request = values['request']
    seen_calls = values['seen_calls']
    session_id = values['session_id']
    source = values['source']
    source_policy = values['source_policy']
    time = values['time']
    tool_call_signature = values['tool_call_signature']
    tool_records = values['tool_records']
    tool_timeout = values['tool_timeout']
    uploaded_descriptors = values['uploaded_descriptors']
    user = values['user']
    window_path = values['window_path']
    task_plan_boundary = values["task_plan_boundary"]
    completed = False
    assistant_text = "".join(iteration_text)
    iteration_reasoning_text = "".join(iteration_reasoning)
    messages.append(
        _assistant_tool_message(
            assistant_text,
            calls,
            reasoning=iteration_reasoning_text,
            native_reasoning=_response_reasoning_item(
                provider_response,
                streamed_content=iteration_reasoning_text,
            ),
        )
    )
    retryable_tool_failure: dict[str, Any] | None = None
    for call_index, call in enumerate(calls):
        if len(tool_records) >= max_tool_calls:
            _close_guidance(guidance_channel)
            yield commit_terminal_round(
                status="limited",
                reason="max_tool_iterations",
                marker=(
                    f"[本轮工具调用已达到最大次数 {max_tool_calls}，"
                    "本轮已停止]"
                ),
                pending_message=(
                    "工具调用因本轮达到最大工具调用次数而未执行"
                ),
                pending_exception_type="ToolCallLimitExceeded",
            )
            return ToolBatchResult(task_plan_boundary, retryable_tool_failure, completed, stop=True)
        if cancel_event is not None and cancel_event.is_set():
            flush_iteration_observed()
            yield commit_cancelled_round()
            return ToolBatchResult(task_plan_boundary, retryable_tool_failure, completed, stop=True)
        signature = tool_call_signature(call.name, call.arguments)
        reuse_allowed = _tool_result_reuse_allowed(
            call.name,
            call.arguments,
        )
        identical_call_count = identical_calls.record(
            call.name, call.arguments
        )
        duplicate = False
        tool_started = time.monotonic()
        if identical_calls.is_blocked(identical_call_count):
            result_payload = {
                "ok": False,
                "error": {
                    "message": (
                        f"工具 {call.name} 使用完全相同参数连续调用已达到"
                        f"上限 {identical_call_limit} 次"
                    ),
                    "exception_type": (
                        "ConsecutiveIdenticalToolCallLimitExceeded"
                    ),
                    "limit": identical_call_limit,
                    "consecutive_identical_calls": identical_call_count,
                    "instruction": (
                        "请修改参数、改用其他工具或根据已有结果继续任务"
                    ),
                },
            }
            status = "identical_call_blocked"
        elif failures.is_unavailable(call.name):
            result_payload = {
                "ok": False,
                "error": {
                    "message": (
                        f"工具 {call.name} 已连续失败 {failure_limit} 次，"
                        "本轮暂时不可用；请更换工具或调整方案"
                    ),
                    "exception_type": "ToolTemporarilyUnavailable",
                    "consecutive_failures": failure_limit,
                    "temporarily_unavailable": True,
                },
            }
            status = "temporarily_unavailable"
        else:
            blocked_result = blocked_recovery.get(signature)
            duplicate = reuse_allowed and signature in seen_calls
            if blocked_result is not None:
                result_payload = copy.deepcopy(
                    blocked_result.get("result")
                )
                status = "retry_reuse_blocked"
                duplicate = True
            elif duplicate:
                result_payload = copy.deepcopy(seen_calls[signature])
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
                            "agent_timeout": agent_timeout,
                            "transport_registry": request.get(
                                "_transport_registry"
                            ),
                            "task_plan_id": request.get("_task_plan_id"),
                            "task_plan_step_id": request.get("_task_plan_step_id"),
                            "task_plan_mode": request.get("_task_plan_mode"),
                            "knowledge_scopes": list(
                                source_policy.direct_knowledge_scopes()
                            ),
                            "uploaded_files": copy.deepcopy(
                                uploaded_descriptors
                            ),
                        },
                        timeout=tool_timeout,
                        cancel_event=cancel_event,
                    )
                    result_payload = {"ok": True, "result": result}
                    status = "completed"
                except BaseException as exc:
                    if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                        raise
                    cancelled_tool = isinstance(exc, ToolCancelledError)
                    oversized_result = isinstance(
                        exc, ToolResultTooLargeError
                    )
                    result_payload = {
                        "ok": False,
                        "error": {
                            **_tool_error_payload(exc),
                            **({"cancelled": True} if cancelled_tool else {}),
                        },
                    }
                    status = (
                        "cancelled"
                        if cancelled_tool
                        else (
                            "result_too_large"
                            if oversized_result
                            else "failed"
                        )
                    )
                    if bool(getattr(exc, "still_running", False)):
                        failures.unavailable.add(call.name)
                        status = "timed_out_running"
                if result_payload.get("ok") is True:
                    if reuse_allowed:
                        seen_calls[signature] = copy.deepcopy(result_payload)
                    else:
                        seen_calls.pop(signature, None)
                else:
                    seen_calls.pop(signature, None)
            failure_count = failures.record(
                call.name,
                succeeded=(
                    bool(result_payload.get("ok"))
                    or status == "result_too_large"
                ),
            )
            if failure_count >= failure_limit:
                result_payload["error"].update(
                    {
                        "consecutive_failures": failure_count,
                        "temporarily_unavailable": True,
                        "instruction": (
                            "请更换工具或调整方案，不要继续重试该工具"
                        ),
                    }
                )
        elapsed_ms = max(0, round((time.monotonic() - tool_started) * 1000))
        record = {
            "id": call.id,
            "name": call.name,
            "arguments": call.arguments,
            "status": status,
            "duplicate": duplicate,
            "consecutive_identical_calls": identical_call_count,
            "result": result_payload,
            "iteration": iteration,
            "elapsed_ms": elapsed_ms,
        }
        tool_records.append(record)
        pending_tool_calls.pop(call.id, None)
        yield RunEvent(
            type="tool_call_result",
            tool_call_id=call.id,
            tool_name=call.name,
            arguments=call.arguments,
            result=result_payload,
            metadata={
                "status": status,
                "duplicate": duplicate,
                "consecutive_identical_calls": identical_call_count,
                "iteration": iteration,
                "elapsed_ms": elapsed_ms,
            },
        )
        tool_value = result_payload.get("result")
        tool_artifacts = (
            tool_value.get("artifacts")
            if isinstance(tool_value, dict)
            else None
        )
        if isinstance(tool_artifacts, list):
            for artifact in tool_artifacts:
                if isinstance(artifact, dict):
                    yield RunEvent(
                        type="media_output",
                        tool_call_id=call.id,
                        tool_name=call.name,
                        result=copy.deepcopy(artifact),
                        metadata={
                            "artifact": copy.deepcopy(artifact),
                            "source": "tool_result",
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
        if (
            defer_failure_commit
            and retryable_tool_failure is None
            and _tool_failure_is_retryable(result_payload, status)
        ):
            retryable_tool_failure = {
                "tool_name": call.name,
                "error": copy.deepcopy(result_payload.get("error") or {}),
                "status": status,
            }
        if request.get("_task_plan_mode") is None:
            task_plan_boundary = detect_task_plan_creation_boundary(
                tool_name=call.name,
                arguments=call.arguments,
                result_payload=result_payload,
            )
        if task_plan_boundary is not None:
            for pending_call in calls[call_index + 1 :]:
                pending_payload = {
                    "ok": False,
                    "error": {
                        "message": (
                            "任务计划已创建，后续工具必须等待批准或由任务计划执行器处理"
                        ),
                        "exception_type": "TaskPlanCreationBoundary",
                        "plan_id": task_plan_boundary.plan_id,
                    },
                }
                pending_record = {
                    "id": pending_call.id,
                    "name": pending_call.name,
                    "arguments": pending_call.arguments,
                    "status": "not_executed",
                    "duplicate": False,
                    "consecutive_identical_calls": 0,
                    "result": pending_payload,
                    "iteration": iteration,
                    "elapsed_ms": 0,
                }
                tool_records.append(pending_record)
                pending_tool_calls.pop(pending_call.id, None)
                yield RunEvent(
                    type="tool_call_result",
                    tool_call_id=pending_call.id,
                    tool_name=pending_call.name,
                    arguments=pending_call.arguments,
                    result=pending_payload,
                    metadata={
                        "status": "not_executed",
                        "duplicate": False,
                        "consecutive_identical_calls": 0,
                        "iteration": iteration,
                        "elapsed_ms": 0,
                        "plan_id": task_plan_boundary.plan_id,
                    },
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": pending_call.id,
                        "name": pending_call.name,
                        "content": _json_result(pending_payload),
                    }
                )
            boundary_text = task_plan_boundary.message
            prefix = "\n\n" if all_text else ""
            visible_boundary_text = f"{prefix}{boundary_text}"
            all_text.append(visible_boundary_text)
            observed_text.append(visible_boundary_text)
            yield RunEvent(type="text_delta", content=visible_boundary_text)
            _close_guidance(guidance_channel)
            completed = True
            break
    return ToolBatchResult(task_plan_boundary, retryable_tool_failure, completed)
