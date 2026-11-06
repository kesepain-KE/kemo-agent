"""History must preserve tool failure and unconfirmed execution semantics."""
import pytest

from run.cards import CardStore, IntegrationStore
from run.history import reserve_session
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
