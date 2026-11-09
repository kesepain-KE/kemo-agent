"""Real CardStream execution, HTTP answers, races and interrupted wait recovery."""
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from plugins.user_decision import tool as decision_plugin
from run.cards import CardConflictError, CardStore, IntegrationStore
from run.cards.execution import _OWNER
from run.config import load_config
from run.engine import iter_request_events
from run.history import delete_session
from run.tools import ToolRegistry, apply_runtime_tool_policy, discover_tools
from run.tools.user_decisions import DecisionClosedError, DecisionStore, TIMEOUT_SECONDS
from tests.cards.test_runtime import call, definition, response, root  # noqa: F401
from web.app import create_app
from web.errors import ConflictError, InvalidRequestError
from web.service import WebRunService


QUESTION = {
    "action": "ask", "title": "选择数据库", "question": "更倾向哪个方案？",
    "options": [{"id": "sqlite", "label": "SQLite", "recommended": True},
                {"id": "pg", "label": "PostgreSQL", "description": "独立数据库"}],
}


def enable(root, enabled=True):
    path = root / "users/alice/user_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config["interactive_decisions"] = {"enabled": enabled}
    path.write_text(json.dumps(config), "utf-8")


def registry(root):
    return ToolRegistry({
        "user_decision": definition(root, decision_plugin.run, name="user_decision"),
    })


def pending(root):
    return DecisionStore(CardStore(root, "alice")).list("web", "s1")


def await_question(root):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        items = pending(root)
        if items:
            return items[0]
        time.sleep(.02)
    raise AssertionError("question was not created")


def execute(root, provider, *, cancel_event=None, **request):
    return list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "s1", "prompt": "设计数据库", **request},
        root=root, provider_factory=lambda _: provider, tool_registry_factory=lambda *_: registry(root),
        cancel_event=cancel_event,
    ))


def stored_question(root, mode="wait"):
    enable(root)
    store = CardStore(root, "alice")
    integration = IntegrationStore(store, "web", "s1", "question-integration")
    integration.start()
    integration.checkpoint_decisions({"run_id": "run_decision_test"})
    execution, _ = integration.register_tool("question-call", {
        "name": "user_decision", "arguments": {**QUESTION, "mode": mode},
    })
    return store, integration, execution["execution_id"]


def test_switch_is_user_owned_default_off_and_filters_the_schema(root):
    tools = registry(root)
    assert not apply_runtime_tool_policy(tools, load_config("alice", root)).tools
    enable(root)
    assert "user_decision" in apply_runtime_tool_policy(tools, load_config("alice", root)).tools
    path = root / "users/alice/user_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config.pop("interactive_decisions")
    path.write_text(json.dumps(config), "utf-8")
    global_path = root / "config/global_config.yaml"
    config = json.loads(global_path.read_text("utf-8"))
    config["interactive_decisions"] = {"enabled": True}
    global_path.write_text(json.dumps(config), "utf-8")
    assert "user_decision" not in apply_runtime_tool_policy(tools, load_config("alice", root)).tools
    # Check the distributed declaration and runtime filtering together.
    from pathlib import Path
    assert "user_decision" in discover_tools(Path(__file__).resolve().parents[2], "alice").tools


@pytest.mark.parametrize("custom", [False, True])
def test_wait_releases_tool_worker_and_continues_with_the_actual_http_answer(root, custom):
    enable(root)
    requests = []
    cancel = threading.Event()

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[call("user_decision", **QUESTION)])
            assert "answered" in request.model_dump_json()
            assert "自定义的方案" in request.model_dump_json() if custom else "PostgreSQL" in request.model_dump_json()
            return response(request, "已按选择完成")

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(execute, root, Provider(), cancel_event=cancel)
        try:
            question = await_question(root)
            time.sleep(.1)
            assert len(requests) == 1 and not future.done()
            assert not any(thread.name.startswith("card-tool-") for thread in threading.enumerate())
            service = WebRunService(root)
            client = TestClient(create_app(root=root, service=service), client=("127.0.0.1", 50000))
            route = f"/api/users/alice/sessions/s1/decisions/{question['decision_id']}/answer"
            # Scope tampering and malformed answers never settle the question.
            assert client.post(route + "?source=app", json={"option_id": "pg"}).status_code == 404
            assert client.post(route, json={"option_id": "pg", "text": "both"}).status_code == 400
            result = client.post(route, json={"text": "自定义的方案"} if custom else {"option_id": "pg"})
            assert result.status_code == 200, result.text
            assert client.post(route, json={"option_id": "sqlite"}).status_code == 409
            events = future.result(8)
        finally:
            cancel.set()
    assert events[-1].metadata["status"] == "completed"
    assert pending(root) == []
    assert len(requests) == 2
    answers = [card for card in CardStore(root, "alice").raw_page("web", "s1")
               if card.raw_kind == "tool_result" and card.raw_payload["result"].get("status") == "answered"]
    assert len(answers) == 1 and answers[0].tool_call_id == "callid_a"


def test_async_work_can_close_its_own_question_and_deliver_without_waiting(root):
    enable(root)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[call("user_decision", **QUESTION, mode="async")])
            if len(requests) == 2:
                question = pending(root)[0]
                return response(request, "SQLite 已足够", calls=[call(
                    "user_decision", "callid_close", action="close",
                    decision_id=question["decision_id"], resolution="实际数据量适合 SQLite",
                )])
            assert "resolved_by_agent" in request.model_dump_json()
            return response(request, "采用 SQLite 完成")

    events = execute(root, Provider())
    assert events[-1].metadata["status"] == "completed", events[-1].to_dict()
    assert len(requests) == 3
    assert pending(root) == []
    with CardStore(root, "alice").db() as db:
        assert db.execute("SELECT count(*) FROM card_tool_executions WHERE execution_live=1").fetchone()[0] == 0


def test_timeout_returns_no_answer_and_resumes_instead_of_choosing_option_one(root, monkeypatch):
    enable(root)
    monkeypatch.setattr("run.tools.user_decisions.TIMEOUT_SECONDS", .12)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[call("user_decision", **QUESTION)])
            payload = request.model_dump_json()
            assert "expired" in payload and "沉默不代表批准" in payload
            assert '"custom":' not in payload
            return response(request, "根据证据自主完成")

    events = execute(root, Provider())
    assert events[-1].metadata["status"] == "completed" and len(requests) == 2
    assert pending(root) == []


@pytest.mark.parametrize("mode", ["wait", "async"])
def test_stopping_a_run_closes_questions_and_releases_the_session(root, mode):
    enable(root)
    cancel = threading.Event()

    class Provider:
        def create(self, request):
            return response(request, "", calls=[call("user_decision", **QUESTION, mode=mode)])

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(
            lambda: list(iter_request_events(
                {"user": "alice", "source": "web", "session_id": "s1", "prompt": "test"},
                root=root, provider_factory=lambda _: Provider(),
                tool_registry_factory=lambda *_: registry(root), cancel_event=cancel,
            )))
        await_question(root)
        cancel.set()
        events = future.result(5)
    assert events[-1].metadata["status"] == "cancelled"
    assert pending(root) == []
    store = CardStore(root, "alice")
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM card_tool_executions WHERE execution_live=1").fetchone()[0] == 0
    IntegrationStore(store, "web", "s1", "next-integration").start()


def test_answer_timeout_and_agent_close_commit_one_terminal_fact(root):
    store, integration, decision_id = stored_question(root)
    decisions = DecisionStore(store)
    item = decisions.get("web", "s1", decision_id)
    operations = [
        {"status": "answered", "option_id": "pg"},
        {"status": "expired", "now": item["created_at"] + TIMEOUT_SECONDS + 1},
        {"status": "resolved_by_agent", "resolution": "已解决"},
    ]
    barrier = threading.Barrier(3)

    def settle(arguments):
        barrier.wait(3)
        try:
            return decisions.settle("web", "s1", decision_id, **arguments)["status"]
        except DecisionClosedError:
            return "lost"

    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(settle, operations))
    assert results.count("lost") == 2
    assert len([card for card in store.raw_page("web", "s1") if card.raw_kind == "tool_result"]) == 1
    assert integration.live_executions() == []
    with pytest.raises(KeyError):
        decisions.get("app", "s1", decision_id)
    with pytest.raises(KeyError):
        decisions.get("web", "other", decision_id)
    (root / "users/bob").mkdir()
    with pytest.raises(KeyError):
        DecisionStore(CardStore(root, "bob")).get("web", "s1", decision_id)


def test_answer_arriving_before_initial_receipt_does_not_break_the_run(root, monkeypatch):
    enable(root)
    run_decision_tool = decision_plugin.run

    def answer_at_registration(*, context, **arguments):
        value = run_decision_tool(**arguments, context=context)
        if arguments["action"] == "ask":
            DecisionStore(CardStore(root, "alice")).settle(
                "web", "s1", context["decision_execution_id"], status="answered", option_id="pg",
            )
        return value

    monkeypatch.setattr(decision_plugin, "run", answer_at_registration)
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            if len(requests) == 1:
                return response(request, "", calls=[call("user_decision", **QUESTION)])
            assert "answered" in request.model_dump_json()
            return response(request, "快速回答后正常继续")

    events = execute(root, Provider())
    assert events[-1].metadata["status"] == "completed", events[-1].to_dict()
    assert len(requests) == 2
    results = [card for card in CardStore(root, "alice").raw_page("web", "s1")
               if card.raw_kind == "tool_result"]
    assert len(results) == 1 and results[0].raw_payload["result"]["status"] == "answered"


def test_late_answer_is_expired_instead_of_authorizing_the_selected_option(root):
    store, _, decision_id = stored_question(root)
    decisions = DecisionStore(store)
    item = decisions.get("web", "s1", decision_id)
    result = decisions.settle("web", "s1", decision_id, status="answered", option_id="pg",
                              now=item["expires_at"] + 1)
    assert result["status"] == "expired" and result["answer"] is None
    with pytest.raises(ConflictError):
        WebRunService(root).answer_decision("alice", "s1", decision_id, option_id="pg")


def test_disabling_the_switch_cancels_pending_cards(root):
    store, integration, decision_id = stored_question(root)
    enable(root, False)
    result = WebRunService(root).decisions("alice", "s1")
    assert result["enabled"] is False and result["decisions"] == []
    assert DecisionStore(store).get("web", "s1", decision_id)["status"] == "cancelled"
    assert integration.live_executions() == []


def test_deleted_history_clears_question_and_answer_without_reviving_session(root):
    store, integration, decision_id = stored_question(root)
    DecisionStore(store).settle("web", "s1", decision_id, status="answered", text="private answer")
    integration.finish("cancelled")
    assert delete_session(root, "alice", "web", "s1") == 1
    assert pending(root) == []
    with pytest.raises(KeyError):
        DecisionStore(store).settle("web", "s1", decision_id, status="answered", option_id="pg")
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM context_cards").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM card_tool_executions").fetchone()[0] == 0


def test_dead_owner_can_resume_same_wait_without_replaying_tool_or_user_message(root):
    store, integration, decision_id = stored_question(root)
    store.append("web", "s1", integration.id, "user_text", {"text": "原始任务"}, level="initial")
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=2147483647,owner_token='lost'")
    # Routine recovery must preserve a known safe native wait.
    assert IntegrationStore(store, "web", "s1", "new").recover_interrupted() == []
    DecisionStore(store).settle("web", "s1", decision_id, status="answered", option_id="pg")
    requests = []

    class Provider:
        def create(self, request):
            requests.append(request)
            assert "PostgreSQL" in request.model_dump_json() and "原始任务" in request.model_dump_json()
            return response(request, "恢复后完成")

    events = execute(root, Provider(), _resume_decision_integration=integration.id,
                     run_id="run_decision_test", prompt="")
    assert events[-1].metadata["status"] == "completed", events[-1].to_dict()
    assert len(requests) == 1
    cards = store.raw_page("web", "s1")
    assert len([card for card in cards if card.raw_kind == "user_text"]) == 1
    assert len([card for card in cards if card.raw_kind == "tool_call"]) == 1
    with store.db() as db:
        row = db.execute("SELECT * FROM card_integrations").fetchone()
        assert row["integration_id"] == integration.id and row["owner_token"] == _OWNER
        assert row["response_closed"] == 1


def test_unknown_live_external_tool_prevents_automatic_decision_resume(root):
    store, integration, _ = stored_question(root)
    integration.register_tool("side-effect", {"name": "shell", "arguments": {"command": "unknown"}})
    with store.db(write=True) as db:
        db.execute("UPDATE card_integrations SET owner_pid=2147483647,owner_token='lost'")
    with pytest.raises(CardConflictError):
        IntegrationStore(store, "web", "s1", integration.id).resume_decisions()


def test_web_config_rejects_global_or_non_boolean_switch(root):
    service = WebRunService(root)
    with pytest.raises(InvalidRequestError):
        service.patch_global_config({"interactive_decisions": {"enabled": True}})
    with pytest.raises(InvalidRequestError):
        service.patch_user_config("alice", {"interactive_decisions": {"enabled": "yes"}})
