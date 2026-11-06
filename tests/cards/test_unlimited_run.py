"""Long tasks remain one integration, with explicit cancellation and real receipts."""
import threading

import pytest

from run.cards import CardStore
from run.engine import iter_request_events
from run.tools import ToolRegistry
from tests.cards.test_runtime import call, definition, response, root, run


@pytest.mark.parametrize("batches", [[1] * 340, [96, 12]])
def test_main_run_exceeds_old_tool_budget_without_new_user_turns(root, batches):
    requests, executed = [], []
    total = sum(batches)

    def work(index):
        executed.append(index)
        return {"ok": True, "index": index}

    class Provider:
        def create(self, request):
            index = len(requests)
            requests.append(request)
            start = sum(batches[:index])
            calls = [call(call_id=f"callid_{n}", index=n)
                     for n in range(start, start + batches[index])] if index < len(batches) else []
            return response(request, "" if calls else "all work completed", calls)

    events = list(run(root, Provider(), tools=[definition(root, work)]))
    assert events[-1].type == "done" and events[-1].metadata["status"] == "completed", events[-1]
    assert events[-1].metadata["tool_calls"] == total
    assert sorted(executed) == list(range(total))
    assert len(requests) == len(batches) + 1
    assert len({r.metadata["integration_id"] for r in requests}) == 1
    store = CardStore(root, "alice")
    cards = list(store.raw_page("web", "s1", limit=500))
    cards.extend(store.raw_page("web", "s1", after_seq=cards[-1].seq, limit=500))
    assert len([c for c in cards if c.raw_kind == "user_text"]) == 1
    assert len([c for c in cards if c.raw_kind == "agent_text" and c.raw_level == "final"]) == 1
    assert not any("tool_budget_exhausted" in c.raw_payload_json for c in cards)


def test_long_main_run_still_honors_cancel(root):
    cancel = threading.Event()
    executed = []

    def work():
        executed.append(1)
        cancel.set()
        return {"ok": True}

    class Provider:
        def create(self, request):
            return response(request, "", [call()])

    tool = definition(root, work)
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "s1", "prompt": "work"},
        root=root, provider_factory=lambda _: Provider(),
        tool_registry_factory=lambda *_: ToolRegistry({tool.name: tool}), cancel_event=cancel,
    ))
    assert events[-1].metadata["status"] == "cancelled"
    assert executed == [1]
    with CardStore(root, "alice").db() as db:
        assert db.execute("SELECT count(*) FROM card_integrations").fetchone()[0] == 1
