"""Sub-agent model loop extracted from :mod:`run.agents.runner`.

The AgentRunner method remains the public compatibility entry point; dependencies
are resolved lazily so factories and tests can continue to patch runner globals.
"""

from __future__ import annotations

def run_model(
    agent_runner,
    context: AgentExecutionContext,
    input_data: dict[str, Any],
    *,
    retry_state: _AgentRetryState | None = None,
    attempt: int = 1,
    max_attempts: int = 1,
) -> AgentRunResult:
    import importlib
    _runner = importlib.import_module("run.agents.runner")
    AgentCancelledError = _runner.AgentCancelledError
    AgentExecutionContext = _runner.AgentExecutionContext
    AgentInputError = _runner.AgentInputError
    AgentOutputError = _runner.AgentOutputError
    AgentProviderError = _runner.AgentProviderError
    AgentRunError = _runner.AgentRunError
    AgentRunResult = _runner.AgentRunResult
    AgentToolRetryError = _runner.AgentToolRetryError
    Any = _runner.Any
    ConsecutiveIdenticalToolCallTracker = _runner.ConsecutiveIdenticalToolCallTracker
    ConsecutiveToolFailureTracker = _runner.ConsecutiveToolFailureTracker
    JsonContent = _runner.JsonContent
    KemoRequest = _runner.KemoRequest
    KemoResponse = _runner.KemoResponse
    MessageItem = _runner.MessageItem
    MessageRole = _runner.MessageRole
    ProviderCongestionError = _runner.ProviderCongestionError
    ReasoningConfig = _runner.ReasoningConfig
    ResponseStatus = _runner.ResponseStatus
    ToolCallItem = _runner.ToolCallItem
    ToolDefinition = _runner.ToolDefinition
    ToolResultItem = _runner.ToolResultItem
    ToolResultTooLargeError = _runner.ToolResultTooLargeError
    _AgentRetryState = _runner._AgentRetryState
    _STRUCTURED_OUTPUT_TOOL_NAME = _runner._STRUCTURED_OUTPUT_TOOL_NAME
    _agent_recovery_items = _runner._agent_recovery_items
    _agent_recovery_records = _runner._agent_recovery_records
    _agent_tool_failure_is_retryable = _runner._agent_tool_failure_is_retryable
    _agent_tool_result_reuse_allowed = _runner._agent_tool_result_reuse_allowed
    _parse_json_object = _runner._parse_json_object
    _record_agent_recovery = _runner._record_agent_recovery
    _response_items_for_next_request = _runner._response_items_for_next_request
    _safe_int = _runner._safe_int
    copy = _runner.copy
    effective_knowledge_scopes = _runner.effective_knowledge_scopes
    execute_tool = _runner.execute_tool
    field = _runner.field
    invalid_tool_name = _runner.invalid_tool_name
    json = _runner.json
    provider_request_slot = _runner.provider_request_slot
    resolve_agent_provider_config = _runner.resolve_agent_provider_config
    resolve_reasoning_selection = _runner.resolve_reasoning_selection
    response_invalid_tool_arguments_error = _runner.response_invalid_tool_arguments_error
    safe_provider_message = _runner.safe_provider_message
    system_prompt_with_tool_argument_repair = _runner.system_prompt_with_tool_argument_repair
    text_from_content = _runner.text_from_content
    tool_call_signature = _runner.tool_call_signature
    uuid = _runner.uuid
    validate_tool_call_batch = _runner.validate_tool_call_batch
    validate_json_schema = _runner.validate_json_schema
    retry_state = retry_state or _AgentRetryState()
    definition = context.definition
    runtime = resolve_agent_provider_config(
        agent_runner.config,
        definition,
        model_override=context.model_override,
    )
    provider = agent_runner.provider_factory(runtime)
    reasoning_selection = resolve_reasoning_selection(
        agent_runner.config,
        runtime,
        provider,
        model=runtime["model"],
        cancel_event=context.cancel_event,
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
    items: list[Any] = [
        MessageItem.text(
            MessageRole.USER,
            json.dumps(input_data, ensure_ascii=False, sort_keys=True),
            item_id=f"msg_{uuid.uuid4().hex}",
        )
    ]
    items.extend(_agent_recovery_items(retry_state.recovery))
    total_usage: dict[str, Any] = copy.deepcopy(retry_state.usage)
    tool_records: list[dict[str, Any]] = []
    final_text = ""
    final_data: dict[str, Any] | None = None
    final_model = runtime["model"]
    response_ids: list[str] = list(retry_state.response_ids)
    parent_request_id: str | None = None
    tool_config = agent_runner.config.get("tools") or {}
    raw_global_tool_calls = tool_config.get("max_iterations", 80)
    if (
        isinstance(raw_global_tool_calls, bool)
        or not isinstance(raw_global_tool_calls, int)
        or raw_global_tool_calls < 1
    ):
        raise AgentRunError("tools.max_iterations 必须是正整数")
    max_tool_calls = min(
        raw_global_tool_calls,
        definition.capabilities.max_tool_iterations,
    )
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
    max_provider_iterations = max_tool_calls + 1
    processed_tool_calls = 0
    tool_timeout = float(tool_config.get("timeout", 240))
    agent_timeout = (agent_runner.config.get("agent_runtime") or {}).get(
        "default_timeout", 600
    )
    raw_identical_call_limit = tool_config.get(
        "consecutive_identical_call_limit", 8
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
    seen_calls: dict[str, dict[str, Any]] = {
        signature: copy.deepcopy(value["result"])
        for signature, value in retry_state.recovery.items()
        if value.get("replay_policy") == "reuse"
        and isinstance(value.get("result"), dict)
    }
    blocked_recovery: dict[str, dict[str, Any]] = {
        signature: value
        for signature, value in retry_state.recovery.items()
        if value.get("replay_policy") == "blocked"
    }
    tool_argument_retry_count = 0
    for iteration in range(1, max_provider_iterations + 1):
        if context.cancel_event.is_set():
            raise AgentCancelledError(f"子代理 {definition.name} 已取消")
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
        invalid_tool_arguments_retries = 0
        repair_tool_name = ""
        while True:
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
            try:
                with provider_request_slot(
                    agent_runner.config,
                    cancel_event=context.cancel_event,
                ):
                    response = provider.create(
                        KemoRequest(
                            request_id=request_id,
                            parent_request_id=parent_request_id,
                            attempt=attempt + invalid_tool_arguments_retries,
                            model=runtime["model"],
                            stream=False,
                            system_prompt=request_system,
                            input=list(items),
                            tools=tool_definitions,
                            generation={"max_output_tokens": context.max_tokens},
                            reasoning=(
                                ReasoningConfig(
                                    enabled=True,
                                    effort=reasoning_selection.effort,
                                    return_mode="content",
                                    context="auto",
                                )
                                if reasoning_selection.enabled
                                and reasoning_selection.effort
                                else None
                            ),
                            provider_options=(
                                {"reasoning_effort": reasoning_selection.effort}
                                if reasoning_selection.enabled
                                and reasoning_selection.effort
                                else {}
                            ),
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
            if not isinstance(response, KemoResponse):
                raise AgentRunError("Provider create() 必须返回 KemoResponse")
            response_ids.append(response.id)
            retry_state.response_ids = list(response_ids)
            parent_request_id = parent_request_id or request_id
            agent_runner._merge_usage(total_usage, agent_runner._usage_dict(response.usage))
            retry_state.usage = copy.deepcopy(total_usage)
            final_model = response.model or runtime["model"]
            invalid_error = response_invalid_tool_arguments_error(response)
            if invalid_error is None:
                declared_tool_schemas: dict[str, dict[str, Any]] = {}
                for raw_schema in context.tool_registry.schemas():
                    function = (
                        raw_schema.get("function")
                        if isinstance(raw_schema.get("function"), dict)
                        else raw_schema
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
        if response.status not in {
            ResponseStatus.COMPLETED,
            ResponseStatus.REQUIRES_ACTION,
        }:
            if response.error is not None:
                provider_error = response.error
                message = safe_provider_message(
                    provider_error.message,
                    "Provider 响应失败",
                )
                retryable: bool | None = None
                fields_set = getattr(provider_error, "model_fields_set", set())
                if "retryable" in fields_set:
                    retryable = provider_error.retryable
                elif isinstance(provider_error.details, dict):
                    declared = provider_error.details.get("retryable")
                    if isinstance(declared, bool):
                        retryable = declared
                raise AgentProviderError(
                    f"子代理 Provider 响应失败：{message}",
                    category=provider_error.type or "provider_error",
                    code=provider_error.code,
                    status_code=provider_error.provider_status,
                    retryable=retryable,
                    retry_after_ms=provider_error.retry_after_ms,
                )
            raise AgentProviderError(
                f"子代理 Provider 响应失败：{response.status}",
                category=(
                    "provider_incomplete"
                    if response.status == ResponseStatus.INCOMPLETE
                    else "provider_error"
                ),
                retryable=response.status != ResponseStatus.CANCELLED,
            )
        normalized_output = _response_items_for_next_request(response.output)
        calls = [
            item for item in normalized_output if isinstance(item, ToolCallItem)
        ]
        messages = [
            item for item in normalized_output if isinstance(item, MessageItem)
        ]
        items.extend(normalized_output)
        structured_calls = [
            call for call in calls if call.name == _STRUCTURED_OUTPUT_TOOL_NAME
        ]
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
        retryable_tool_failure: AgentToolRetryError | None = None
        for call in calls:
            if processed_tool_calls >= max_tool_calls:
                raise AgentRunError(
                    f"子代理 {definition.name} 已达到最大工具调用次数 {max_tool_calls}"
                )
            processed_tool_calls += 1
            signature = tool_call_signature(call.name, call.arguments)
            reuse_allowed = _agent_tool_result_reuse_allowed(
                call.name,
                call.arguments,
            )
            duplicate = False
            identical_call_count = identical_calls.record(call.name, call.arguments)
            if identical_calls.is_blocked(identical_call_count):
                payload = {
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
                blocked_result = retry_state.recovery.get(signature)
                if (
                    not isinstance(blocked_result, dict)
                    or blocked_result.get("replay_policy") != "blocked"
                ):
                    blocked_result = None
                duplicate = reuse_allowed and signature in seen_calls
                if blocked_result is not None:
                    payload = copy.deepcopy(blocked_result.get("result") or {})
                    status = "retry_reuse_blocked"
                    duplicate = True
                elif duplicate:
                    payload = copy.deepcopy(seen_calls[signature])
                    status = "duplicate_reused"
                else:
                    try:
                        tool = context.tool_registry.get(call.name)
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
                                "agent_trigger": input_data.get("trigger"),
                                "tool_timeout": tool_timeout,
                                "agent_timeout": agent_timeout,
                                "knowledge_scopes": list(
                                    effective_knowledge_scopes(
                                        definition,
                                        agent_runner.config,
                                    )
                                ),
                            },
                            timeout=tool_timeout,
                            cancel_event=context.cancel_event,
                        )
                        payload = {"ok": True, "result": value}
                        status = "completed"
                    except ToolResultTooLargeError as exc:
                        payload = {"ok": False, "error": exc.error_payload()}
                        status = "result_too_large"
                    except Exception as exc:
                        error = {
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
                                error[field] = value
                            elif isinstance(value, str) and value.strip():
                                error[field] = value.strip()[:160]
                        retryable = getattr(exc, "retryable", None)
                        if isinstance(retryable, bool) and getattr(
                            exc,
                            "retryable_declared",
                            True,
                        ):
                            error["retryable"] = retryable
                        payload = {
                            "ok": False,
                            "error": error,
                        }
                        if bool(getattr(exc, "still_running", False)):
                            failures.unavailable.add(call.name)
                            status = "timed_out_running"
                        else:
                            status = "failed"
                    if payload.get("ok") is True:
                        if reuse_allowed:
                            seen_calls[signature] = copy.deepcopy(payload)
                    else:
                        seen_calls.pop(signature, None)
                    failure_count = failures.record(
                        call.name,
                        succeeded=(
                            bool(payload.get("ok")) or status == "result_too_large"
                        ),
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
            _record_agent_recovery(retry_state, call, payload)
            items.append(
                ToolResultItem(
                    id=f"result_{uuid.uuid4().hex}",
                    call_id=call.call_id,
                    name=call.name,
                    is_error=not bool(payload.get("ok")),
                    content=[JsonContent(data=payload)],
                )
            )
            if (
                attempt < max_attempts
                and retryable_tool_failure is None
                and _agent_tool_failure_is_retryable(payload, status)
            ):
                error = payload.get("error")
                if not isinstance(error, dict):
                    error = {}
                retryable_tool_failure = AgentToolRetryError(
                    call.name,
                    status_code=_safe_int(error.get("status_code")),
                    retry_after_ms=_safe_int(error.get("retry_after_ms")),
                )
        if retryable_tool_failure is not None:
            raise retryable_tool_failure
    else:
        raise AgentRunError(f"子代理 {definition.name} 未生成最终输出")
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
    current_tool_ids = {
        str(record.get("id") or "")
        for record in tool_records
        if isinstance(record, dict)
    }
    committed_tool_records = [
        *_agent_recovery_records(
            retry_state.recovery,
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
            "execution": definition.execution,
            "write_policy": definition.write_policy,
            "source": definition.source,
            "task_id": context.task_id,
            "prompt": context.prompt_bundle.diagnostics,
            "tool_calls": committed_tool_records,
            "response_ids": response_ids,
            "tool_argument_retries": tool_argument_retry_count,
            "structured_output_transport": (
                "tool" if final_data is not None else "text"
            ),
        },
    )
