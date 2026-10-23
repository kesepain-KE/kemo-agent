"""Bounded source pages and durable continuation of model extraction."""
from __future__ import annotations

from copy import deepcopy
import json
from types import SimpleNamespace

from run.memory.work import WorkLedger, digest
from run.agents import AgentOutputError


class MemoryWorkPending(AgentOutputError):
    """Durable budget pause, not malformed model output to retry/split."""

# Conservative Unicode character budget: even CJK fits within a small token
# window. Never discard an overlong message: split it into overlapping pages.
INPUT_CHAR_BUDGET = 12000
MESSAGE_CHAR_BUDGET = 5000
MAX_PAGES_PER_RUN = 6


def source_pages(rounds: list[dict]) -> list[list[dict]]:
    pages, page, size = [], [], 0
    for item in rounds:
        for message in item.get("messages", []):
            body = message["content"]
            for offset in range(0, len(body), MESSAGE_CHAR_BUDGET - 300):
                segment = body[offset:offset + MESSAGE_CHAR_BUDGET]
                if size + len(segment) > INPUT_CHAR_BUDGET and page:
                    pages.append(page)
                    page, size = [], 0
                page.append({"round": item["round"], "messages": [
                    {**message, "content": segment}]})
                size += len(segment)
    if page:
        pages.append(page)
    return pages or [[]]


def run_extraction_pages(context, input_data):
    """Never mark a saturated model response as a fully consumed source."""
    from run.agents import AgentOutputError, AgentRunResult
    from run.memory import memory_extraction_candidate_limit
    from run.conversation import new_usage_total, record_provider_request, usage_from_dict

    runner = getattr(context, "runner", SimpleNamespace(config={}))
    limit = memory_extraction_candidate_limit(getattr(runner, "config", {}), len(input_data["rounds"]))
    ledger = (WorkLedger(runner.root, runner.user)
              if hasattr(runner, "root") and hasattr(runner, "user")
              and any(item.get("messages") for item in input_data["rounds"]) else None)
    key = "extract:" + digest(input_data)
    initial = {"page": 0, "candidates": [], "metadata": {"tool_calls": []},
               "source": input_data, "usage": new_usage_total()}
    job = ledger.enqueue("extraction", key, initial) if ledger else None
    state = job["payload"] if job else initial
    if job and job["status"] == "completed":
        return AgentRunResult(agent="self_improve", data={"candidates": state["candidates"]},
                              raw_text="", usage={}, model=state.get("model", ""),
                              metadata=deepcopy(state["metadata"]))
    if ledger:
        with ledger.lease(key) as token:
            state = ledger.require(key)["payload"]
            return _run_pages(context, input_data, ledger, key, state, limit, token)
    return _run_pages(context, input_data, None, key, state, limit, None)


def _run_pages(context, input_data, ledger, key, state, limit, token):
    from run.agents import AgentRunResult
    from run.conversation import new_usage_total, record_provider_request, usage_from_dict
    usage = new_usage_total()
    pages = source_pages(input_data["rounds"])
    try:
        for _ in range(MAX_PAGES_PER_RUN):
            index = state["page"]
            if index >= len(pages):
                break
            already = [c for c in state["candidates"] if isinstance(c, dict)]
            returned = []
            for c in reversed(already):
                summary = {"filename": str(c.get("filename") or "")[:70],
                           "evidence": str(c.get("evidence") or "")[:120],
                           "evidence_rounds": c.get("evidence_rounds")}
                if len(json.dumps([summary, *returned], ensure_ascii=False)) > 4000:
                    break
                returned.insert(0, summary)
            page_input = {**input_data, "rounds": pages[index],
                          "extraction_page": index, "candidate_limit": limit,
                          "continuation": {
                              "already_returned": returned,
                              "instruction": "继续枚举未返回的独立事实；禁止重复。has_more 表示本页仍有未返回的事实。"}}
            result = context.run_model(page_input)
            candidates = result.data.get("candidates")
            if not isinstance(candidates, list):
                raise AgentOutputError("self_improve 输出缺少 candidates 数组")
            identities = {digest(c) for c in state["candidates"]}
            from run.memory.evidence import contains_sensitive_payload
            new = []
            for candidate in candidates:
                if contains_sensitive_payload(candidate):
                    continue
                identity = digest(candidate)
                if identity not in identities:
                    new.append(deepcopy(candidate))
                    identities.add(identity)
            saturated = len(candidates) >= limit or result.data.get("has_more") is True
            if saturated and not new:
                raise AgentOutputError("记忆分页没有前进，已保留待办与源轮次，禁止推进历史游标")
            state["candidates"].extend(new)
            state["metadata"]["tool_calls"].extend(
                call for call in result.metadata.get("tool_calls") or []
                if not contains_sensitive_payload(call))
            record_provider_request(state["usage"], usage_from_dict(result.usage))
            record_provider_request(usage, usage_from_dict(result.usage))
            state["model"] = result.model
            if not saturated:
                state["page"] += 1
            if ledger:
                ledger.save(key, state, completed=state["page"] >= len(pages), lease_token=token)
        if state["page"] < len(pages):
            raise MemoryWorkPending("记忆提取待办已保存；本次分页预算用尽，后续维护继续，不推进源轮次")
        result = AgentRunResult(agent="self_improve", data={"candidates": state["candidates"]},
                                raw_text="", usage=usage, model=state.get("model", ""),
                                metadata=state["metadata"])
        result.metadata["extraction_work_key"] = key
        return result
    except Exception as exc:
        if ledger:
            ledger.save(key, state, error=type(exc).__name__, lease_token=token)
        raise
