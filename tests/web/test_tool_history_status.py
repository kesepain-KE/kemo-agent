"""History must preserve tool failure and unconfirmed execution semantics."""
import pytest

from run.cards import CardStore, IntegrationStore
from run.history import reserve_session
from tests.card_history_support import seed_history
from web.service import WebRunService


@pytest.mark.parametrize("status,result,execution_live,expected", [
    ("completed", {"ok": True}, False, "success"),
    ("duplicate_reused", {"ok": True}, False, "success"),
    ("duplicate_blocked", {"ok": False}, False, "error"),
    ("completed", {"ok": False}, False, "error"),
    ("timed_out_running", {"ok": False, "error": {"still_running": True}}, True, "timed_out_running"),
    ("interrupted_unknown", {"ok": False, "error": {"still_running": True}}, True, "timed_out_running"),
    ("failed", {"ok": False, "error": {"timeout": True}}, False, "error"),
    ("unknown_status", {"ok": True}, False, "error"),
])
def test_tool_status_survives_history_service(tmp_path, status, result, execution_live, expected):
    user = tmp_path / "users/alice"
    user.mkdir(parents=True)
    (user / "user_config.yaml").write_text("{}", "utf-8")
    reserve_session(tmp_path, "alice", "web", "s1")
    store = CardStore(tmp_path, "alice")
    integration = IntegrationStore(store, "web", "s1", "integration-1")
    integration.start()
    store.append("web", "s1", integration.id, "user_text", {"text": "work"}, level="initial")
    integration.request_started()
    execution, _ = integration.register_tool("call-1", {"name": "file", "arguments": {}})
    integration.tool_result(
        execution["execution_id"], result, status=status, duration_ms=1,
        execution_live=execution_live,
    )
    integration.request_finished(accepted_seq=store.snapshot("web", "s1").last_seq)
    integration.finish("partial" if execution_live else "completed",
                       final_payload={"text": "receipt"})
    tool = WebRunService(tmp_path).history("alice", "s1")["integration_traces"][0]["tools"][0]
    assert tool["status"] == expected


def test_legacy_detached_exit_keeps_raw_cursor_without_rendering_a_tool(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    (tmp_path / "users/alice/user_config.yaml").write_text("{}", "utf-8")
    saved = seed_history(tmp_path, "alice", "web", "s1", [
        {"role": "user", "content": "start service"},
        {"role": "assistant", "content": "service ready"},
        {"role": "user", "content": "next task"},
        {"role": "assistant", "content": "next result"},
    ])
    old_id = saved.integrations[0]["integration_id"]
    job_id = "job_" + "a" * 32
    receipt = saved.store.append("web", "s1", old_id, "tool_result", {
        "execution_id": job_id,
        "tool_call_id": "service-call",
        "original_call": {"name": "shell", "arguments": {
            "command": "dev-server", "background": True, "completion_mode": "detached",
        }},
        "execution": {"status": "failed", "execution_live": False},
        "result": {"ok": False, "job_id": job_id, "completion_mode": "detached"},
    }, tool_call_id="service-call", event_key=f"shell-result:{job_id}")

    page = WebRunService(tmp_path).history("alice", "s1", limit=1)
    assert page["pagination"]["last_seq"] == receipt.seq
    assert page["pagination"]["next_before"] == receipt.seq
    assert page["cards"][0]["card_id"] == receipt.card_id
    assert "tool" not in page["cards"][0]
    assert page["integration_traces"][0]["tools"] == []
    assert saved.cards[-1].raw_payload["result"]["job_id"] == job_id
