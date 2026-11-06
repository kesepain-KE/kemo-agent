"""Bounded source pages and durable continuation of model extraction."""
from __future__ import annotations

from copy import deepcopy
from itertools import groupby
from provider.tokenizer import count_value

from run.memory.work import WorkLedger, digest
from run.agents import AgentOutputError


class MemoryWorkPending(AgentOutputError):
    """Durable budget pause, not malformed model output to retry/split."""

INPUT_TOKEN_BUDGET = 12000
MAX_PAGES_PER_RUN = 6


def source_pages(rounds: list[dict], token_budget=INPUT_TOKEN_BUDGET) -> list[list[dict]]:
    """Pack integrations first, then split huge prose losslessly by tokens.

    RAW evidence IDs survive paging. Repeated user context is context_only:
    useful to understand a late answer, never an additional observation.
    """
    if token_budget < 128:
        raise AgentOutputError("记忆分页元数据已占满 token 预算")
    pages, page = [], []
    groups = [list(group) for _, group in groupby(
        rounds, key=lambda item: item.get("integration_id") or ("round", item["round"]))]
    for group in groups:
        if count_value(group) <= token_budget:
            if page and count_value([*page, *group]) > token_budget:
                pages.append(page)
                page = []
            page.extend(deepcopy(group))
            continue
        if page:
            pages.append(page)
            page = []
        context = []
        for item in group:
            for message in item.get("messages", []):
                body, offset = message["content"], 0
                prefix = context if message["role"] == "assistant" else []
                while offset < len(body):
                    low, high, end, candidate = offset + 1, len(body), 0, []
                    while low <= high:
                        middle = (low + high) // 2
                        record = {**item, "messages": [{**message, "content": body[offset:middle]}],
                                  "segment": {"offset": offset, "end": middle, "total": len(body)}}
                        probe = [*prefix, record]
                        if count_value(probe) <= token_budget:
                            end, candidate, low = middle, probe, middle + 1
                        else:
                            high = middle - 1
                    if not end:
                        raise AgentOutputError("单条记忆证据的元数据无法装入 token 预算")
                    pages.append(deepcopy(candidate))
                    offset = end
                if message["role"] == "user":
                    length = min(len(body), max(32, token_budget // 16))
                    context.append({**item, "context_only": True,
                                    "messages": [{**message, "content": body[:length]}]})
                    while context and count_value(context) > token_budget // 4:
                        context.pop(0)
    if page:
        pages.append(page)
    return pages or [[]]


def run_extraction_pages(context, input_data):
    """Never mark a saturated model response as a fully consumed source."""
    from run.agents import AgentRunResult
    from run.memory import memory_extraction_candidate_limit, memory_extraction_input_tokens
    from run.conversation import new_usage_total

    runner = context.runner
    limit = memory_extraction_candidate_limit(runner.config, len(input_data["rounds"]))
    ledger = WorkLedger(runner.root, runner.user)
    input_budget = memory_extraction_input_tokens(runner.config)
    # Page geometry is pinned in the durable job; changing config mid-resume
    # must not reinterpret an old numeric cursor using different page lengths.
    key = "extract:" + digest({"source": input_data, "paging": "narrative-tokens-v1"})
    initial = {"page": 0, "candidates": [], "metadata": {"tool_calls": []},
               "source": input_data, "usage": new_usage_total(), "input_budget": input_budget}
    job = ledger.enqueue("extraction", key, initial)
    state = job["payload"]
    if job["status"] == "completed":
        return AgentRunResult(agent="self_improve", data={"candidates": state["candidates"]},
                              raw_text="", usage={}, model=state.get("model", ""),
                              metadata=deepcopy(state["metadata"]))
    with ledger.lease(key) as token:
        state = ledger.require(key)["payload"]
        return _run_pages(context, input_data, ledger, key, state, limit, token)


def _run_pages(context, input_data, ledger, key, state, limit, token):
    from run.agents import AgentRunResult
    from run.conversation import new_usage_total, record_provider_request, usage_from_dict
    usage = new_usage_total()
    input_budget = state["input_budget"]
    base_input = {k: v for k, v in input_data.items() if k != "rounds"}
    if isinstance(base_input.get("source"), dict):
        base_input["source"] = {k: v for k, v in base_input["source"].items() if k != "evidence_dates"}
    # Budget includes source metadata and continuation, not just visible prose.
    page_budget = input_budget - count_value(base_input) - min(1024, input_budget // 3)
    pages = source_pages(input_data["rounds"], page_budget)
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
                if count_value([summary, *returned]) > min(512, input_budget // 12):
                    break
                returned.insert(0, summary)
            page_input = {**base_input, "rounds": pages[index],
                          "extraction_page": index, "candidate_limit": limit,
                          "continuation": {
                              "already_returned": returned,
                              "instruction": "围绕个人主题整合偏好、原因与近况，不逐句建档；仅补充未返回主题，禁止重复。context_only 只供理解，不作新证据。has_more 表示本页仍有未整理主题。"}}
            if count_value(page_input) > input_budget:
                raise AgentOutputError("记忆提取完整正文输入超过 token 预算，未推进游标")
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
            ledger.save(key, state, completed=state["page"] >= len(pages), lease_token=token)
        if state["page"] < len(pages):
            raise MemoryWorkPending("记忆提取待办已保存；本次分页预算用尽，后续维护继续，不推进源轮次")
        result = AgentRunResult(agent="self_improve", data={"candidates": state["candidates"]},
                                raw_text="", usage=usage, model=state.get("model", ""),
                                metadata=state["metadata"])
        result.metadata["extraction_work_key"] = key
        return result
    except Exception as exc:
        ledger.save(key, state, error=type(exc).__name__, lease_token=token)
        raise
