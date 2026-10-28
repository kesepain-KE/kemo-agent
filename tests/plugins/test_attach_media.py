from __future__ import annotations

import json
import os
import importlib
import threading
from typing import Any
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from provider.adapters.compat import chat_request_to_kemo, kemo_request_to_chat
from provider.schema import ChatRequest, ProviderError
from run.context import estimate_messages_tokens
from run.extensions import (
    AttachmentError, describe_local_asset, request_media_attachment,
)
from run.tools import discover_tools, execute_tool
from tests.media_input_support import (
    PNG, MediaProvider, enqueue, make_host, make_root,
)


@pytest.mark.parametrize("arguments", [
    {}, {"asset_ids": "asset_a"}, {"paths": [1]}, {"paths": [""]},
    {"paths": [str(i) for i in range(9)]}, {"paths": ["a"], "note": "x" * 4001},
    {"paths": ["a"], "detail": []}, {"paths": ["a"], "detail": "wrong"},
])
def test_intent_validation(arguments):
    with pytest.raises(AttachmentError):
        request_media_attachment(**arguments)


def test_process_plugin_only_returns_intent(tmp_path):
    config, image = make_root(tmp_path)
    tool = discover_tools(tmp_path, "alice").get("attach_media")
    assert tool.execution_mode == "process"
    arguments = {"paths": [str(image)], "note": "inspect"}
    result = execute_tool(tool, arguments, context={
        "root": str(tmp_path), "user": "alice",
    }, timeout=15)
    assert result == request_media_attachment(**arguments)
    assert "status" not in result
    host = make_host(tmp_path, config)
    result = enqueue(host, arguments)
    assert result["status"] == "queued"
    assert host.audit[0]["status"] == "queued"
    assert "path" not in result["assets"][0]


@pytest.mark.parametrize("field,value", [
    ("name", "other"), ("directory", "other"), ("entrypoint", "bad.py:run"),
])
def test_canonical_tool_only(tmp_path, field, value):
    config, image = make_root(tmp_path)
    host = make_host(tmp_path, config)
    tool = SimpleNamespace(
        name="attach_media", directory=tmp_path / "plugins/attach_media",
        entrypoint="tool.py:run",
    )
    setattr(tool, field, tmp_path / value if field == "directory" else value)
    arguments = {"paths": [str(image)]}
    with pytest.raises(AttachmentError, match="内置"):
        host.accept(tool, arguments, request_media_attachment(**arguments),
                    call_id="callid_1", tool_records=[])
    assert not host.queue.entries


def test_forged_intent_does_not_enqueue(tmp_path):
    config, image = make_root(tmp_path)
    host = make_host(tmp_path, config)
    with pytest.raises(AttachmentError, match="无效"):
        host.accept(discover_tools(tmp_path, "alice").get("attach_media"),
                    {"paths": [str(image)]}, {"status": "queued"},
                    call_id="callid_1", tool_records=[])


@pytest.mark.parametrize("authorization", ["user", "tool", "managed"])
def test_local_path_authorization(tmp_path, authorization):
    config, image = make_root(tmp_path)
    if authorization != "managed":
        image = tmp_path / "outside.png"
        image.write_bytes(PNG)
    host = make_host(tmp_path, config, user_text=str(image) if authorization == "user" else "")
    records = [{"result": {"ok": True, "result": {"files": [{"path": str(image)}]}}}]
    assert enqueue(host, {"paths": [str(image)]},
                   records=records if authorization == "tool" else [])["status"] == "queued"


@pytest.mark.parametrize("records", [
    [], [{"arguments": {"path": "outside.png"}, "result": {"ok": True, "result": {}}}],
    [{"result": {"ok": False, "result": {"path": "outside.png"}}}],
])
def test_unapproved_path_rejected(tmp_path, records):
    config, _ = make_root(tmp_path)
    (tmp_path / "outside.png").write_bytes(PNG)
    with pytest.raises(AttachmentError, match="本地路径必须"):
        enqueue(make_host(tmp_path, config), {"paths": ["outside.png"]}, records=records)


@pytest.mark.parametrize("as_asset", [False, True])
def test_other_user_even_with_explicit_path_is_forbidden(tmp_path, as_asset):
    config, _ = make_root(tmp_path)
    image = tmp_path / "users/bob/download/private.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(PNG)
    descriptor = describe_local_asset(tmp_path, "alice", {"path": str(image)})
    host = make_host(tmp_path, config, known=[descriptor], user_text=str(image))
    args = {"asset_ids": [descriptor["asset_id"]]} if as_asset else {"paths": [str(image)]}
    with pytest.raises(AttachmentError, match="其他用户"):
        enqueue(host, args)


def test_parent_symlink_rejected(tmp_path):
    config, _ = make_root(tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    (target / "screen.png").write_bytes(PNG)
    link = tmp_path / "users/alice/download/linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    with pytest.raises(AttachmentError, match="符号链接|联接"):
        enqueue(make_host(tmp_path, config), {"paths": [str(link / "screen.png")]})


def test_url_and_unknown_asset_rejected(tmp_path):
    config, _ = make_root(tmp_path)
    for args, pattern in [
        ({"paths": ["https://example.com/private.png"]}, "URL"),
        ({"paths": [r"\\server\share\private.png"]}, "URL"),
        ({"asset_ids": ["asset_unknown"]}, "不属于"),
    ]:
        with pytest.raises(AttachmentError, match=pattern):
            enqueue(make_host(tmp_path, config), args)


@pytest.mark.parametrize("fault", ["changed", "missing", "spoofed"])
def test_file_integrity(tmp_path, fault):
    config, image = make_root(tmp_path)
    host = make_host(tmp_path, config)
    if fault == "spoofed":
        image.write_bytes(b"not a png")
        with pytest.raises(AttachmentError, match="真实图片"):
            enqueue(host, {"paths": [str(image)]})
        return
    enqueue(host, {"paths": [str(image)]})
    if fault == "changed":
        image.write_bytes(PNG + b"changed")
    else:
        image.rename(image.with_suffix(".moved"))
    with pytest.raises(AttachmentError):
        host.refresh_messages([])
    assert host.audit[0]["status"] == "failed"
    assert "data:image" not in json.dumps(host.audit)


def test_dedupe_projection_and_receipt_lifecycle(tmp_path):
    config, image = make_root(tmp_path)
    state = {}
    host = make_host(tmp_path, config, state=state)
    first = enqueue(host, {"paths": [str(image)], "note": "external data"})
    again = enqueue(host, {"asset_ids": first["asset_ids"], "note": "ignored later note"})
    assert first["attachment_id"] == again["attachment_id"]
    messages: list[dict[str, Any]] = [
        {"role": "tool", "tool_call_id": "callid_attach", "content": "{}"}
    ]
    host.refresh_messages(messages)
    assert host.audit[0]["status"] == "prepared"
    assert messages[-1]["content"][1]["type"] == "image"
    assert messages[-1]["content"][1]["source"]["kind"] == "inline_base64"
    assert messages[-1]["role"] == "user"
    host.acknowledge()
    retry = make_host(tmp_path, config, state=state)
    retry.refresh_messages(messages)
    assert len(messages) == 2
    assert retry.audit[0]["status"] == "included"
    rebuilt = []
    retry.refresh_messages(rebuilt)
    assert len(rebuilt) == 1
    assert "ignored later note" not in rebuilt[0]["content"][0]["text"]
    mapped = kemo_request_to_chat(chat_request_to_kemo(ChatRequest(
        model="vision-main", messages=rebuilt, stream=False,
    )))
    assert mapped.messages[0]["content"][1]["type"] == "image_url"
    assert mapped.messages[0]["content"][1]["image_url"]["url"].startswith("data:image/png")
    assert "inline_base64" not in json.dumps(host.audit)


@pytest.mark.parametrize("scope,user", [
    (("main", "web", "other", "run"), "alice"),
    (("subagent", "task", "worker"), "alice"),
    (("main", "web", "session", "run"), "bob"),
])
def test_scope_mismatch_rejected(tmp_path, scope, user):
    config, _ = make_root(tmp_path)
    state = {}
    make_host(tmp_path, config, state=state)
    with pytest.raises(AttachmentError, match="不属于"):
        make_host(tmp_path, config, state=state, scope=scope, user=user)
    assert not make_host(tmp_path, config).queue.entries


@pytest.mark.parametrize("extension,data,kind", [
    ("png", PNG, "image"), ("wav", b"RIFF0000WAVEdata", "audio"),
    ("mp4", b"\0\0\0\x18ftypisomvideo", "video"), ("txt", b"hello", "file"),
])
def test_kemo_native_modalities_and_upload_reuse(tmp_path, extension, data, kind):
    config, _ = make_root(tmp_path, provider_type="kemo")
    path = tmp_path / f"users/alice/download/input.{extension}"
    path.write_bytes(data)
    provider = MediaProvider()
    state = {}
    host = make_host(tmp_path, config, provider, state=state)
    enqueue(host, {"paths": [str(path)]})
    messages = []
    host.refresh_messages(messages)
    host.refresh_messages(messages)
    assert len(provider.uploads) == 1
    assert messages[0]["content"][1]["type"] == kind
    assert messages[0]["content"][1]["asset_id"] == "asset_remote_1"
    assert "source" not in messages[0]["content"][1]
    assert estimate_messages_tokens(messages) >= 1024
    host.acknowledge()
    # A changed account is not allowed to reuse a remote file belonging to the old one.
    runtime = {**config["provider"], "api_key": "different-account"}
    retry = make_host(tmp_path, config, provider, state=state, runtime_provider=runtime)
    retry.refresh_messages([])
    assert len(provider.uploads) == 2


@pytest.mark.parametrize("provider_type,kind", [
    ("chat", "audio"), ("chat", "video"), ("chat", "file"),
    ("kemo", "audio"), ("kemo", "video"), ("kemo", "file"),
])
def test_unsupported_modality_never_queues(tmp_path, provider_type, kind):
    config, _ = make_root(tmp_path, provider_type=provider_type)
    extension, data = {
        "audio": ("wav", b"RIFF0000WAVEdata"),
        "video": ("mp4", b"\0\0\0\x18ftypisomvideo"), "file": ("txt", b"hello"),
    }[kind]
    path = tmp_path / f"users/alice/download/input.{extension}"
    path.write_bytes(data)
    host = make_host(tmp_path, config, MediaProvider(modalities=["text", "image"]))
    with pytest.raises(AttachmentError, match="不支持"):
        enqueue(host, {"paths": [str(path)]})
    assert not host.queue.entries


def test_route_budget_and_limits_are_atomic(tmp_path, monkeypatch):
    config, image = make_root(tmp_path)
    config["multimodal_routing"]["vision"] = "dedicated"
    with pytest.raises(AttachmentError, match="视觉路由"):
        enqueue(make_host(tmp_path, config), {"paths": [str(image)]})
    config["multimodal_routing"]["vision"] = "auto"
    with pytest.raises(AttachmentError, match="预算"):
        enqueue(make_host(tmp_path, config, input_limit=1024), {"paths": [str(image)]})
    from run.extensions import media_input_queue
    for name in ("MAX_RUN_MEDIA_ITEMS", "MAX_RUN_MEDIA_BYTES"):
        with monkeypatch.context() as scoped:
            scoped.setattr(media_input_queue, name, 0)
            host = make_host(tmp_path, config)
            with pytest.raises(AttachmentError):
                enqueue(host, {"paths": [str(image)]})
            assert not host.queue.entries


def test_visible_asset_is_not_uploaded_or_appended_again(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    descriptor = describe_local_asset(tmp_path, "alice", {"path": str(image)})
    provider = MediaProvider()
    host = make_host(tmp_path, config, provider, known=[descriptor],
                     visible_asset_ids={descriptor["asset_id"]})
    enqueue(host, {"asset_ids": [descriptor["asset_id"]]})
    messages = []
    host.refresh_messages(messages)
    assert not messages and not provider.uploads
    host.acknowledge()
    assert host.audit[0]["status"] == "included"


def test_total_images_limit_includes_other_native_inputs(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    host = make_host(tmp_path, config, MediaProvider(max_images=1))
    enqueue(host, {"paths": [str(image)]})
    messages = [{"role": "user", "content": [{"type": "image", "asset_id": "asset_other"}]}]
    host.refresh_messages(messages)
    with pytest.raises(AttachmentError, match="总数"):
        host.validate_request(messages)


def test_expired_upload_gets_new_idempotency_key(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    provider = MediaProvider()
    host = make_host(tmp_path, config, provider)
    enqueue(host, {"paths": [str(image)]})
    host.refresh_messages([])
    next(iter(host.queue.transport.remote.values())).expires_at = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    )
    host.refresh_messages([])
    assert len(provider.uploads) == 2
    assert provider.uploads[0][1]["idempotency_key"] != provider.uploads[1][1]["idempotency_key"]


def test_upload_failure_is_retryable_without_losing_queue(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    provider = MediaProvider()
    host = make_host(tmp_path, config, provider)
    enqueue(host, {"paths": [str(image)]})
    original = provider.upload_asset
    def broken(*args, **kwargs):
        raise ProviderError("offline", category="connection_error", retryable=True)
    provider.upload_asset = broken
    with pytest.raises(ProviderError):
        host.refresh_messages([])
    assert host.audit[0]["status"] == "queued"
    provider.upload_asset = original
    messages = []
    host.refresh_messages(messages)
    assert len(messages) == 1


def test_cancellation_does_not_queue_or_send(tmp_path):
    config, image = make_root(tmp_path)
    cancel = threading.Event()
    cancel.set()
    host = make_host(tmp_path, config, cancel_event=cancel)
    with pytest.raises(AttachmentError, match="取消"):
        enqueue(host, {"paths": [str(image)]})
    assert not host.queue.entries


def test_single_file_limit_precedes_hashing(tmp_path, monkeypatch):
    config, image = make_root(tmp_path)
    from run.extensions import attachments
    monkeypatch.setitem(attachments._MEDIA_LIMITS, "image", 1)
    def forbidden(*args):
        pytest.fail("Oversized media must be rejected before hashing")
    monkeypatch.setattr(attachments, "_sha256", forbidden)
    with pytest.raises(AttachmentError, match="超过请求上限"):
        enqueue(make_host(tmp_path, config), {"paths": [str(image)]})


def test_permanent_upload_failure_is_audited_as_failed(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    provider = MediaProvider()
    host = make_host(tmp_path, config, provider)
    enqueue(host, {"paths": [str(image)]})
    def broken(*args, **kwargs):
        raise ProviderError("expired file", category="asset_error", retryable=False)
    provider.upload_asset = broken
    with pytest.raises(ProviderError):
        host.refresh_messages([])
    assert host.audit[0]["status"] == "failed"


def test_subagent_full_context_budget_not_just_media(tmp_path):
    config, image = make_root(tmp_path)
    host = make_host(tmp_path, config, input_limit=3000)
    enqueue(host, {"paths": [str(image)]})
    items = []
    host.refresh_items(items)
    with pytest.raises(AttachmentError, match="子代理请求超过上下文预算"):
        host.validate_items(items, "system prompt " * 10000, [])
    assert host.audit[0]["status"] == "prepared"


def test_max_images_is_checked_before_commit(tmp_path):
    config, image = make_root(tmp_path, provider_type="kemo")
    host = make_host(tmp_path, config, MediaProvider(max_images=1))
    enqueue(host, {"paths": [str(image)]})
    another = image.with_name("another.png")
    another.write_bytes(PNG + b"different")
    with pytest.raises(AttachmentError, match="图片数量"):
        enqueue(host, {"paths": [str(another)]})
    assert len(host.queue.entries) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows junction boundary")
def test_parent_directory_junction_rejected(tmp_path):
    config, _ = make_root(tmp_path)
    target = tmp_path / "real"
    target.mkdir()
    (target / "screen.png").write_bytes(PNG)
    junction = tmp_path / "users/alice/download/junction"
    getattr(importlib.import_module("_winapi"), "CreateJunction")(str(target), str(junction))
    with pytest.raises(AttachmentError, match="符号链接|联接"):
        enqueue(make_host(tmp_path, config), {"paths": [str(junction / "screen.png")]})


def test_explicit_subagent_json_path_is_authorized_without_inheriting_main_assets(tmp_path):
    config, _ = make_root(tmp_path)
    image = tmp_path / "explicit.png"
    image.write_bytes(PNG)
    host = make_host(tmp_path, config, scope=("subagent", "web", "s", "task", "worker"),
                     user_text=json.dumps({"path": str(image)}, ensure_ascii=False))
    assert not host.known
    assert enqueue(host, {"paths": [str(image)]})["status"] == "queued"
