from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from message.identity import IdentityBinding, IdentityResolver
from message.plugin import FileMessageTransport, MessagePluginConfig, MessagePluginError
from message.router import MessageRouter
from message.transport import MockTransport, TransportPolicy, TransportRegistrationError, TransportRegistry
from run.infra import LogStore
from run.infra.runtime_diagnostics import runtime_event_snapshot
from run.tools import ToolRegistry
from tests.message.test_message_runtime import _done_events, _envelope, _write_file_plugin


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "config").mkdir()
    for user in ("alice", "bob"):
        directory = tmp_path / "users" / user
        directory.mkdir(parents=True)
        (directory / "user_config.yaml").write_text("{}", encoding="utf-8")
    return tmp_path


def make_router(root, policy=None, *, bindings=None):
    registry = TransportRegistry()
    transport = MockTransport()
    registry.register(transport, policy or TransportPolicy(allowed_tools=frozenset()))
    transport.start(lambda envelope: None, lambda component, exc: None)
    source = Mock(side_effect=_done_events)
    results = []
    router = MessageRouter(
        root,
        IdentityResolver(root, bindings if bindings is not None else [
            IdentityBinding("mock", "ext-1", "alice"),
            IdentityBinding("mock", "ext-2", "bob"),
        ]),
        registry,
        event_source=source,
        tool_registry_factory=lambda root, user: ToolRegistry({}),
        on_result=results.append,
    )
    return router, source, transport, results


@pytest.mark.parametrize("chat_type", ["group", "channel"])
def test_non_private_message_is_logged_once_without_run_or_history(workspace, chat_type):
    router, source, transport, results = make_router(workspace)
    result = router.route(_envelope(chat_type=chat_type))
    assert result.status == "ignored"
    assert result.error is not None
    assert result.error["code"] == "direct_only"
    assert result.session_id == ""
    source.assert_not_called()
    assert transport.sent == []
    assert results == [result]
    assert not list((workspace / "users").rglob("history.sqlite3"))
    logs = LogStore(workspace).list_messages("mock", user="alice")
    assert len(logs) == 1
    assert "direct_only" in logs[0]["content"]


def test_unknown_group_sender_still_does_not_create_a_run(workspace):
    router, source, _, _ = make_router(workspace, bindings=[])
    result = router.route(_envelope(chat_type="group", user_id="unbound"))
    assert result.status == "ignored"
    source.assert_not_called()
    assert len(LogStore(workspace).list_messages("mock", user="__system__")) == 1


def test_private_chat_allowlist_is_enforced_before_session_reservation(workspace):
    policy = TransportPolicy.from_dict({"allowed_chat_ids": ["permitted"]})
    router, source, _, _ = make_router(workspace, policy)
    denied = router.route(_envelope("denied", chat_id="elsewhere"))
    assert denied.status == "ignored"
    assert denied.error is not None
    assert denied.error["code"] == "chat_not_allowed"
    source.assert_not_called()
    assert not list((workspace / "users").rglob("history.sqlite3"))
    accepted = router.route(_envelope("permitted", chat_id="permitted"))
    assert accepted.status == "completed"
    assert source.call_count == 1


def test_bound_user_does_not_bypass_known_sender_identity(workspace):
    router, source, transport, _ = make_router(workspace, TransportPolicy(bound_user="alice"))
    result = router.route(_envelope(user_id="ext-2"))
    assert result.status == "rejected"
    assert result.error is not None
    assert result.error["code"] == "sender_not_allowed"
    assert result.sender_verified is False
    source.assert_not_called()
    assert transport.sent == []
    assert not list((workspace / "users").rglob("history.sqlite3"))
    assert len(LogStore(workspace).list_messages("mock", user="alice")) == 1


def test_bound_user_does_not_accept_an_unmapped_sender(workspace):
    router, source, _, _ = make_router(workspace, TransportPolicy(bound_user="alice"), bindings=[])
    result = router.route(_envelope(user_id="untrusted"))
    assert result.status == "rejected"
    source.assert_not_called()


@pytest.mark.parametrize("sender", ["alice", "ext-1"])
def test_bound_sender_matching_username_or_existing_binding_still_works(workspace, sender):
    router, source, _, _ = make_router(workspace, TransportPolicy(bound_user="alice"))
    result = router.route(_envelope(user_id=sender))
    assert result.status == "completed"
    assert result.user == "alice"
    assert result.sender_verified is True
    assert source.call_count == 1


def test_bound_sender_explicit_allowlist_preserves_external_platform_ids(workspace):
    policy = TransportPolicy.from_dict({"bound_user": "alice", "allowed_sender_ids": ["platform-owner"]})
    router, source, _, _ = make_router(workspace, policy, bindings=[])
    result = router.route(_envelope(user_id="platform-owner"))
    assert result.status == "completed"
    assert result.sender_verified is True
    assert source.call_count == 1
    denied = router.route(_envelope("another", user_id="somebody-else"))
    assert denied.status == "rejected"
    assert source.call_count == 1


def test_unavailable_legacy_sender_is_observable_not_falsely_verified(workspace):
    router, source, _, _ = make_router(workspace, TransportPolicy(bound_user="alice"), bindings=[])
    envelope = _envelope()
    # Valid MessageEnvelope requires an ID; retain a defensive path for legacy
    # transports that bypass dataclass validation rather than inventing an ID.
    object.__setattr__(envelope, "external_user_id", "")
    result = router.route(envelope)
    assert result.status == "completed"
    assert result.sender_verified is False
    assert result.warnings == ["该平台无法验证发送者"]
    assert source.call_count == 1
    assert any(
        event["title"] == "message_sender_unverifiable"
        for event in runtime_event_snapshot(workspace, "alice")
    )


@pytest.mark.parametrize("field", ["allowed_chat_ids", "allowed_sender_ids"])
def test_policy_rejects_malformed_allowlists(field):
    with pytest.raises(TransportRegistrationError):
        TransportPolicy.from_dict({field: "not-an-array"})


def test_plugin_configuration_exposes_optional_chat_and_sender_policies(workspace):
    directory = _write_file_plugin(workspace)
    path = directory / "message.json"
    config = json.loads(path.read_text("utf-8"))
    config.update(allowed_chat_ids=["chat-1"], allowed_sender_ids=["ext-1"])
    path.write_text(json.dumps(config), encoding="utf-8")
    policy = MessagePluginConfig.load(workspace, directory).policy()
    assert policy.allowed_chat_ids == frozenset({"chat-1"})
    assert policy.allowed_sender_ids == frozenset({"ext-1"})
    config["allowed_sender_ids"] = False
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(MessagePluginError):
        MessagePluginConfig.load(workspace, directory)


def test_file_transport_does_not_add_fake_send_or_duplicate_policy_logs(workspace):
    directory = _write_file_plugin(workspace)
    transport = FileMessageTransport(MessagePluginConfig.load(workspace, directory))
    router, source, _, _ = make_router(workspace)
    result = router.route(_envelope(chat_type="group"))
    transport._write_log((), result)
    source.assert_not_called()
    assert len(LogStore(workspace).list_messages("mock", user="alice")) == 1
    assert LogStore(workspace).list_messages("msg_filedemo", user="alice") == []
