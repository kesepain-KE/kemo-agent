"""Bounded memory recovery from CardStream RAW sequence claims."""
from run.agents import AgentRunner
from run.config import load_config
from run.history import claim_pending_memory
from run.memory import extract_card_memory, memory_extraction_mode, memory_extraction_input_tokens


def recover_pending_memory(scheduler, user):
    config = load_config(user, scheduler.root)
    mode = memory_extraction_mode(config)
    result = {"mode": mode, "claimed": 0, "processed": [], "failed": [], "deferred": []}
    if mode == "disabled":
        return result
    statuses = {"queued", "failed", "processing"}
    if mode in {"background", "on_commit"}:
        statuses.add("pending")
    claim = claim_pending_memory(scheduler.root, user,
                                 input_tokens=memory_extraction_input_tokens(config), statuses=statuses)
    if claim is None:
        return result
    result["claimed"] = claim["memory_claim_end_seq"] - claim["memory_claim_start_seq"] + 1
    outcome = extract_card_memory(
        root=scheduler.root, user=user, source=claim["source"], session_id=claim["session_id"],
        config=config, agent_runner=AgentRunner(scheduler.root, user, config=config,
                                               provider_factory=scheduler.provider_factory),
        cancel_event=scheduler._stop_event, claim=claim,
    )
    error = outcome.get("error") or {}
    if error.get("capacity_blocked") or error.get("work_pending"):
        result["deferred"].append(outcome)
    else:
        result["processed" if outcome.get("status") == "completed" else "failed"].append(outcome)
    return result
