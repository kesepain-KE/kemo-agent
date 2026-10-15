"""Apply a bounded portion of candidates, retaining every remaining item."""
from __future__ import annotations

from run.memory.work import WorkLedger, digest


def apply_candidate_work(store, candidates, source, operation_id=None):
    from run.memory import memory_extraction_candidate_limit
    from run.memory.evidence import contains_sensitive_payload
    if contains_sensitive_payload(source):
        raise ValueError("记忆待办来源元数据包含疑似敏感凭据，禁止持久化")
    safe_candidates = [c for c in candidates if not contains_sensitive_payload(c)]
    key = "candidate:" + (operation_id or digest([source, candidates]))
    ledger = WorkLedger(store.root, store.user)
    job = ledger.enqueue("candidate", key, {"candidates": safe_candidates, "offset": 0,
                                         "rejected_sensitive": len(candidates) - len(safe_candidates),
                                         "results": []}, source)
    state = job["payload"]
    if job["status"] != "completed":
        start = state["offset"]
        stop = min(len(state["candidates"]),
                   start + memory_extraction_candidate_limit(store.config, 1))
        if start < stop:
            result = store.upsert_candidates(
                state["candidates"][start:stop], source=job["source"],
                operation_id="work_" + digest([key, start, stop]))
            state["results"].append(result)
        state["offset"] = stop
        ledger.save(key, state, completed=stop >= len(state["candidates"]))
    if state["offset"] < len(state["candidates"]):
        return {"status": "failed", "candidate_count": len(candidates),
                "error": {"message": "候选待办已保存，后续批次继续，源历史游标尚未推进",
                          "exception_type": "MemoryWorkPending", "work_pending": True},
                "pending_candidates": len(state["candidates"]) - state["offset"]}
    combined = {}
    for result in state["results"]:
        for name, value in result.items():
            if isinstance(value, list):
                combined.setdefault(name, []).extend(value)
            elif name == "rejected":
                combined[name] = combined.get(name, 0) + value
    if not state["results"]:
        combined = store.upsert_candidates([], source=source, operation_id=operation_id)
    combined["rejected"] = combined.get("rejected", 0) + state.get("rejected_sensitive", 0)
    if state.get("rejected_sensitive"):
        combined.setdefault("rejection_reasons", []).append({"reason": "sensitive_credential"})
    combined.update(operation_id=operation_id, replayed=job["status"] == "completed")
    return {"status": "completed", "persisted": combined}
