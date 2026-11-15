"""Native and dedicated inputs share media types, provenance and Kemo limits."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from plugins.multimodal import tool
from provider.protocol.assets import AssetDescriptor
from provider.schema import ChatResponse, ProviderError, ToolCall
from run.engine import handle_request
from run.extensions import AttachmentError, describe_local_asset
from tests.media_input_support import PNG, MediaProvider, enqueue, make_host, make_root, native_blocks
from tests.type_assertions import require_present


MEDIA = [
    ("image", "png", PNG, "analyze_image", "vision", "image/png"),
    ("audio", "wav", b"RIFF0000WAVEdata", "transcribe_audio", "audio_transcription", "audio/wav"),
    ("video", "mp4", b"\0\0\0\x18ftypisomvideo", "analyze_video", "video_understanding", "video/mp4"),
    ("file", "txt", b"report: revenue 42", "analyze_file", "file_understanding", "text/plain"),
]


class LoadingProvider(MediaProvider):
    operations = {"vision": True, "audio_transcription": True, "video_understanding": True}

    def capabilities(self, model, **kwargs):
        result = super().capabilities(model, **kwargs)
        result.extensions["operations"] = dict(self.operations)
        return result


@pytest.fixture
def loading(tmp_path, monkeypatch):
    config, _ = make_root(tmp_path, provider_type="kemo")
    config["multimodal_models"] = {item[4]: "media-model" for item in MEDIA}
    path = tmp_path / "users/alice/user_config.yaml"
    user_config = json.loads(path.read_text("utf-8"))
    user_config["multimodal_models"] = config["multimodal_models"]
    path.write_text(json.dumps(user_config), "utf-8")
    dedicated = LoadingProvider()
    factory = Mock(return_value=dedicated)
    monkeypatch.setattr(tool, "create_provider", factory)
    return config, dedicated, factory


@pytest.mark.parametrize(("kind", "extension", "data", "action", "slot", "mime"), MEDIA, ids=[row[0] for row in MEDIA])
@pytest.mark.parametrize("source", ["paths", "asset_ids"])
def test_local_inputs_have_equivalent_native_and_dedicated_blocks(
    tmp_path, loading, kind, extension, data, action, slot, mime, source,
):
    config, dedicated, _ = loading
    path = tmp_path / f"users/alice/download/input.{extension}"
    path.write_bytes(data)
    descriptor = describe_local_asset(tmp_path, "alice", {"path": str(path)})
    arguments = {source: [str(path) if source == "paths" else descriptor["asset_id"]], "detail": "high"}
    native = LoadingProvider()
    host = make_host(tmp_path, config, native, known=[descriptor])
    enqueue(host, arguments)
    messages = []
    host.refresh_messages(messages)
    tool.run(action, instruction="inspect input", **arguments, context={
        "root": str(tmp_path), "user": "alice", "uploaded_files": [descriptor],
    })
    block = dedicated.requests[0].input[0].content[1].model_dump(mode="json", exclude_none=True)
    assert block == messages[0]["content"][1]
    assert block["type"] == kind and block["mime_type"] == mime
    assert len(native.uploads) == len(dedicated.uploads) == 1
    assert native.uploads[0][1]["checksum_sha256"] == dedicated.uploads[0][1]["checksum_sha256"]
    if kind == "image":
        assert block["detail"] == "high"
    if kind == "file":
        assert block["filename"] == path.name


def gateway_asset(mime="application/pdf", **changes):
    return AssetDescriptor(
        protocol_version="2.0", id="asset_owned",
        status="ready", purpose="input", filename="input.pdf",
        mime_type=mime, size=128, checksum_sha256="a" * 64,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    ).model_copy(update=changes)


@pytest.mark.parametrize(("kind", "extension", "data", "action", "slot", "mime"), MEDIA, ids=[row[0] for row in MEDIA])
def test_owned_gateway_asset_uses_same_block_without_reupload(
    tmp_path, loading, monkeypatch, kind, extension, data, action, slot, mime,
):
    config, dedicated, _ = loading
    remote = gateway_asset(mime, filename=f"input.{extension}")
    native = LoadingProvider()
    for provider in (native, dedicated):
        monkeypatch.setattr(provider, "get_asset", Mock(return_value=remote), raising=False)
    host = make_host(tmp_path, config, native)
    receipt = enqueue(host, {"asset_ids": [remote.id], "detail": "high"})
    messages = []
    host.refresh_messages(messages)
    host.refresh_messages(messages)
    tool.run(action, asset_ids=[remote.id], detail="high", instruction="inspect", context={
        "root": str(tmp_path), "user": "alice", "uploaded_files": [],
    })
    assert messages[0]["content"][1] == dedicated.requests[0].input[0].content[1].model_dump(
        mode="json", exclude_none=True,
    )
    assert not native.uploads and not dedicated.uploads
    native.get_asset.assert_called_once_with(remote.id)
    assert receipt["assets"][0]["media_kind"] == kind
    assert receipt["assets"][0]["available"] is False
    assert "path" not in receipt["assets"][0]
    host.acknowledge()
    host.refresh_messages(messages)
    assert not messages and host.audit[0]["status"] == "included"


@pytest.mark.parametrize("declaration", [False, {"supported": False}])
def test_disabled_conversation_rejected_by_both_file_routes(tmp_path, loading, declaration):
    config, dedicated, _ = loading
    path = tmp_path / "users/alice/download/input.txt"
    path.write_text("document", "utf-8")
    dedicated.operations = {"conversation": declaration}
    native = LoadingProvider()
    native.operations = dedicated.operations
    host = make_host(tmp_path, config, native)
    with pytest.raises(AttachmentError, match="不支持"):
        enqueue(host, {"paths": [str(path)]})
    with pytest.raises(ValueError, match="conversation"):
        tool.run("analyze_file", paths=[str(path)], instruction="inspect", context={
            "root": str(tmp_path), "user": "alice", "uploaded_files": [],
        })
    assert not native.uploads and not dedicated.uploads and not dedicated.requests
    assert not host.queue.entries


@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_model_image_limit_applies_before_upload(tmp_path, loading, loader):
    config, dedicated, _ = loading
    paths = [tmp_path / f"users/alice/download/{index}.png" for index in range(2)]
    for path in paths:
        path.write_bytes(PNG + path.name.encode())
    provider = LoadingProvider(max_images=1)
    dedicated.max_images = 1
    with pytest.raises((ValueError, AttachmentError), match="图片.*上限"):
        if loader == "native":
            enqueue(make_host(tmp_path, config, provider), {"paths": list(map(str, paths))})
        else:
            tool.run("analyze_image", paths=list(map(str, paths)), instruction="inspect", context={
                "root": str(tmp_path), "user": "alice", "uploaded_files": [],
            })
    assert not provider.uploads and not dedicated.uploads


@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_upload_checksum_mismatch_is_not_sent_to_model(tmp_path, loading, monkeypatch, loader):
    config, dedicated, _ = loading
    path = tmp_path / "users/alice/download/input.txt"
    path.write_text("document", "utf-8")
    native = LoadingProvider()
    for provider in (native, dedicated):
        monkeypatch.setattr(provider, "upload_asset", lambda *a, **kw: SimpleNamespace(
            id="asset_mismatch", checksum_sha256="0" * 64, expires_at=None,
        ))
    host = make_host(tmp_path, config, native) if loader == "native" else None
    with pytest.raises(AttachmentError, match="校验和"):
        if loader == "native":
            enqueue(require_present(host), {"paths": [str(path)]})
            require_present(host).refresh_messages([])
        else:
            tool.run("analyze_file", paths=[str(path)], instruction="inspect", context={
                "root": str(tmp_path), "user": "alice", "uploaded_files": [],
            })
    if host is not None:
        assert host.audit[0]["status"] == "failed"
    assert not native.requests and not dedicated.requests


@pytest.mark.parametrize("fault", ["account", "expired", "forbidden"])
def test_native_gateway_asset_keeps_account_and_expiry_boundary(
    tmp_path, loading, monkeypatch, fault,
):
    config, _, _ = loading
    remote = gateway_asset()
    provider = LoadingProvider()
    monkeypatch.setattr(provider, "get_asset", Mock(return_value=remote), raising=False)
    state = {}
    host = make_host(tmp_path, config, provider, state=state)
    if fault == "forbidden":
        provider.get_asset.side_effect = ProviderError("forbidden", status_code=403, retryable=False)
        with pytest.raises(ProviderError, match="forbidden"):
            enqueue(host, {"asset_ids": [remote.id]})
        assert not host.queue.entries
        return
    enqueue(host, {"asset_ids": [remote.id]})
    if fault == "account":
        host = make_host(tmp_path, config, provider, state=state, runtime_provider={
            **config["provider"], "api_key": "different-account",
        })
    else:
        remote.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(AttachmentError, match="账户|过期"):
        host.refresh_messages([])
    assert host.audit[0]["status"] == "failed"
    assert not provider.requests and not provider.uploads


def test_gateway_file_is_delivered_by_real_tool_loop_without_private_metadata(
    tmp_path, loading, monkeypatch,
):
    remote = gateway_asset()
    provider = LoadingProvider([
        ChatResponse(text="", tool_calls=[ToolCall(
            id="callid_remote", name="attach_media", arguments={"asset_ids": [remote.id]},
        )]),
        ChatResponse(text="document understood"),
    ])
    monkeypatch.setattr(provider, "get_asset", Mock(return_value=remote), raising=False)
    result = handle_request(
        {"user": "alice", "source": "web", "session_id": "gateway-document", "prompt": "inspect document"},
        root=tmp_path, provider_factory=lambda _: provider,
    )
    assert result["text"] == "document understood"
    assert not native_blocks(provider.requests[0])
    block = native_blocks(provider.requests[1])[0]
    assert block.type == "file" and block.asset_id == remote.id
    assert not provider.uploads
    assert "provider_identity" not in json.dumps(
        provider.requests[1].model_dump(mode="json", exclude_none=True),
    )
