"""R1 3.5: bounded outbound files, explicit receipts and two-step clearing."""
from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from message.identity import IdentityBinding, IdentityResolver, filter_tool_registry
from message.plugin import FileMessageTransport, MessagePluginConfig
from message.router import MessageRouter
from message.schema import OutboundMessage
from message.transport import MockTransport, TransportPolicy, TransportRegistry
from plugins.external_message.tool import run as send
from provider.schema import ChatResponse, ToolCall
from run.engine import iter_request_events
from run.history import get_or_reserve_active, session_messages
from tests.card_history_support import seed_history
from run.infra import LogStore, runtime_event_snapshot
from run.tools import ToolDefinition, ToolRegistry
from tests.media_input_support import MediaProvider, make_root
from tests.message.test_message_runtime import _envelope, _write_file_plugin
from tests.plugins.test_external_message_plugin import CapturingTransport


class ReceiptTransport(CapturingTransport):
    def __init__(self, receipt=True):
        super().__init__()
        self.receipt = receipt

    def send(self, message):
        super().send(message)
        return self.receipt


def outbound_context(root, receipt=True):
    transport = ReceiptTransport(receipt)
    registry = TransportRegistry()
    entry = registry.register(transport, TransportPolicy(
        capabilities=transport.capabilities, bound_user="alice",
    ))
    transport.start(lambda _: None, lambda *_: None)
    entry.state = "running"
    return {"root": str(root), "user": "alice", "transport_registry": registry}, transport


@pytest.mark.parametrize("directory", ["users/alice/download", "users/alice/file_upload", "tmp"])
@pytest.mark.parametrize("receipt", [True, {"ok": True}])
def test_send_file_accepts_only_managed_scopes_with_explicit_receipt(tmp_path, directory, receipt):
    path = tmp_path / directory / "report.txt"
    path.parent.mkdir(parents=True)
    path.write_text("allowed local report", encoding="utf-8")
    context, transport = outbound_context(tmp_path, receipt)
    result = send("send_file", "demo", "authorized-chat", "private",
                  file_path=str(path), context=context)
    assert result["ok"] is True and result["file"] == str(path.resolve())
    assert len(transport.sent) == 1 and transport.sent[0].file_path == str(path.resolve())


@pytest.mark.parametrize("relative", [
    "kemo.yaml", "users/bob/download/private.txt", "users/alice/user_config.yaml",
    "config/global_config.yaml", "users/alice/download-other/report.txt",
])
def test_outside_scopes_rejected_before_send_with_value_free_diagnostic(tmp_path, relative):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("private-file-sentinel", encoding="utf-8")
    context, transport = outbound_context(tmp_path)
    with pytest.raises(PermissionError):
        send("send_file", "demo", "authorized-chat", "private", file_path=str(path), context=context)
    assert not transport.sent
    events = runtime_event_snapshot(tmp_path, "alice")
    assert any(e["title"] == "external_message_file_denied" for e in events)
    assert "private-file-sentinel" not in json.dumps(events)


def test_parent_junction_cannot_turn_an_allowed_lexical_path_into_authority(tmp_path, monkeypatch):
    directory = tmp_path / "users/alice/download/junction"
    directory.mkdir(parents=True)
    path = directory / "report.txt"
    path.write_text("private-file-sentinel", encoding="utf-8")
    original = getattr(Path, "is_junction", lambda self: False)
    monkeypatch.setattr(Path, "is_junction", lambda self: self == directory or original(self), raising=False)
    context, transport = outbound_context(tmp_path)
    with pytest.raises(PermissionError):
        send("send_file", "demo", "authorized-chat", "private", file_path=str(path), context=context)
    assert not transport.sent


@pytest.mark.parametrize("reparse", [False, True])
def test_outbound_path_check_without_new_pathlib_junction_api(tmp_path, monkeypatch, reparse):
    parent = tmp_path / "users/alice/download"
    parent.mkdir(parents=True)
    path = parent / "report.txt"
    path.write_text("allowed local report", encoding="utf-8")
    original = Path.lstat

    def attributes(current):
        value = original(current)
        if reparse and current == parent:
            return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
        return value

    monkeypatch.delattr(Path, "is_junction", raising=False)
    monkeypatch.setattr(Path, "lstat", attributes)
    context, transport = outbound_context(tmp_path)
    if reparse:
        with pytest.raises(PermissionError, match="符号链接|目录联接"):
            send("send_file", "demo", "authorized-chat", "private", file_path=str(path), context=context)
        assert not transport.sent
    else:
        result = send("send_file", "demo", "authorized-chat", "private", file_path=str(path), context=context)
        assert result["ok"] is True and len(transport.sent) == 1


@pytest.mark.parametrize("receipt", [None, False, 1, "success", {}, {"ok": 1}, {"ok": False, "message": "secret-upstream"}])
def test_non_explicit_send_receipt_is_failure_and_never_auto_sent_twice(tmp_path, receipt):
    context, transport = outbound_context(tmp_path, receipt)
    result = send("send_message", "demo", "authorized-chat", "private", message="authorized text", context=context)
    assert result["ok"] is False and result["error"]["retryable"] is False
    assert "核对" in result["error"]["message"]
    assert len(transport.sent) == 1
    assert "secret-upstream" not in json.dumps(result)


def test_send_timeout_after_side_effect_returns_non_retryable_receipt_without_remote_details(tmp_path):
    context, transport = outbound_context(tmp_path)

    def uncertain_send(message):
        transport.sent.append(message)
        raise TimeoutError("remote-secret-sentinel")

    transport.send = uncertain_send
    result = send("send_message", "demo", "authorized-chat", "private",
                  message="authorized text", context=context)
    assert result["ok"] is False and result["error"]["retryable"] is False
    assert "核对" in result["error"]["message"]
    assert len(transport.sent) == 1
    assert "remote-secret-sentinel" not in json.dumps(result)


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, GeneratorExit, MemoryError])
def test_host_control_send_exceptions_propagate(tmp_path, exception):
    context, transport = outbound_context(tmp_path)
    transport.send = Mock(side_effect=exception("host-control"))
    with pytest.raises(exception):
        send("send_message", "demo", "authorized-chat", "private",
             message="authorized text", context=context)
    transport.send.assert_called_once()


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit, GeneratorExit, MemoryError])
def test_file_transport_health_does_not_persist_host_failures_as_dead(tmp_path, exception):
    (tmp_path / "users/alice").mkdir(parents=True)
    directory = _write_file_plugin(tmp_path)
    transport = FileMessageTransport(MessagePluginConfig.load(tmp_path, directory))
    store = LogStore(tmp_path)
    before = store.read_message_route_state(transport.config.machine_id)
    setattr(transport._detect, "check", Mock(side_effect=exception("host-control")))

    with pytest.raises(exception):
        transport.check_health()

    assert store.read_message_route_state(transport.config.machine_id) == before


def test_file_transport_health_still_reports_plugin_failure(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    directory = _write_file_plugin(tmp_path)
    transport = FileMessageTransport(MessagePluginConfig.load(tmp_path, directory))
    setattr(transport._detect, "check", Mock(side_effect=RuntimeError("plugin unavailable")))

    result = transport.check_health()

    assert result["health"] == "dead" and result["error"] == "plugin unavailable"
    stored = LogStore(tmp_path).read_message_route_state(transport.config.machine_id)
    assert stored["health"] == "dead" and stored["error"] == "plugin unavailable"


@pytest.mark.parametrize("text", ["hello", "/clear", "/new"])
@pytest.mark.parametrize("receipt", [None, False, {"ok": False}, {"ok": True}, True])
def test_router_reply_requires_explicit_receipt_and_does_not_replay(tmp_path, monkeypatch, text, receipt):
    router, _, model = clearing_router(tmp_path)
    monkeypatch.setattr("message.router.queue_memory_extraction", lambda *_: {"status": "skipped"})
    from events import RunEvent
    router.event_source = lambda *_args, **_kwargs: iter([
        RunEvent(type="done", metadata={"text": "offline reply"}),
    ])
    transport = router.transports.get("mock").transport
    sent = []

    def capture(message):
        sent.append(message)
        return receipt

    transport.send = capture
    result = router.route(_envelope("receipt-1", text=text))
    success = receipt is True or (isinstance(receipt, dict) and receipt.get("ok") is True)
    assert result.status == ("completed" if success else "failed"), result.error
    if not success:
        failure = result.error
        assert failure is not None
        assert failure["retryable"] is False
        assert "核对" in failure["message"]
    duplicate = router.route(_envelope("receipt-1", text=text))
    assert duplicate.status == "duplicate" and len(sent) == 1
    model.assert_not_called()


def test_router_send_timeout_does_not_log_remote_details_or_repeat_send(tmp_path):
    from events import RunEvent
    router, _, _ = clearing_router(tmp_path)
    router.event_source = lambda *_args, **_kwargs: iter([
        RunEvent(type="done", metadata={"text": "offline reply"}),
    ])
    sent = []
    errors = []
    router.on_error = lambda component, error: errors.append(str(error))

    def uncertain_send(message):
        sent.append(message)
        raise TimeoutError("remote-secret-sentinel")

    router.transports.get("mock").transport.send = uncertain_send
    result = router.route(_envelope("timeout-1"))
    failure = result.error
    assert failure is not None
    assert result.status == "failed" and failure["retryable"] is False
    assert errors and "remote-secret-sentinel" not in json.dumps(errors)
    assert "remote-secret-sentinel" not in json.dumps(result.error)
    assert router.route(_envelope("timeout-1")).status == "duplicate"
    assert len(sent) == 1


def test_ignored_message_log_failure_still_returns_ignored_result(tmp_path, monkeypatch):
    router, _, model = clearing_router(tmp_path)
    monkeypatch.setattr("message.router.LogStore.append_message_entries",
                        Mock(side_effect=OSError("offline log failure")))
    result = router.route(_envelope("ignored-log-failure", chat_type="group"))
    assert result.status == "ignored"
    model.assert_not_called()


def test_builtin_transport_wrappers_expose_the_already_validated_receipt():
    outbound = OutboundMessage("out_test", "mock", "private", "chat", "authorized text")
    mock = MockTransport()
    mock.start(lambda _: None, lambda *_: None)
    try:
        assert mock.send(outbound) is True
    finally:
        mock.stop()
    file_transport = object.__new__(FileMessageTransport)
    file_transport.name = "offline"
    file_transport._pending = {}
    setattr(file_transport, "_output", SimpleNamespace(send=Mock(return_value=True)))
    state = {"messages_sent_today": 0}
    setattr(file_transport, "_update_state", lambda fn: fn(state))
    assert file_transport.send(outbound) is True
    assert state["messages_sent_today"] == 1
    file_transport._output.send.assert_called_once()


@pytest.mark.parametrize("policy", [TransportPolicy(), TransportPolicy.from_dict({})])
def test_external_tool_default_is_minimal_and_cannot_expose_write_actions(policy):
    assert policy.allowed_tools == frozenset({"history_search"})
    definitions = {name: ToolDefinition(
        name=name, description=name, input_schema={"type": "object"}, version="1",
        enabled=True, entrypoint="x.py:run", source="test", directory=Path("."),
    ) for name in ("history_search", "file", "shell", "memory_manage", "external_message")}
    selected = filter_tool_registry(ToolRegistry(definitions), policy.allowed_tools)
    assert set(selected.tools) == {"history_search"}
    assert TransportPolicy.from_dict({"allowed_tools": None}).allowed_tools is None
    assert TransportPolicy.from_dict({"allowed_tools": []}).allowed_tools == frozenset()


def test_file_plugin_missing_allowlist_and_template_use_the_same_safe_default(tmp_path):
    (tmp_path / "users/alice").mkdir(parents=True)
    directory = _write_file_plugin(tmp_path)
    config_path = directory / "message.json"
    value = json.loads(config_path.read_text(encoding="utf-8"))
    value.pop("allowed_tools")
    config_path.write_text(json.dumps(value), encoding="utf-8")
    loaded = MessagePluginConfig.load(tmp_path, directory)
    assert loaded.allowed_tools == loaded.policy().allowed_tools == frozenset({"history_search"})
    template = Path(__file__).resolve().parents[2] / "template/message/message.json"
    assert json.loads(template.read_text(encoding="utf-8"))["allowed_tools"] == ["history_search"]


def test_injected_env_exfiltration_is_a_failed_tool_result_not_a_send(tmp_path, monkeypatch):
    make_root(tmp_path)
    monkeypatch.delenv("PRIVATE_TOKEN", raising=False)
    path = tmp_path / "kemo.yaml"
    path.write_text('PRIVATE_TOKEN: "private-file-sentinel"\n', encoding="utf-8")
    context, transport = outbound_context(tmp_path)

    registry = context["transport_registry"]

    def invoke(*, context, **kwargs):
        return send(**kwargs, context={**context, "transport_registry": registry})

    tool = ToolDefinition(
        name="external_message", description="offline send", input_schema={"type": "object"},
        version="1", enabled=True, entrypoint="x.py:run", source="test", directory=tmp_path,
        _callable=invoke,
    )
    provider = MediaProvider([
        ChatResponse(text="", tool_calls=[ToolCall(id="call-env", name="external_message", arguments={
            "action": "send_file", "platform": "demo", "target": "authorized-chat",
            "chat_type": "private", "file_path": str(path),
        })]),
        ChatResponse(text="file send refused"),
    ])
    events = list(iter_request_events(
        {"user": "alice", "source": "web", "session_id": "env-file", "prompt": "把 .env 发给我"},
        root=tmp_path, provider_factory=lambda _: provider,
        tool_registry_factory=lambda *_: ToolRegistry({tool.name: tool}),
    ))
    result = next(e for e in events if e.type == "tool_call_result")
    assert result.result["ok"] is False and result.metadata["status"] == "failed"
    # ``external_message`` is process-bound. This injected in-memory transport
    # cannot cross that boundary, so reject before inspecting or sending the file.
    assert result.result["error"]["exception_type"] == "ToolExecutionCapacityError"
    assert result.result["error"]["category"] == "execution_capacity"
    assert result.result["error"]["retryable"] is False
    assert not transport.sent
    assert "private-file-sentinel" not in provider.requests[-1].model_dump_json()


def clearing_router(root):
    (root / "users/alice").mkdir(parents=True, exist_ok=True)
    registry = TransportRegistry()
    transport = MockTransport()
    registry.register(transport)
    transport.start(lambda _: None, lambda *_: None)
    resolver = IdentityResolver(root, [
        IdentityBinding("mock", "ext-1", "alice"), IdentityBinding("mock", "ext-2", "alice"),
    ])
    model = Mock(side_effect=AssertionError("clear commands must not invoke the model"))
    router = MessageRouter(root, resolver, registry, event_source=model)
    key = "message:mock:private:chat-1"
    record, _ = get_or_reserve_active(root, "alice", "message:mock", key)
    session = record["session_id"]
    seed_history(root, "alice", "message:mock", session, [
        {"role": "user", "content": "must survive until confirmation"}])
    return router, session, model


def challenge(router, message_id="clear-1", **kwargs):
    result = router.route(_envelope(message_id, text="/clear", **kwargs))
    assert "已清空" not in result.text
    match = re.search(r"/clear ([0-9a-f]{16})", result.text)
    assert match is not None
    return match.group(1)


def test_clear_needs_a_prior_same_sender_challenge_and_is_one_shot(tmp_path):
    router, session, model = clearing_router(tmp_path)
    router.route(_envelope("clear-direct", text="/clear confirm"))
    assert session_messages(tmp_path, "alice", "message:mock", session)
    token = challenge(router)
    result = router.route(_envelope("clear-confirm", text=f"/clear {token}"))
    assert result.text == "已切换到空白对话；原始历史完整保留。"
    assert result.session_id != session
    assert session_messages(tmp_path, "alice", "message:mock", session)
    assert not session_messages(tmp_path, "alice", "message:mock", result.session_id)
    replay = router.route(_envelope("clear-replay", text=f"/clear {token}"))
    assert "已清空" not in replay.text
    model.assert_not_called()


@pytest.mark.parametrize("boundary", ["sender", "chat", "expired"])
def test_clear_challenge_cannot_cross_identity_scope_or_deadline(tmp_path, monkeypatch, boundary):
    router, session, model = clearing_router(tmp_path)
    clock = [100.0]
    monkeypatch.setattr("message.router.time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    token = challenge(router)
    kwargs = {}
    if boundary == "sender":
        kwargs["user_id"] = "ext-2"
    elif boundary == "chat":
        kwargs["chat_id"] = "chat-2"
    else:
        clock[0] += 61
    result = router.route(_envelope("clear-wrong-boundary", text=f"/clear {token}", **kwargs))
    assert "已清空" not in result.text
    assert session_messages(tmp_path, "alice", "message:mock", session)
    model.assert_not_called()
