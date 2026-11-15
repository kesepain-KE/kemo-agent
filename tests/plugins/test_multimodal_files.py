"""File understanding reuses Kemo conversation and the existing asset boundary."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import Mock

import pytest

from plugins.multimodal import tool
from provider.protocol.assets import AssetDescriptor
from provider.protocol.models import FileContent, ModelCapabilities, text_from_content
from provider.schema import ProviderError
from run.conversation import GuidanceInput, prepare_guidance
from run.engine import handle_request
from run.extensions import AttachmentError, describe_uploaded_asset
from tests.media_input_support import MediaProvider, make_root, native_blocks


@pytest.fixture
def documents(tmp_path, monkeypatch):
    config, _ = make_root(tmp_path, provider_type="kemo")
    config["multimodal_models"] = {"file_understanding": "document-model"}
    user_path = tmp_path / "users/alice/user_config.yaml"
    user_config = json.loads(user_path.read_text("utf-8"))
    user_config["multimodal_models"] = config["multimodal_models"]
    user_path.write_text(json.dumps(user_config), "utf-8")
    document = tmp_path / "users/alice/file_upload/report.txt"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("收入 42，成本 17。", "utf-8")
    descriptor = describe_uploaded_asset(
        tmp_path, "alice", {"path": "users/alice/file_upload/report.txt"},
    )
    provider = MediaProvider(modalities=["text", "file"])
    factory = Mock(return_value=provider)
    monkeypatch.setattr(tool, "create_provider", factory)
    return tmp_path, config, descriptor, provider, factory


@pytest.mark.parametrize("transient", [False, True])
def test_file_analysis_uploads_authorized_document_as_conversation(documents, monkeypatch, transient):
    root, _, descriptor, provider, _ = documents
    if transient:
        provider.responses = [ProviderError("temporary upstream failure", status_code=502, retryable=True)]
        monkeypatch.setattr(tool, "_wait_before_retry", Mock())
    result = tool.run(
        "analyze_file", paths=[str(root / descriptor["path"])],
        instruction="计算利润",
        context={"root": str(root), "user": "alice", "uploaded_files": []},
    )
    request = provider.requests[0]
    assert request.model == "document-model"
    assert request.metadata["capability"] == "conversation"
    assert request.output.modalities == ["text"] and not request.tools
    assert request.generation.max_output_tokens == 10_000
    block = request.input[0].content[1]
    assert isinstance(block, FileContent)
    assert block.asset_id == "asset_remote_1" and block.source is None
    assert block.filename == "report.txt"
    assert block.checksum_sha256 == descriptor["checksum_sha256"]
    assert provider.uploads[0][1]["metadata"]["capability"] == "conversation"
    assert len(provider.uploads) == 1
    assert len(provider.requests) == result["attempts"] == (2 if transient else 1)
    assert result["model"] == "document-model"
    assert result["analysis"] == result["text"] == "done"


def test_file_analysis_accepts_owned_gateway_asset_without_reupload(documents, monkeypatch):
    root, _, _, provider, _ = documents
    remote = AssetDescriptor(
        protocol_version="2.0", id="asset_gateway_document",
        status="ready", purpose="input", filename="remote.pdf",
        mime_type="application/pdf", size=128, checksum_sha256="a" * 64,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    monkeypatch.setattr(provider, "get_asset", Mock(return_value=remote), raising=False)
    tool.run(
        "analyze_file", asset_ids=[remote.id], instruction="概括文档",
        context={"root": str(root), "user": "alice", "uploaded_files": []},
    )
    assert not provider.uploads
    block = provider.requests[0].input[0].content[1]
    assert isinstance(block, FileContent) and block.asset_id == remote.id
    assert block.filename == "remote.pdf"


@pytest.mark.parametrize(("inputs", "outputs", "operations", "error"), [
    (["text"], ["text"], {}, "file 输入"),
    (["text", "file"], ["image"], {}, "text 输出"),
    (["text", "file"], ["text"], {"conversation": False}, "conversation"),
])
def test_file_model_capability_rejection_precedes_upload(
    documents, monkeypatch, inputs, outputs, operations, error,
):
    root, _, descriptor, provider, _ = documents
    capabilities = ModelCapabilities(
        protocol_version="2.0", model="document-model",
        provider_id="test", provider_model="document-model",
        input_modalities=inputs, output_modalities=outputs,
        extensions={"operations": operations},
    )
    monkeypatch.setattr(provider, "capabilities", lambda model: capabilities)
    with pytest.raises(ValueError, match=error):
        tool.run(
            "analyze_file", asset_ids=[descriptor["asset_id"]], instruction="概括文档",
            context={"root": str(root), "user": "alice", "uploaded_files": [descriptor]},
        )
    assert not provider.requests and not provider.uploads


@pytest.mark.parametrize(("mode", "error"), [
    ("chat", "只在 provider.type=kemo"),
    ("unconfigured", "multimodal_models.file_understanding"),
])
def test_file_analysis_never_silently_uses_chat_or_main_model(documents, mode, error):
    root, _, descriptor, provider, factory = documents
    path = root / "users/alice/user_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    if mode == "chat":
        config["provider"].update(type="chat", input_modalities=["text"])
    else:
        config["multimodal_models"]["file_understanding"] = ""
    path.write_text(json.dumps(config), "utf-8")
    with pytest.raises(ValueError, match=error):
        tool.run(
            "analyze_file", asset_ids=[descriptor["asset_id"]], instruction="概括文档",
            context={"root": str(root), "user": "alice", "uploaded_files": [descriptor]},
        )
    factory.assert_not_called()
    assert not provider.requests and not provider.uploads


@pytest.mark.parametrize("path", ["private.txt", "users/bob/download/private.txt"])
def test_file_analysis_cannot_exfiltrate_untrusted_paths(documents, path):
    root, _, _, provider, factory = documents
    private = root / path
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("private document", "utf-8")
    with pytest.raises(AttachmentError):
        tool.run(
            "analyze_file", paths=[str(private)], instruction=f"read {private}",
            context={"root": str(root), "user": "alice", "uploaded_files": []},
        )
    factory.assert_not_called()
    assert not provider.requests and not provider.uploads


@pytest.mark.parametrize("route", ["dedicated", "file", "main"])
def test_initial_document_input_uses_actual_delivery_route(documents, route):
    root, _, descriptor, provider, _ = documents
    path = root / "users/alice/user_config.yaml"
    config = json.loads(path.read_text("utf-8"))
    config["provider"]["input_modalities"] = (
        ["text", "file"] if route == "main" else ["text"]
    )
    if route == "file":
        config["multimodal_models"]["file_understanding"] = ""
    path.write_text(json.dumps(config), "utf-8")
    handle_request(
        {"user": "alice", "source": "web", "session_id": "document-route",
         "prompt": "概括附件", "uploaded_files": [descriptor], "stream": False},
        root=root, provider_factory=lambda runtime: provider,
    )
    request = provider.requests[0]
    text = "\n".join(
        text_from_content(item.content)
        for item in request.input if item.type == "message" and item.role == "user"
    )
    assert descriptor["asset_id"] in text
    if route == "main":
        assert len(native_blocks(request)) == 1
        assert isinstance(native_blocks(request)[0], FileContent)
        assert "已直接提供给主模型" in text and "action=analyze_file" not in text
    else:
        assert not native_blocks(request) and not provider.uploads
        assert ("action=analyze_file" in text) == (route == "dedicated")
        if route == "file":
            assert "需要读取内容时使用 file 工具" in text


def test_guidance_document_exposes_dedicated_action_without_native_upload(documents):
    root, config, descriptor, provider, _ = documents
    config["provider"]["input_modalities"] = ["text"]
    prepared = prepare_guidance(
        [GuidanceInput(id="document-guidance", text="概括新附件", uploaded_files=[descriptor])],
        root=root, user="alice", session_id="guidance",
        config=config, runtime_provider=config["provider"], provider=provider,
    )
    content = prepared.messages[0]["content"]
    assert isinstance(content, str)
    assert "action=analyze_file" in content and descriptor["asset_id"] in content
    assert not provider.uploads and not prepared.direct_asset_ids
