"""Inputs and generated media share bounded, isolated cross-run references."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

from plugins.multimodal import tool
from provider.protocol.assets import AssetDescriptor
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.models import ImageContent, KemoResponse, MessageItem
from provider.schema import ProviderError
from run.config import ConfigError, load_config
from run.extensions import AttachmentError
from run.extensions import media_asset_cache as cache
from tests.media_input_support import PNG, MediaProvider, enqueue, make_host, make_root
from tests.type_assertions import require_type


class ImageGenerator(MediaProvider):
    def capabilities(self, model, **kwargs):
        result = super().capabilities(model, **kwargs)
        result.output_modalities = ["image"]
        result.extensions["operations"] = {"image_generation": True}
        return result

    def create(self, request):
        return KemoResponse(
            request_id=request.request_id, status=ResponseStatus.COMPLETED,
            model=request.model, output=[MessageItem(
                id="msg_generated", role=MessageRole.ASSISTANT,
                content=[ImageContent(asset_id="asset_generated", mime_type="image/png",
                                      checksum_sha256=hashlib.sha256(PNG).hexdigest())],
            )],
        )

    def get_asset(self, asset_id):
        now = datetime.now(timezone.utc)
        return AssetDescriptor(
            protocol_version="2.0", id=asset_id, status="ready", purpose="output",
            filename="generated.png", mime_type="image/png", size=len(PNG),
            checksum_sha256=hashlib.sha256(PNG).hexdigest(),
            created_at=now, expires_at=now + timedelta(seconds=1),
        )

    def download_asset(self, asset_id, destination, **kwargs):
        destination.write_bytes(PNG)
        return destination


@pytest.fixture
def generated(tmp_path, monkeypatch, request):
    config, _ = make_root(tmp_path, provider_type="kemo")
    config["multimodal_models"] = {"image_generation": "image-model", "vision": "vision-model"}
    if hasattr(request, "param"):
        config["media_assets"] = {"ttl_seconds": request.param}
    (tmp_path / "config/global_config.yaml").write_text(json.dumps(config), "utf-8")
    user_path = tmp_path / "users/alice/user_config.yaml"
    user_config = json.loads(user_path.read_text("utf-8"))
    user_config["multimodal_models"] = config["multimodal_models"]
    user_path.write_text(json.dumps(user_config), "utf-8")
    clock = [cache.time()]
    monkeypatch.setattr(cache, "time", lambda: clock[0])
    monkeypatch.setattr(tool, "create_provider", lambda _: ImageGenerator())
    context = {"root": str(tmp_path), "user": "alice", "source": "web",
               "session_id": "session", "uploaded_files": []}
    result = tool.run("generate_image", instruction="draw a test image", context=context)
    return config, result["artifacts"][0], context, clock


@pytest.mark.parametrize("provider_type", ["chat", "kemo"])
@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_generated_id_uses_local_image_after_gateway_id_expires(
    tmp_path, generated, monkeypatch, provider_type, loader,
):
    config, artifact, context, _ = generated
    config["provider"]["type"] = provider_type
    if provider_type == "chat":
        config["provider"]["input_modalities"] = ["text", "image"]
    provider = MediaProvider()
    provider.get_asset = Mock(side_effect=ProviderError("expired", status_code=410, retryable=False))
    if loader == "native":
        host = make_host(tmp_path, config, provider)
        receipt = enqueue(host, {"asset_ids": [artifact["asset_id"]]})
        messages = []
        host.refresh_messages(messages)
        block = messages[0]["content"][1]
        assert receipt["ok"] is True and block["type"] == "image"
    else:
        path = tmp_path / "users/alice/user_config.yaml"
        user = json.loads(path.read_text("utf-8"))
        user["provider"] = config["provider"]
        path.write_text(json.dumps(user), "utf-8")
        original_capabilities = provider.capabilities
        def capabilities(model, **kwargs):
            result = original_capabilities(model, **kwargs)
            result.extensions["operations"] = {"vision": True}
            return result
        monkeypatch.setattr(provider, "capabilities", capabilities)
        monkeypatch.setattr(tool, "create_provider", lambda _: provider)
        result = tool.run("analyze_image", asset_ids=[artifact["asset_id"]],
                          instruction="inspect generated image", context=context)
        assert result["analysis"] == "done"
        block = provider.requests[0].input[0].content[1].model_dump(mode="json", exclude_none=True)
    provider.get_asset.assert_not_called()
    if provider_type == "chat":
        assert block["source"]["kind"] == "inline_base64" and not provider.uploads
    else:
        assert block["asset_id"] == "asset_remote_1"
        assert provider.uploads[0][0].read_bytes() == PNG


@pytest.mark.parametrize("generated", [60, 120], indirect=True)
def test_reference_expiry_uses_configured_seconds_without_read_renewal(tmp_path, generated):
    config, artifact, _, clock = generated
    ttl = config["media_assets"]["ttl_seconds"]
    started = clock[0]
    assert datetime.fromisoformat(artifact["reference_expires_at"]).timestamp() - started == pytest.approx(ttl, abs=1e-6, rel=0)
    def lookup():
        return cache.resolve_retained_media(tmp_path, "alice", [artifact["asset_id"]],
                                            source="web", session_id="session")
    clock[0] = started + ttl - 0.01
    ids, descriptors = lookup()
    assert descriptors and ids[0] == descriptors[0]["asset_id"]
    clock[0] = started + ttl
    assert lookup() == ([artifact["asset_id"]], [])
    saved = tmp_path / artifact["project_path"]
    assert saved.read_bytes() == PNG
    assert enqueue(make_host(tmp_path, config), {"paths": [str(saved)]})["ok"] is True


def test_default_120_seconds_survives_another_process(tmp_path, generated):
    _, artifact, _, clock = generated
    expires = datetime.fromisoformat(artifact["reference_expires_at"]).timestamp()
    assert expires - clock[0] == pytest.approx(120)
    script = (
        "import json,sys; from pathlib import Path; "
        "from run.extensions.media_asset_cache import resolve_retained_media; "
        "print(json.dumps(resolve_retained_media(Path(sys.argv[1]), 'alice', "
        "[sys.argv[2]], source='web', session_id='session')))"
    )
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script,
                             str(tmp_path), artifact["asset_id"]],
                            cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    ids, descriptors = json.loads(result.stdout)
    assert descriptors and ids[0] == descriptors[0]["asset_id"]


@pytest.mark.parametrize("scope", ["user", "source", "session", "installation"])
def test_local_reference_does_not_cross_scope(tmp_path, generated, scope):
    _, artifact, _, _ = generated
    root, user, source, session = tmp_path, "alice", "web", "session"
    if scope == "user":
        user = "bob"
    elif scope == "source":
        source = "app"
    elif scope == "session":
        session = "other-session"
    else:
        root = tmp_path / "another-installation"
    assert cache.resolve_retained_media(root, user, [artifact["asset_id"]],
                                        source=source, session_id=session) == ([artifact["asset_id"]], [])


@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_retained_file_changes_fail_before_upload(tmp_path, generated, monkeypatch, loader):
    config, artifact, context, _ = generated
    (tmp_path / artifact["project_path"]).write_bytes(PNG + b"changed")
    provider = MediaProvider()
    monkeypatch.setattr(tool, "create_provider", lambda _: provider)
    with pytest.raises(AttachmentError, match="内容已经变化"):
        if loader == "native":
            enqueue(make_host(tmp_path, config, provider), {"asset_ids": [artifact["asset_id"]]})
        else:
            tool.run("analyze_image", asset_ids=[artifact["asset_id"]], instruction="inspect", context=context)
    assert not provider.uploads and not provider.requests


def test_concurrent_tool_registrations_keep_all_references(tmp_path, generated):
    _, artifact, _, _ = generated
    def retain(index):
        item = {**artifact, "asset_id": f"asset_parallel_{index}"}
        cache.retain_media_assets(tmp_path, "alice", [item], source="web", session_id="session", ttl_seconds=120)
        return item["asset_id"]
    with ThreadPoolExecutor(max_workers=4) as workers:
        original_ids = list(workers.map(retain, range(8)))
    ids, descriptors = cache.resolve_retained_media(tmp_path, "alice", original_ids, source="web", session_id="session")
    assert len(ids) == len(descriptors) == 8
    assert all(asset_id.startswith("asset_") and asset_id not in original_ids for asset_id in ids)


@pytest.mark.parametrize("invalid", [0, -1, True, 1.5, "120", None])
def test_invalid_lifetime_is_rejected_by_config_loader(tmp_path, invalid):
    config, _ = make_root(tmp_path)
    config["media_assets"] = {"ttl_seconds": invalid}
    (tmp_path / "config/global_config.yaml").write_text(json.dumps(config), "utf-8")
    with pytest.raises(ConfigError, match="media_assets.ttl_seconds"):
        load_config("alice", tmp_path)


@pytest.mark.parametrize("source", ["web", "app", "message:demo"])
@pytest.mark.parametrize("media_index", range(4), ids=["image", "audio", "video", "file"])
@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_accepted_inputs_can_be_reused_in_a_later_run(
    tmp_path, monkeypatch, source, media_index, loader,
):
    from types import SimpleNamespace
    from provider.protocol.models import TextContent
    from run.conversation.media_routing import prepare_provider_request
    from run.extensions import describe_message_asset, describe_uploaded_asset
    from tests.plugins.test_media_loading_parity import MEDIA, LoadingProvider

    kind, extension, data, action, slot, _ = MEDIA[media_index]
    config, _ = make_root(tmp_path, provider_type="kemo")
    config["multimodal_models"] = {slot: "analysis-model"}
    user_path = tmp_path / "users/alice/user_config.yaml"
    user = json.loads(user_path.read_text("utf-8"))
    user["multimodal_models"] = config["multimodal_models"]
    user_path.write_text(json.dumps(user), "utf-8")
    if source.startswith("message:"):
        module = tmp_path / "message/out/demo"
        (module / "files").mkdir(parents=True)
        (module / "message.json").write_text(
            json.dumps({"bound_user": "alice", "files_dir": "files/"}), "utf-8",
        )
        path = module / f"files/input.{extension}"
        describe = lambda: describe_message_asset(tmp_path, "alice", {"path": str(path)}, source="demo")
    else:
        path = tmp_path / f"users/alice/file_upload/input.{extension}"
        path.parent.mkdir(parents=True, exist_ok=True)
        describe = lambda: describe_uploaded_asset(tmp_path, "alice", {"path": str(path)})
    path.write_bytes(data)
    descriptor = describe()
    clock = [cache.time()]
    monkeypatch.setattr(cache, "time", lambda: clock[0])
    prepared = prepare_provider_request(
        {"uploaded_files": [descriptor]}, base=tmp_path, user="alice", source=source,
        session_id="session", content_blocks=[TextContent(text="inspect")],
        dependencies=SimpleNamespace(provider_factory=lambda _: LoadingProvider()), cancel_event=None,
    )
    assert prepared.uploaded_descriptors[0]["asset_id"] == descriptor["asset_id"]
    assert "reference_expires_at" in prepared.uploaded_descriptors[0]
    remote_id = prepared.provider_media[0].asset_id
    aliases, registered = cache.resolve_retained_media(
        tmp_path, "alice", [descriptor["asset_id"], remote_id], source=source, session_id="session",
    )
    assert len(registered) == 2 and aliases[0] == aliases[1]
    if source.startswith("message:"):
        # FileMessageTransport owns and removes this original after the round.
        path.unlink()
    clock[0] += 30
    provider = LoadingProvider()
    provider.get_asset = Mock(side_effect=ProviderError("local ID is not a gateway ID", status_code=404, retryable=False))
    if loader == "native":
        host = make_host(tmp_path, config, provider,
                         scope=("main", source, "session", "run-2"))
        enqueue(host, {"asset_ids": [descriptor["asset_id"]]})
        messages = []
        host.refresh_messages(messages)
        assert messages[0]["content"][1]["type"] == kind
    else:
        monkeypatch.setattr(tool, "create_provider", lambda _: provider)
        result = tool.run(action, asset_ids=[descriptor["asset_id"]], instruction="inspect", context={
            "root": str(tmp_path), "user": "alice", "source": source,
            "session_id": "session", "uploaded_files": [],
        })
        assert result["text"] == "done"
        assert provider.requests[0].input[0].content[1].type == kind
    assert provider.uploads[0][0].read_bytes() == data
    provider.get_asset.assert_not_called()
    clock[0] += 90
    assert cache.resolve_retained_media(tmp_path, "alice", [remote_id],
                                        source=source, session_id="session") == ([remote_id], [])
    assert cache.resolve_retained_media(tmp_path, "alice", [descriptor["asset_id"]],
                                        source=source, session_id="session") == ([descriptor["asset_id"]], [])
    assert provider.uploads[0][0].is_file()


@pytest.mark.parametrize("source", ["web", "app"])
def test_guidance_inputs_enter_the_same_session_registry(tmp_path, source):
    from run.conversation.guidance_runtime import prepare_guidance
    from run.conversation.guidance import GuidanceInput
    from run.extensions import describe_uploaded_asset

    config, _ = make_root(tmp_path, provider_type="kemo")
    path = tmp_path / "users/alice/file_upload/report.txt"
    path.parent.mkdir(parents=True)
    path.write_text("guidance document", "utf-8")
    descriptor = describe_uploaded_asset(tmp_path, "alice", {"path": str(path)})
    prepare_guidance(
        [GuidanceInput(text="inspect", uploaded_files=[descriptor])],
        root=tmp_path, user="alice", source=source, session_id="session", config=config,
        runtime_provider=config["provider"], provider=MediaProvider(),
    )
    host = make_host(tmp_path, config, scope=("main", source, "session", "run-2"))
    assert enqueue(host, {"asset_ids": [descriptor["asset_id"]]})["ok"] is True


@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_explicit_local_paths_register_their_returned_ids(tmp_path, monkeypatch, loader):
    from tests.plugins.test_media_loading_parity import LoadingProvider

    config, image = make_root(tmp_path, provider_type="kemo")
    path = tmp_path / "users/alice/user_config.yaml"
    user = json.loads(path.read_text("utf-8"))
    user["multimodal_models"] = {"vision": "analysis-model"}
    path.write_text(json.dumps(user), "utf-8")
    if loader == "native":
        host = make_host(tmp_path, config)
        result = enqueue(host, {"paths": [str(image)]})
        asset_id = result["asset_ids"][0]
        host.refresh_messages([])
    else:
        monkeypatch.setattr(tool, "create_provider", lambda _: LoadingProvider())
        result = tool.run("analyze_image", paths=[str(image)], instruction="inspect", context={
            "root": str(tmp_path), "user": "alice", "source": "web", "session_id": "session",
        })
        asset_id = result["asset_ids"][0]
    assert enqueue(make_host(tmp_path, config), {"asset_ids": [asset_id]})["ok"] is True
    assert enqueue(make_host(tmp_path, config), {"asset_ids": ["asset_remote_1"]})["ok"] is True


@pytest.mark.parametrize("source", ["web", "app"])
def test_main_model_outputs_also_register_the_output_id(tmp_path, source):
    from run.engine import iter_request_events
    class MainGenerator(ImageGenerator):
        def capabilities(self, model, **kwargs):
            result = super().capabilities(model, **kwargs)
            result.output_modalities = ["text", "image"]
            return result

        def create(self, request):
            response = super().create(request)
            require_type(response.output[0], MessageItem).phase = MessagePhase.FINAL_ANSWER
            return response

    config, _ = make_root(tmp_path, provider_type="kemo")
    events = list(iter_request_events(
        {"user": "alice", "source": source, "session_id": "session", "prompt": "draw"},
        root=tmp_path, provider_factory=lambda _: MainGenerator(),
    ))
    assert not [event for event in events if event.type == "error"]
    artifact = next(event.result for event in events if event.type == "media_output")
    assert "reference_expires_at" in artifact
    provider = MediaProvider()
    host = make_host(tmp_path, config, provider, scope=("main", source, "session", "run-2"))
    assert enqueue(host, {"asset_ids": [artifact["asset_id"]]})["ok"] is True
    messages = []
    host.refresh_messages(messages)
    assert messages[0]["content"][1]["type"] == "image"
    assert provider.uploads[0][0].read_bytes() == PNG


def test_message_snapshot_rechecks_the_modules_bound_user(tmp_path):
    from run.extensions import RunAssetResolver, describe_message_asset

    config, image = make_root(tmp_path)
    module = tmp_path / "message/out/demo"
    (module / "files").mkdir(parents=True)
    metadata = module / "message.json"
    metadata.write_text(json.dumps({"bound_user": "alice", "files_dir": "files/"}), "utf-8")
    attachment = module / "files/input.png"
    attachment.write_bytes(PNG)
    descriptor = describe_message_asset(tmp_path, "alice", {"path": str(attachment)}, source="demo")
    cache.retain_media_descriptors(tmp_path, "alice", [descriptor], source="message:demo",
                                   session_id="session", ttl_seconds=120)
    ids, retained = cache.resolve_retained_media(tmp_path, "alice", [descriptor["asset_id"]],
                                                source="message:demo", session_id="session")
    attachment.unlink()
    assert RunAssetResolver(tmp_path, "alice", retained).local_asset(ids[0])[0].read_bytes() == PNG
    metadata.write_text(json.dumps({"bound_user": "bob", "files_dir": "files/"}), "utf-8")
    with pytest.raises(AttachmentError, match="当前用户不匹配"):
        enqueue(make_host(tmp_path, config, scope=("main", "message:demo", "session", "run-2")),
                {"asset_ids": [descriptor["asset_id"]]})


@pytest.mark.parametrize("source", ["web", "app"])
@pytest.mark.parametrize("media_index", [0, 3], ids=["image", "file"])
@pytest.mark.parametrize("loader", ["native", "dedicated"])
def test_published_extension_artifacts_can_be_reused_in_a_later_run(
    tmp_path, monkeypatch, source, media_index, loader,
):
    from plugins.expand_call.tool import run as call_expand
    from plugins.expand_creater.tool import run as create_expand
    from tests.plugins.test_media_loading_parity import MEDIA, LoadingProvider

    kind, extension, data, action, slot, _ = MEDIA[media_index]
    config, _ = make_root(tmp_path, provider_type="kemo")
    user_path = tmp_path / "users/alice/user_config.yaml"
    user = json.loads(user_path.read_text("utf-8"))
    user["multimodal_models"] = {slot: "analysis-model"}
    user_path.write_text(json.dumps(user), "utf-8")
    context = {"root": str(tmp_path), "user": "alice", "source": source,
               "session_id": "session", "tool_timeout": 15}
    create_expand(
        "create", "user", name="media_export", explain="Export a test artifact.",
        injection="Artifact export available.", operations="Call export to save the artifact.",
        start_expand=(
            "from pathlib import Path\n"
            "def execute(command, params=None):\n"
            "    target = Path(__file__).resolve().parent / params['name']\n"
            "    target.write_bytes(bytes.fromhex(params['data']))\n"
            "    return {'artifacts': [{'path': target.name}]}\n"
        ),
        context=context,
    )
    result = call_expand("user", "media_export", "export",
                         {"name": f"export.{extension}", "data": data.hex()}, context=context)
    artifact = result["artifacts"][0]
    assert artifact["asset_id"].startswith("expand_")
    assert "reference_expires_at" in artifact
    assert result["result"]["artifacts"] == result["artifacts"]
    provider = LoadingProvider()
    provider.get_asset = Mock(side_effect=ProviderError("not a remote ID", status_code=404, retryable=False))
    if loader == "native":
        host = make_host(tmp_path, config, provider, scope=("main", source, "session", "run-2"))
        assert enqueue(host, {"asset_ids": [artifact["asset_id"]]})["ok"] is True
        messages = []
        host.refresh_messages(messages)
        assert messages[0]["content"][1]["type"] == kind
    else:
        monkeypatch.setattr(tool, "create_provider", lambda _: provider)
        result = tool.run(action, asset_ids=[artifact["asset_id"]], instruction="inspect", context=context)
        assert result["text"] == "done"
        assert provider.requests[0].input[0].content[1].type == kind
    assert provider.uploads[0][0].read_bytes() == data
    provider.get_asset.assert_not_called()


@pytest.mark.parametrize("entry", ["initial", "guidance", "dedicated"])
def test_new_upload_does_not_replay_an_expired_idempotent_result(tmp_path, monkeypatch, entry):
    from types import SimpleNamespace
    from provider.protocol.models import TextContent
    from run.conversation.guidance import GuidanceInput
    from run.conversation.guidance_runtime import prepare_guidance
    from run.conversation.media_routing import prepare_provider_request
    from run.extensions import describe_local_asset
    from tests.plugins.test_media_loading_parity import LoadingProvider

    class IdempotentProvider(LoadingProvider):
        def __init__(self):
            super().__init__()
            self.by_key = {}

        def upload_asset(self, path, **kwargs):
            key = kwargs["idempotency_key"]
            if key not in self.by_key:
                remote = super().upload_asset(path, **kwargs)
                remote.expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
                self.by_key[key] = remote
            return self.by_key[key]

        def wait_asset_ready(self, value, **kwargs):
            if value.expires_at <= datetime.now(timezone.utc):
                raise ProviderError("expired upload", status_code=410, retryable=False)
            return value

    config, image = make_root(tmp_path, provider_type="kemo")
    user_path = tmp_path / "users/alice/user_config.yaml"
    user = json.loads(user_path.read_text("utf-8"))
    user["multimodal_models"] = {"vision": "analysis-model"}
    user_path.write_text(json.dumps(user), "utf-8")
    descriptor = describe_local_asset(tmp_path, "alice", {"path": str(image)})
    provider = IdempotentProvider()
    monkeypatch.setattr(tool, "create_provider", lambda _: provider)
    context = {"root": str(tmp_path), "user": "alice", "source": "web",
               "session_id": "session", "uploaded_files": [descriptor]}

    def submit():
        if entry == "initial":
            prepare_provider_request(
                {"uploaded_files": [descriptor]}, base=tmp_path, user="alice", source="web",
                session_id="session", content_blocks=[TextContent(text="inspect")],
                dependencies=SimpleNamespace(provider_factory=lambda _: provider), cancel_event=None,
            )
        elif entry == "guidance":
            prepare_guidance(
                [GuidanceInput(text="inspect", uploaded_files=[descriptor])],
                root=tmp_path, user="alice", source="web", session_id="session", config=config,
                runtime_provider=config["provider"], provider=provider,
            )
        else:
            tool.run("analyze_image", asset_ids=[descriptor["asset_id"]], instruction="inspect", context=context)

    submit()
    next(iter(provider.by_key.values())).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    submit()
    assert len(provider.uploads) == len(provider.by_key) == 2


def test_registering_an_output_preserves_publication_limits_and_checks_input_on_reuse(tmp_path, monkeypatch):
    from run.extensions import attachments

    config, _ = make_root(tmp_path, provider_type="kemo")
    path = tmp_path / "users/alice/download/large.txt"
    data = b"published document larger than model input limit"
    path.write_bytes(data)
    monkeypatch.setitem(attachments._MEDIA_LIMITS, "file", 8)
    artifact = {"asset_id": "expand_large", "project_path": path.relative_to(tmp_path).as_posix(),
                "checksum_sha256": hashlib.sha256(data).hexdigest()}
    saved = cache.retain_media_assets(tmp_path, "alice", [artifact], source="web", session_id="session", ttl_seconds=120)
    assert "reference_expires_at" in saved[0]
    with pytest.raises(AttachmentError, match="超过"):
        enqueue(make_host(tmp_path, config), {"asset_ids": [artifact["asset_id"]]})
    assert path.read_bytes() == data
