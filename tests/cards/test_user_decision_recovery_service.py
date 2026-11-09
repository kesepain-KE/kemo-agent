"""The Web recovery worker reclaims a confirmed lost native question."""
import threading
import time
from functools import partial

from run.cards import CardStore
from run.engine import iter_request_events
from run.tools.user_decisions import DecisionStore
from tests.cards.test_runtime import response, root  # noqa: F401
from tests.cards.test_user_decisions import registry, stored_question
from web.service import WebRunService


def test_web_recovery_resumes_pending_question_in_same_integration_and_records_final(root):
    store, integration, decision_id = stored_question(root)
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=2147483647,owner_token='lost'")
    invoked = threading.Event()

    class Provider:
        def create(self, request):
            invoked.set()
            assert "answered" in request.model_dump_json()
            return response(request, "恢复后真实完成")

    service = WebRunService(root, event_source=partial(
        iter_request_events, provider_factory=lambda _: Provider(),
        tool_registry_factory=lambda *_: registry(root),
    ))
    service.recover_decision_runs()
    try:
        assert "run_decision_test" in service._active_runs
        time.sleep(.1)
        assert not invoked.is_set()
        service.recover_decision_runs()
        assert len(service._active_runs) == 1
        service.answer_decision("alice", "s1", decision_id, option_id="sqlite")
        assert invoked.wait(5)
        deadline = time.monotonic() + 5
        while service._active_runs and time.monotonic() < deadline:
            time.sleep(.01)
        assert not service._active_runs
        assert DecisionStore(store).list("web", "s1") == []
        cards = store.raw_page("web", "s1")
        assert len([card for card in cards if card.raw_kind == "tool_call"]) == 1
        assert cards[-1].raw_level == "final" and cards[-1].raw_payload["text"] == "恢复后真实完成"
        assert cards[-1].integration_id == integration.id
    finally:
        for active in list(service._active_runs.values()):
            active.cancel_event.set()
