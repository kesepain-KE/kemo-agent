from run.memory_pipeline import extract_compressed_round_memory


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
    result = context.run_model(model_input)
    if memory_result is not None:
        _merge_usage(result.usage, memory_result.usage)
        result.metadata["memory_extraction"] = {
            "completed": True,
            "candidate_count": len(memory_result.data.get("candidates") or []),
            "agent": memory_result.agent,
        }
    return result
