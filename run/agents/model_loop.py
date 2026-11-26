"""Sub-agent model loop used by AgentRunner."""

from __future__ import annotations

import copy
import json
import time
import uuid
from typing import TYPE_CHECKING, Any

from events import RunEvent
from provider.factory import ProviderCongestionError, provider_request_slot
from provider.protocol.diagnostics import safe_provider_message
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import (
    KemoRequest, KemoResponse, MessageItem, ReasoningConfig,
    ToolCallItem, ToolDefinition, ToolResultItem, text_from_content,
)
from provider.token_usage import reported_token_fields
from agents._runtime.resources import effective_knowledge_scopes
from run.extensions import (
    MediaInputHost, context_input_budget, model_output_token_limit, resolve_reasoning_selection,
)
from run.agents.contracts import (
    AgentCancelledError, AgentInputError, AgentOutputError,
    AgentRunError, AgentRunResult, AgentToolRetryError,
    _parse_json_object, resolve_agent_provider_config, validate_json_schema,
)
from run.agents.card_stream import begin_model_attempt
from run.agents.continuation import response_items_for_next_request
from run.agents.loop_safepoints import drain_tool, wrap_request
from run.agents.model_failures import raise_provider_failure
from run.agents.budgets import SegmentBudget, capture_budget, stopped_result, overridable, provider_attempt, validate_input_budget
from run.agents.controls import take_guidance
from run.agents.retry_policy import (
    _AgentRetryState, _agent_recovery_records,
    _agent_tool_failure_is_retryable,
    _record_agent_recovery,
)
from run.retry import HOST_CONTROL_EXCEPTIONS, AttemptToolDedup, view_from_error
from run.tools import (
    ConsecutiveIdenticalToolCallTracker, ConsecutiveToolFailureTracker, ShellInbox,
    ToolExecutionCapacityError, ToolResultTooLargeError, execute_tool,
    invalid_tool_name, response_invalid_tool_arguments_error,
    system_prompt_with_tool_argument_repair, tool_call_signature,
    tool_result_payload, validate_tool_call_batch,
)

if TYPE_CHECKING:
    from run.agents.runner import AgentExecutionContext


_STRUCTURED_OUTPUT_TOOL_NAME = "submit_structured_output"
_HEARTBEAT_INTERVAL_SECONDS = 600
_heartbeat_clock = time.monotonic


def run_model(
    agent_runner,
    context: AgentExecutionContext,
    input_data: dict[str, Any],
    *,
    retry_state: _AgentRetryState | None = None,
    attempt: int = 1,
    max_attempts: int = 1,
) -> AgentRunResult:
    from run.conversation import is_context_length_exceeded, response_terminal_error

    retry_state = retry_state or _AgentRetryState()
    # Snapshots present before this attempt are the only records that should
    # be labelled as recovered. Current-attempt results are emitted via
    # ``tool_records`` and must retain statuses such as ``completed``.
    recovery_signatures_at_start = set(retry_state.recovery)
    definition = context.definition
    shell_inbox = ShellInbox(agent_runner.root, agent_runner.user, context.source,
                             context.session_id, owner_task_id=context.task_id)
    runtime = resolve_agent_provider_config(
        agent_runner.config,
        definition,
        model_override=context.model_override,
    )
    continuation = context.continuation
    assert continuation is not None
    stream = begin_model_attempt(context, runtime, input_data, retry_state)
    total_usage: dict[str, Any] = copy.deepcopy(retry_state.usage)
    response_ids: list[str] = list(retry_state.response_ids)
    capture_budget(context)
    continuation.capture(total_usage, response_ids)
    provider = agent_runner.provider_factory(runtime)
    reasoning_selection = resolve_reasoning_selection(
        agent_runner.config,
        runtime,
        provider,
        model=runtime["model"],
        cancel_event=context.cancel_event,
    )
    request_reasoning = None
    if reasoning_selection.enabled and reasoning_selection.capabilities is not None:
        declared = reasoning_selection.capabilities.reasoning
        return_mode = next(
            (mode for mode in ("content", "auto", "summary", "none") if mode in declared.returns),
            None,
        )
        context_mode = next((
            mode for mode in ("auto", "none", "current_turn", "all_turns")
            if mode in declared.contexts and (mode != "all_turns" or declared.persisted_state)
        ), None)
        if return_mode is not None and context_mode is not None:
            request_reasoning = ReasoningConfig.model_validate({
                "enabled": True, "effort": reasoning_selection.effort,
                "return": return_mode, "context": context_mode,
            })
    media_inputs = MediaInputHost(
        root=agent_runner.root, user=agent_runner.user,
        scope=("subagent", context.source, context.session_id, context.task_id, definition.name),
        state=retry_state.auxiliary, config=agent_runner.config,
        runtime_provider=runtime, provider=provider,
        session_id=context.session_id, known=[],
        user_text=json.dumps(input_data, ensure_ascii=False),
        cancel_event=context.cancel_event,
        input_limit=None,
    )
    system = (
        context.prompt_bundle.text
        + "\n\n[output_schema]\n"
        + json.dumps(definition.output_schema, ensure_ascii=False, sort_keys=True)
    )
    if context.structured_output_tool:
        system += (
            "\n\n[structured_output_transport]\n"
            f"必须调用 {_STRUCTURED_OUTPUT_TOOL_NAME} 一次提交最终结果；"
            "工具参数必须符合 output_schema。不要把最终结果作为普通文本输出。"
        )
    tool_records: list[dict[str, Any]] = []
    final_text = ""
    final_data: dict[str, Any] | None = None
    final_model = runtime["model"]
    parent_request_id: str | None = None
    tool_config = agent_runner.config.get("tools") or {}
    context.budget = context.budget or SegmentBudget(definition, None, definition.timeout)
    raw_invalid_tool_arguments_retries = tool_config.get(
        "invalid_tool_arguments_retries", 2
    )
    if (
        isinstance(raw_invalid_tool_arguments_retries, bool)
        or not isinstance(raw_invalid_tool_arguments_retries, int)
        or raw_invalid_tool_arguments_retries < 0
    ):
        raise AgentRunError(
            "tools.invalid_tool_arguments_retries 必须是大于等于 0 的整数"
        )
    invalid_tool_arguments_retry_limit = raw_invalid_tool_arguments_retries
    tool_timeout = float(tool_config.get("timeout", 240))
    agent_timeout = (agent_runner.config.get("agent_runtime") or {}).get(
        "default_timeout", 600
    )
    raw_identical_call_limit = tool_config.get(
        "consecutive_identical_call_limit", 3
    )
    if (
        isinstance(raw_identical_call_limit, bool)
        or not isinstance(raw_identical_call_limit, int)
        or raw_identical_call_limit < 1
    ):
        raise AgentRunError("tools.consecutive_identical_call_limit 必须是正整数")
    identical_call_limit = raw_identical_call_limit
    raw_failure_limit = (agent_runner.config.get("history") or {}).get(
        "consecutive_tool_fail_limit", 5
    )
    if (
        isinstance(raw_failure_limit, bool)
        or not isinstance(raw_failure_limit, int)
        or raw_failure_limit < 1
    ):
        raise AgentRunError("history.consecutive_tool_fail_limit 必须是正整数")
    failure_limit = raw_failure_limit
    failures = ConsecutiveToolFailureTracker(failure_limit)
    identical_calls = ConsecutiveIdenticalToolCallTracker(identical_call_limit)
    seen_calls = AttemptToolDedup(retry_state.recovery)
    tool_argument_retry_count = 0
    shell_notices: list[tuple[tuple[str, ...], str]] = []
    provider_input_limit = None
    preventative_retry_at = 0.0

    def take_shell_notice() -> bool:
        before = len(shell_inbox.delivered)
        notice = shell_inbox.take(committed_jobs=stream.shell_jobs)
        if notice:
            job_ids = tuple(job_id for job_id in shell_inbox.delivered[before:]
                            if job_id not in stream.shell_jobs)
            shell_notices.append((job_ids, notice["content"]))
        return notice is not None

    def progress(status: str, **detail: Any) -> None:
        if context.event_callback is not None:
            from run.agents.public_trace import public_tool_data
            # Never send raw arguments/results into event callbacks or logs.
            for key in ("arguments", "result"):
                if key in detail:
                    detail[key] = public_tool_data(detail[key])
            context.event_callback(RunEvent(type="subagent_progress", metadata={
                "phase": "subagent", "agent": definition.name,
                "task_id": context.task_id, "source": context.source,
                "session_id": context.session_id, "status": status,
                "budget": context.budget.snapshot(), "usage": total_usage, **detail,
            }))

    def check_boundary():
        if context.cancel_event.is_set():
            raise AgentCancelledError(f"子代理 {definition.name} 已取消")
        if not context.managed or context.event_callback is None:
            return
        now = _heartbeat_clock()
        last = retry_state.heartbeat_at
        if now - (context.budget.started if last is None else last) < _HEARTBEAT_INTERVAL_SECONDS:
            return
        retry_state.heartbeat_at = now
        retry_state.heartbeat_seq += 1
        progress("running", heartbeat=True, heartbeat_seq=retry_state.heartbeat_seq,
                 elapsed_seconds=round(now - context.budget.started, 3),
                 usage={"input_tokens": int(total_usage.get("prompt_tokens") or 0),
                        "output_tokens": int(total_usage.get("completion_tokens") or 0),
                        "total_tokens": int(total_usage.get("total_tokens") or 0)},
                 tool_calls_used=context.budget.used, recent_cards=stream.heartbeat_cards())

    from itertools import count
    iteration = 0
    for iteration in count(1):
        check_boundary()
        if overridable(definition) and (announcement := context.budget.announce()):
            message, first = announcement
            stream.control("budget", message, first=first)
        take_guidance(context, stream)
        capture_budget(context)
        if context.budget.limit and context.budget.used >= context.budget.limit and shell_inbox.pending():
            if not shell_inbox.wait(context.cancel_event,
                    wrap_up=continuation.wrap_up):
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
        take_shell_notice()
        tail = [MessageItem.text(MessageRole.USER, text) for _, text in shell_notices]
        media_inputs.refresh_items(tail)
        tool_schemas = context.tool_registry.schemas(exclude=failures.unavailable)
        tool_definitions = agent_runner._tool_definitions(tool_schemas)
        if context.structured_output_tool:
            if any(
                tool.name == _STRUCTURED_OUTPUT_TOOL_NAME
                for tool in tool_definitions
            ):
                raise AgentRunError(
                    f"子代理工具名与内部结构化输出工具冲突："
                    f"{_STRUCTURED_OUTPUT_TOOL_NAME}"
                )
            tool_definitions.append(
                ToolDefinition(
                    name=_STRUCTURED_OUTPUT_TOOL_NAME,
                    description="提交符合输出 Schema 的最终结构化结果。",
                    parameters=definition.output_schema,
                    strict=True,
                )
            )
        tool_definitions, request_max_tokens = wrap_request(context, tail, tool_definitions)
        configured_limit = int((agent_runner.config.get("agents") or {}).get("token_limit", 1000000))
        request_max_tokens = model_output_token_limit(
            reasoning_selection.capabilities,
            configured=request_max_tokens if request_max_tokens is not None else runtime.get("max_tokens"),
        )
        reservation = int((agent_runner.config.get("agents") or {}).get("output_reserve", 10000))
        if request_max_tokens is not None:
            reservation = min(reservation, request_max_tokens)
        capacity = context_input_budget(
            configured_limit, reasoning_selection.capabilities, output_reserve=reservation)
        request_input_limit = min(capacity["effective_token_limit"],
                                  context.input_token_limit or capacity["effective_token_limit"])
        if provider_input_limit is not None:
            request_input_limit = min(request_input_limit, provider_input_limit)
        invalid_tool_arguments_retries = 0
        repair_tool_name = ""
        overflow_attempts = 0
        overflow_wire = None

        def recover_overflow(error, measured):
            nonlocal overflow_attempts, overflow_wire, request_input_limit, provider_input_limit
            if (overflow_attempts >= 3 or context.cancel_event.is_set()
                    or not is_context_length_exceeded(error)):
                return False
            overflow_attempts += 1
            overflow_wire = measured
            request_input_limit = max(1, min(request_input_limit - 1, int(measured * .7)))
            provider_input_limit = request_input_limit
            return True

        while True:
            if invalid_tool_arguments_retries or overflow_attempts:
                check_boundary()
            request_id = f"req_{uuid.uuid4().hex}"
            request_system = (
                system_prompt_with_tool_argument_repair(
                    system,
                    tool_name=repair_tool_name,
                    retry_number=invalid_tool_arguments_retries,
                )
                if invalid_tool_arguments_retries
                else system
            )
            prepared = validate_input_budget(
                context, request_system, tool_definitions, tail, provider=provider,
                runtime=runtime, capabilities=reasoning_selection.capabilities,
                configured_limit=configured_limit, input_limit=request_input_limit,
                usage=total_usage, retry_state=retry_state, force_compact=overflow_wire is not None,
                skip_preventative=time.monotonic() < preventative_retry_at,
            )
            if prepared.compaction is not None or prepared.compaction_error is not None:
                preventative_retry_at = time.monotonic() + 60
            overflow_wire = None
            items = list(prepared.items)
            media_inputs.input_limit = request_input_limit
            media_inputs.validate_items(items, request_system, tool_definitions)
            output_limit = model_output_token_limit(
                reasoning_selection.capabilities, configured=request_max_tokens,
                input_tokens=prepared.wire_tokens,
            )
            try:
                progress("model_request", iteration=iteration)
                with provider_request_slot(
                    agent_runner.config,
                    cancel_event=context.cancel_event,
                ):
                    if context.cancel_event.is_set():
                        raise AgentCancelledError(f"子代理 {definition.name} 已取消")
                    response = provider_attempt(context, total_usage, retry_state, provider,
                        KemoRequest.create(
                            request_id=request_id,
                            parent_request_id=parent_request_id,
                            attempt=attempt + invalid_tool_arguments_retries,
                            model=runtime["model"],
                            stream=False,
                            system_prompt=request_system,
                            input=items,
                            tools=tool_definitions,
                            generation={"max_output_tokens": output_limit},
                            reasoning=request_reasoning,
                            provider_options={},
                            metadata={
                                "capability": "conversation",
                                "user": agent_runner.user,
                                "source": "subagent",
                                "agent": definition.name,
                                "task_id": context.task_id,
                                "iteration": iteration,
                                "tool_argument_retry": (
                                    invalid_tool_arguments_retries
                                ),
                                "retry_attempt": attempt,
                            },
                        )
                    )
            except ProviderCongestionError as exc:
                if context.cancel_event.is_set():
                    raise AgentCancelledError(
                        f"子代理 {definition.name} 已取消"
                    ) from exc
                raise
            except Exception as error:
                if not recover_overflow(error, prepared.wire_tokens):
                    raise
                parent_request_id = parent_request_id or request_id
                if failed_usage := getattr(error, "failed_usage", None):
                    agent_runner._merge_usage(total_usage, {"provider_request_count": 1, **failed_usage})
                    retry_state.usage = copy.deepcopy(total_usage)
                    with continuation.lock:
                        continuation.payload["usage"] = copy.deepcopy(total_usage)
                continue
            if not isinstance(response, KemoResponse):
                raise AgentRunError("Provider create() 必须返回 KemoResponse")
            response_ids.append(response.id)
            retry_state.response_ids = list(response_ids)
            parent_request_id = parent_request_id or request_id
            agent_runner._merge_usage(total_usage, agent_runner._usage_dict(response.usage))
            retry_state.usage = copy.deepcopy(total_usage)
            final_model = response.model or runtime["model"]
            with continuation.lock:
                continuation.payload["usage"] = copy.deepcopy(total_usage)
                continuation.payload["model"] = final_model
                continuation.payload["response_ids"] = list(response_ids)[-160:]
            if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
                if recover_overflow(response_terminal_error(response), prepared.wire_tokens):
                    continue
            elif (type(actual_input := response.usage.input_tokens) is int
                  and "input_tokens" in reported_token_fields(response.usage)):
                stream.store.observe_usage(
                    prepared.snapshot, request_id=request_id, estimated=prepared.estimated_tokens,
                    actual=actual_input, items=prepared.items,
                )
            invalid_error = response_invalid_tool_arguments_error(response)
            if invalid_error is None:
                declared_tool_schemas: dict[str, dict[str, Any]] = {}
                for raw_schema in context.tool_registry.schemas():
                    function = (
                        (checked_286 if isinstance(checked_286 := raw_schema.get("function"), dict) else raw_schema)
                    )
                    name = str(function.get("name") or "").strip()
                    parameters = function.get("parameters") or function.get(
                        "input_schema"
                    )
                    if name and isinstance(parameters, dict):
                        declared_tool_schemas[name] = parameters
                for tool in tool_definitions:
                    if isinstance(tool.parameters, dict):
                        declared_tool_schemas[tool.name] = tool.parameters
                invalid_error = validate_tool_call_batch(
                    [
                        item
                        for item in response.output
                        if isinstance(item, ToolCallItem)
                    ],
                    declared_tool_schemas,
                )
            if invalid_error is None:
                break
            if continuation.wrap_injected:
                raise AgentOutputError("超时收尾输出无效，不自动重跑任务")
            if (
                invalid_tool_arguments_retries
                >= invalid_tool_arguments_retry_limit
            ):
                raise AgentRunError(
                    f"{invalid_error['message']}；已重试 "
                    f"{invalid_tool_arguments_retries}/"
                    f"{invalid_tool_arguments_retry_limit} 次"
                )
            invalid_tool_arguments_retries += 1
            tool_argument_retry_count += 1
            repair_tool_name = invalid_tool_name(invalid_error)
        if response.status not in {ResponseStatus.COMPLETED, ResponseStatus.REQUIRES_ACTION}:
            raise_provider_failure(response, continuation, definition.name, final_model)
        normalized_output = response_items_for_next_request(response.output)
        stream.response(normalized_output, shell_notices)
        shell_notices.clear()
        media_inputs.acknowledge()
        shell_inbox.acknowledge()
        returned = {item.call_id for item in normalized_output if isinstance(item, ToolResultItem)}
        calls = [item for item in normalized_output
                 if isinstance(item, ToolCallItem) and item.call_id not in returned]
        messages = [
            item for item in normalized_output if isinstance(item, MessageItem)
        ]
        public_text = "".join(text_from_content(item.content) for item in messages)
        fragment = ({"status": "partial", "summary": public_text[:6000],
                     "verification": ["阶段性公开正文，尚未通过最终输出校验。"]}
                    if public_text and calls else None)
        continuation.capture(total_usage, response_ids, conclusion=fragment)
        structured_calls = [
            call for call in calls if call.name == _STRUCTURED_OUTPUT_TOOL_NAME
        ]
        if (not calls or (len(calls) == 1 and len(structured_calls) == 1)) and not continuation.wrap_up.is_set():
            if not shell_inbox.wait(context.cancel_event,
                                    wrap_up=continuation.wrap_up):
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
            if take_shell_notice():
                # A proposed structured submission is not final while results
                # remain to be processed. Pair it before making another request.
                for proposed in structured_calls:
                    stream.result(proposed.call_id,
                                  {"ok": False, "reason": "请先处理后台 Shell 结束通知"}, "not_executed")
                continue
        if structured_calls:
            raw_structured = json.dumps(
                structured_calls[0].arguments,
                ensure_ascii=False,
                sort_keys=True,
            )
            if len(structured_calls) != 1 or len(calls) != 1:
                raise AgentOutputError(
                    "子代理结构化输出必须且只能调用一次提交工具",
                    raw_text=raw_structured,
                )
            try:
                validate_json_schema(
                    structured_calls[0].arguments,
                    definition.output_schema,
                )
            except AgentInputError as exc:
                raise AgentOutputError(
                    str(exc),
                    raw_text=raw_structured,
                ) from exc
            final_data = dict(structured_calls[0].arguments)
            final_text = raw_structured
            stream.result(structured_calls[0].call_id, {"ok": True}, "completed")
            retry_state.progress = True
            tool_records.append(
                {
                    "id": structured_calls[0].call_id,
                    "name": _STRUCTURED_OUTPUT_TOOL_NAME,
                    "arguments": structured_calls[0].arguments,
                    "status": "structured_output",
                    "iteration": iteration,
                }
            )
            break
        if not calls:
            final_text = "".join(
                text_from_content(item.content) for item in messages
            )
            break
        retry_state.progress = True
        retryable_tool_failure: AgentToolRetryError | None = None
        for call in calls:
            if context.cancel_event.is_set():
                raise AgentCancelledError(f"子代理 {definition.name} 已取消")
            if continuation.wrap_up.is_set():
                break
            import time as _tool_time
            tool_started = _tool_time.monotonic()
            if not context.budget.consume(context, call):
                stream.settle_unstarted()
                capture_budget(context)
                continuation.capture(total_usage, response_ids)
                break
            signature = tool_call_signature(call.name, call.arguments)
            duplicate = False
            identical_call_count = identical_calls.count if signature == identical_calls.signature else 0
            # Safe defaults so the record below is always bound; every branch of the
            # dispatch chain assigns both before they are read.
            payload: dict[str, Any] = {}
            status = "failed"
            dedup_result = (None if failures.is_unavailable(call.name)
                            else seen_calls.check(call.name, call.arguments, identical_calls))
            if stream.unknown_legacy_call(call):
                payload = {"ok": False, "error": {
                    "code": "legacy_execution_unknown",
                    "message": "该调用在旧续跑快照中的结果未知，未重复执行；请先核对实际状态。",
                }}
                status, duplicate = "duplicate_blocked", True
            elif dedup_result is not None:
                payload, status, duplicate, identical_call_count = dedup_result
            elif failures.is_unavailable(call.name):
                payload = {
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
                capacity_limited = False
                execution_attempted = False
                if dedup_result is None:
                    stream.start_tool(call)
                    try:
                        tool = context.tool_registry.get(call.name)
                        progress("tool_running", iteration=iteration, tool_name=call.name,
                                 tool_call_id=call.call_id, arguments=call.arguments,
                                 tool_count=len(tool_records) + 1)
                        execution_attempted = True
                        value = execute_tool(
                            tool,
                            call.arguments,
                            context={
                                "root": str(agent_runner.root),
                                "user": agent_runner.user,
                                "source": context.source,
                                "session_id": context.session_id,
                                "caller": "subagent",
                                "agent": definition.name,
                                "task_id": context.task_id,
                                "shell_ownership": {
                                    **shell_inbox.ownership(), "tool_call_id": call.call_id,
                                    "original_call": {"name": call.name,
                                                      "arguments": copy.deepcopy(call.arguments)},
                                },
                                "agent_trigger": input_data.get("trigger"),
                                "tool_timeout": tool_timeout,
                                "agent_timeout": agent_timeout,
                                "knowledge_scopes": list(
                                    effective_knowledge_scopes(
                                        definition,
                                        agent_runner.config,
                                    )
                                ),
                                "uploaded_files": list(media_inputs.known),
                                **({
                                    "user_text": media_inputs.user_text,
                                    "tool_records": copy.deepcopy(tool_records),
                                } if call.name == "multimodal" else {}),
                            },
                            timeout=tool_timeout,
                            cancel_event=context.cancel_event,
                        )
                        if call.name == "attach_media":
                            value = media_inputs.accept(
                                tool, call.arguments, value,
                                call_id=call.call_id, tool_records=tool_records,
                            )
                        payload = tool_result_payload(
                            call.name, value, root=agent_runner.root,
                            user=agent_runner.user, session_id=context.session_id,
                        )
                        status = "completed" if payload["ok"] is True else "failed"
                    except ToolResultTooLargeError as exc:
                        payload = {"ok": False, "error": exc.error_payload()}
                        status = "result_too_large"
                    except HOST_CONTROL_EXCEPTIONS:
                        raise
                    except Exception as exc:
                        capacity_limited = isinstance(exc, ToolExecutionCapacityError)
                        tool_error: dict[str, Any] = {
                            "message": safe_provider_message(
                                str(exc),
                                "工具调用失败",
                            ),
                            "exception_type": str(
                                getattr(exc, "remote_exception_type", "")
                                or type(exc).__name__
                            ),
                        }
                        for field in ("category", "retry_after_ms", "still_running"):
                            value = getattr(exc, field, None)
                            if isinstance(value, (bool, int, float)):
                                tool_error[field] = value
                            elif isinstance(value, str) and value.strip():
                                tool_error[field] = value.strip()[:160]
                        retryable = getattr(exc, "retryable", None)
                        if isinstance(retryable, bool) and getattr(
                            exc,
                            "retryable_declared",
                            True,
                        ):
                            tool_error["retryable"] = retryable
                        payload = {
                            "ok": False,
                            "error": tool_error,
                        }
                        if bool(getattr(exc, "still_running", False)):
                            failures.unavailable.add(call.name)
                            status = "timed_out_running"
                        else:
                            status = "failed"
                        recovered = drain_tool(
                            exc, tool_name=call.name, arguments=call.arguments,
                            root=agent_runner.root, user=agent_runner.user,
                        )
                        if recovered is not None:
                            payload, status = recovered
                    finally:
                        if execution_attempted and not capacity_limited:
                            identical_call_count = identical_calls.record(call.name, call.arguments)
                    seen_calls.remember(call.name, call.arguments, payload, iteration)
                    failure_count = (
                        failures.record_capacity_limited(call.name)
                        if capacity_limited
                        else failures.record(
                            call.name,
                            succeeded=(
                                bool(payload.get("ok")) or status == "result_too_large"
                            ),
                        )
                    )
                    if failure_count >= failure_limit:
                        payload["error"].update(
                            {
                                "consecutive_failures": failure_count,
                                "temporarily_unavailable": True,
                                "instruction": "请更换工具或调整方案，不要继续重试该工具",
                            }
                        )
            tool_records.append(
                {
                    "id": call.call_id,
                    "name": call.name,
                    "arguments": call.arguments,
                    "status": status,
                    "duplicate": duplicate,
                    "result": payload,
                    "iteration": iteration,
                    "consecutive_identical_calls": identical_call_count,
                }
            )
            duration_ms = round((_tool_time.monotonic() - tool_started) * 1000)
            stream.result(call.call_id, payload, status, duration_ms=duration_ms)
            progress("tool_finished", iteration=iteration, tool_name=call.name,
                     tool_call_id=call.call_id, tool_status=status,
                     arguments=call.arguments, result=payload,
                     elapsed_ms=duration_ms,
                     summary="执行成功" if payload.get("ok") else "执行失败")
            _record_agent_recovery(retry_state, call, payload, iteration=iteration)
            capture_budget(context)
            continuation.capture(total_usage, response_ids)
            if (
                (
                    context.retry_ledger is None
                    or context.retry_ledger.would_allow_retry(retry_state.progress)
                )
                and retryable_tool_failure is None
                and _agent_tool_failure_is_retryable(payload, status)
            ):
                error = payload.get("error")
                if not isinstance(error, dict):
                    error = {}
                failure = view_from_error(error)
                retryable_tool_failure = AgentToolRetryError(
                    call.name,
                    status_code=failure.status_code,
                    retry_after_ms=failure.retry_after_ms,
                )
        if continuation.wrap_up.is_set():
            stream.settle_unstarted()
        if retryable_tool_failure is not None:
            raise retryable_tool_failure
    progress("validating", iteration=iteration)
    if final_data is None:
        try:
            data = _parse_json_object(final_text)
            validate_json_schema(data, definition.output_schema)
        except AgentOutputError as exc:
            raise AgentOutputError(str(exc), raw_text=final_text) from exc
        except AgentInputError as exc:
            raise AgentOutputError(str(exc), raw_text=final_text) from exc
    else:
        data = final_data
    stopped_result(context, total_usage, response_ids, conclusion=data)
    current_tool_ids = {
        str(record.get("id") or "")
        for record in tool_records
        if isinstance(record, dict)
    }
    committed_tool_records = [
        *_agent_recovery_records(
            {
                signature: value
                for signature, value in retry_state.recovery.items()
                if signature in recovery_signatures_at_start
            },
            exclude_ids=current_tool_ids,
        ),
        *tool_records,
    ]
    return AgentRunResult(
        agent=definition.name,
        data=data,
        raw_text=final_text,
        usage=total_usage,
        model=final_model,
        metadata={
            "model_profile": definition.model_profile,
            "budget": context.budget.snapshot(),
            "execution": definition.execution,
            "write_policy": definition.write_policy,
            "source": definition.source,
            "task_id": context.task_id,
            "prompt": context.prompt_bundle.diagnostics,
            "tool_calls": committed_tool_records,
            "media_inputs": media_inputs.queue.snapshot(),
            "response_ids": response_ids,
            "tool_argument_retries": tool_argument_retry_count,
            "structured_output_transport": (
                "tool" if final_data is not None else "text"
            ),
        },
    )
