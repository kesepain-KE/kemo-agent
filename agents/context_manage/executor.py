from run.agent_runner import AgentOutputError
from run.memory_pipeline import extract_compressed_round_memory


def _repair_input(input_data, error):
    raw_text = str(getattr(error, "raw_text", "") or "").strip()
    return {
        **input_data,
        "_format_repair": {
            "required": True,
            "previous_error": str(error),
            "previous_output": (
                raw_text[-4000:]
                if raw_text
                else "（上一轮没有生成可解析的最终正文）"
            ),
            "instruction": (
                "上一轮摘要未通过 JSON 格式或 Schema 校验。重新完成同一摘要任务，"
                "只输出一个合法 JSON 对象；必须包含 facts、requirements、decisions、"
                "unfinished、tool_results、entities、narrative 七个字段，不输出 Markdown、"
                "思考或解释。保持内容简洁，确保 JSON 完整闭合。"
            ),
        },
    }


def _run_model_with_repair(context, input_data):
    try:
        return context.run_model(input_data)
    except AgentOutputError as first_error:
        try:
            result = context.run_model(_repair_input(input_data, first_error))
        except AgentOutputError as repair_error:
            raw_text = repair_error.raw_text or first_error.raw_text
            raise AgentOutputError(
                f"context_manage JSON 修复失败：{repair_error}",
                raw_text=raw_text,
            ) from repair_error
        result.metadata["format_repaired"] = True
        result.metadata["format_error"] = str(first_error)
        return result


def _merge_usage(target, source):
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
    target["estimated"] = bool(
        target.get("estimated", False) or source.get("estimated", False)
    )


def execute(context, input_data):
    trigger = str(input_data.get("trigger") or "")
    rounds = input_data.get("rounds")
    memory_result = None
    model_input = dict(input_data)
    has_memory_payload = isinstance(rounds, list) and any(
        isinstance(item, dict) and (item.get("messages") or item.get("tools"))
        for item in rounds
    )
    if (
        trigger in {"round_limit", "token_limit", "manual", "api_context_length"}
        and has_memory_payload
        and not bool(input_data.get("skip_memory_extraction", False))
    ):
        memory_result = extract_compressed_round_memory(
            root=context.runner.root,
            user=context.runner.user,
            config=context.runner.config,
            rounds=rounds,
            trigger=trigger,
            agent_runner=context.runner,
            cancel_event=context.cancel_event,
        )
        model_input["memory_extraction"] = {
            "completed": True,
            "candidate_count": len(memory_result.data.get("candidates") or []),
        }
    result = _run_model_with_repair(context, model_input)
    if memory_result is not None:
        _merge_usage(result.usage, memory_result.usage)
        result.metadata["memory_extraction"] = {
            "completed": True,
            "candidate_count": len(memory_result.data.get("candidates") or []),
            "agent": memory_result.agent,
        }
    return result
