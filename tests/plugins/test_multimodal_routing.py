from __future__ import annotations

import base64
import json
import hashlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.assets import AssetDescriptor
from provider.protocol.models import (
    ImageContent,
    KemoResponse,
    MessageItem,
    ModelCapabilities,
    TextContent,
    text_from_content,
)
from provider.schema import ChatResponse, ProviderError
from run.extensions import (
    AttachmentError,
    UploadedAssetResolver,
    describe_message_asset,
    describe_uploaded_asset,
)
from run.engine import handle_request
from run.context import estimate_messages_tokens
from run.history import find_window, load_window
from run.extensions import (
    _capability_cache,
    configured_input_modalities,
    select_vision_route,
)


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class RecordingProvider:
    def __init__(self) -> None:
        self.requests = []

    def create(self, request):
        self.requests.append(request)
        return chat_response_to_kemo(
            ChatResponse(text="看到了图片"),
            request,
        )


class CapabilityProvider:
    def __init__(self, modalities: list[str]) -> None:
        self.modalities = modalities
        self.calls = 0

    def capabilities(self, model: str) -> ModelCapabilities:
        self.calls += 1
        return ModelCapabilities(model=model, input_modalities=self.modalities)


class MultimodalRoutingTests(unittest.TestCase):
    def make_root(self, *, input_modalities=None, vision=""):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "config").mkdir()
        (root / "plugins").mkdir()
        for path in (
            root / "shared_skills",
            root / "users" / "alice" / "history",
            root / "users" / "alice" / "file_upload",
        ):
            path.mkdir(parents=True, exist_ok=True)
        (root / "config" / "global_config.json").write_text(
            json.dumps({"tools": {"enabled": False, "max_iterations": 4}}),
            "utf-8",
        )
        provider = {
            "type": "chat",
            "base_url": "http://127.0.0.1:1/v1",
            "api_key": "test-key",
            "model": "main-model",
            "stream": False,
        }
        if input_modalities is not None:
            provider["input_modalities"] = input_modalities
        (root / "users" / "alice" / "user_config.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "provider": provider,
                    "multimodal_models": {"vision": vision},
                    "multimodal_routing": {"vision": "auto"},
                }
            ),
            "utf-8",
        )
        image = root / "users" / "alice" / "file_upload" / "screen.png"
        image.write_bytes(_PNG)
        descriptor = describe_uploaded_asset(
            root,
            "alice",
            {"path": "users/alice/file_upload/screen.png"},
        )
        return root, descriptor

    def test_uploaded_asset_is_stable_and_rejects_spoofed_image(self) -> None:
        root, descriptor = self.make_root()
        repeated = describe_uploaded_asset(root, "alice", descriptor)
        self.assertEqual(descriptor["asset_id"], repeated["asset_id"])
        self.assertEqual(descriptor["mime_type"], "image/png")
        resolver = UploadedAssetResolver(root, "alice", [descriptor])
        image = resolver.image_content(descriptor["asset_id"], provider="chat")
        self.assertEqual(image.mime_type, "image/png")
        (root / descriptor["path"]).write_bytes(b"not a png")
        changed = describe_uploaded_asset(root, "alice", descriptor)
        with self.assertRaisesRegex(AttachmentError, "真实图片"):
            UploadedAssetResolver(root, "alice", [changed]).image_content(
                changed["asset_id"], provider="chat"
            )

    def test_uploaded_asset_rejects_corrupted_image_with_valid_header(self) -> None:
        root, descriptor = self.make_root()
        (root / descriptor["path"]).write_bytes(
            b"\x89PNG\r\n\x1a\n" + b"corrupted-payload"
        )
        changed = describe_uploaded_asset(root, "alice", descriptor)
        with self.assertRaisesRegex(AttachmentError, "真实图片"):
            UploadedAssetResolver(root, "alice", [changed]).image_content(
                changed["asset_id"], provider="chat"
            )

    def test_chat_image_route_rejects_bmp_with_clear_message(self) -> None:
        from PIL import Image

        root, _ = self.make_root()
        bmp = root / "users" / "alice" / "file_upload" / "screen.bmp"
        Image.new("RGB", (1, 1), color="white").save(bmp, format="BMP")
        descriptor = describe_uploaded_asset(root, "alice", {"path": str(bmp)})
        with self.assertRaisesRegex(AttachmentError, "Chat 图片通道不支持"):
            UploadedAssetResolver(root, "alice", [descriptor]).image_content(
                descriptor["asset_id"], provider="chat"
            )

    def test_uploaded_asset_cannot_escape_user_upload_root(self) -> None:
        root, _ = self.make_root()
        outside = root / "outside.png"
        outside.write_bytes(_PNG)
        with self.assertRaisesRegex(AttachmentError, "file_upload"):
            describe_uploaded_asset(root, "alice", {"path": str(outside)})

    def test_uploaded_asset_rejects_link_inside_upload_root(self) -> None:
        root, _ = self.make_root()
        outside = root / "outside.txt"
        outside.write_text("outside", "utf-8")
        linked = root / "users" / "alice" / "file_upload" / "linked.txt"
        try:
            linked.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"symlink unavailable: {exc}")

        with self.assertRaisesRegex(AttachmentError, "符号链接|目录联接"):
            describe_uploaded_asset(root, "alice", {"path": str(linked)})

    @unittest.skipUnless(os.name == "nt", "Windows path alias regression")
    def test_absolute_windows_upload_path_accepts_temp_directory_alias(self) -> None:
        root, _ = self.make_root()
        uploaded = root / "users" / "alice" / "file_upload" / "absolute.txt"
        uploaded.write_text("hello", "utf-8")

        descriptor = describe_uploaded_asset(root, "alice", {"path": str(uploaded)})

        self.assertEqual(descriptor["name"], "absolute.txt")
        self.assertEqual(descriptor["media_kind"], "file")

    def test_uploaded_audio_video_and_file_are_classified_without_becoming_images(self) -> None:
        root, _ = self.make_root()
        upload = root / "users" / "alice" / "file_upload"
        (upload / "voice.wav").write_bytes(b"RIFF0000WAVEdata")
        (upload / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypisomvideo")
        (upload / "notes.txt").write_text("hello", "utf-8")
        audio = describe_uploaded_asset(root, "alice", {"path": str(upload / "voice.wav")})
        video = describe_uploaded_asset(root, "alice", {"path": str(upload / "clip.mp4")})
        document = describe_uploaded_asset(root, "alice", {"path": str(upload / "notes.txt")})
        self.assertEqual(audio["media_kind"], "audio")
        self.assertTrue(audio["is_audio"])
        self.assertEqual(video["media_kind"], "video")
        self.assertTrue(video["is_video"])
        self.assertEqual(document["media_kind"], "file")
        self.assertFalse(document["is_image"])
        resolver = UploadedAssetResolver(root, "alice", [audio, video, document])
        self.assertEqual(resolver.local_asset(audio["asset_id"], expected_kind="audio")[0].name, "voice.wav")

    def test_full_input_modalities_are_kemo_only(self) -> None:
        self.assertEqual(
            configured_input_modalities(
                {"provider": {"type": "kemo", "input_modalities": ["text", "audio", "video", "file"]}}
            ),
            ("text", "audio", "video", "file"),
        )
        with self.assertRaisesRegex(Exception, "input_modalities"):
            configured_input_modalities(
                {"provider": {"type": "chat", "input_modalities": ["text", "audio"]}}
            )

    def test_chat_main_model_receives_image_only_when_explicitly_declared(self) -> None:
        root, descriptor = self.make_root(input_modalities=["text", "image"])
        provider = RecordingProvider()
        result = handle_request(
            {
                "user": "alice",
                "source": "web",
                "session_id": "vision-main",
                "prompt": "",
                "uploaded_files": [descriptor],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )
        self.assertEqual(result["text"], "看到了图片")
        chat = kemo_request_to_chat(provider.requests[0])
        user_message = next(item for item in reversed(chat.messages) if item["role"] == "user")
        self.assertIsInstance(user_message["content"], list)
        self.assertTrue(any(item.get("type") == "image_url" for item in user_message["content"]))

    def test_attachment_only_round_persists_reference_and_next_round_is_valid(self) -> None:
        root, descriptor = self.make_root(input_modalities=["text", "image"])
        provider = RecordingProvider()

        first = handle_request(
            {
                "user": "alice",
                "source": "web",
                "session_id": "attachment-only-history",
                "prompt": "",
                "uploaded_files": [descriptor],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )
        second = handle_request(
            {
                "user": "alice",
                "source": "web",
                "session_id": "attachment-only-history",
                "prompt": "继续说明上一张图片",
                "uploaded_files": [],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )

        self.assertEqual(first["text"], "看到了图片")
        self.assertEqual(second["text"], "看到了图片")
        historical_messages = [
            item
            for item in provider.requests[1].input
            if isinstance(item, MessageItem)
        ]
        self.assertEqual(
            [str(item.role) for item in historical_messages],
            ["user", "assistant", "user"],
        )
        historical_user_text = text_from_content(historical_messages[0].content)
        self.assertIn("[本轮输入资产]", historical_user_text)
        self.assertIn(descriptor["path"], historical_user_text)

        window_path = find_window(root, "alice", "web", "attachment-only-history")
        self.assertIsNotNone(window_path)
        assert window_path is not None
        window = load_window(window_path)
        user_items = [
            item
            for item in window["items"]["items"]
            if item.get("type") == "message" and item.get("role") == "user"
        ]
        self.assertTrue(user_items[0]["content"])
        persisted = json.dumps(user_items[0]["content"], ensure_ascii=False)
        self.assertIn(descriptor["path"], persisted)
        self.assertNotIn("inline_base64", persisted)

        history_attachment = {
            "asset_id": descriptor["asset_id"],
            "name": descriptor["name"],
            "media_kind": descriptor["media_kind"],
            "mime_type": descriptor["mime_type"],
            "size": descriptor["size"],
            "checksum_sha256": descriptor["checksum_sha256"],
            "scope": "file_upload",
            "relative_path": descriptor["relative_path"],
        }
        self.assertEqual(
            window["text"]["messages"][0]["attachments"],
            [history_attachment],
        )
        self.assertEqual(
            user_items[0]["metadata"]["input_attachments"],
            [history_attachment],
        )
        self.assertEqual(
            window["data"]["round_metrics"][0]["input_attachments"],
            [history_attachment],
        )
        self.assertNotIn("path", history_attachment)

    def test_non_image_attachment_only_round_can_continue(self) -> None:
        root, _ = self.make_root(input_modalities=["text", "image"])
        note = root / "users" / "alice" / "file_upload" / "note.txt"
        note.write_text("attachment body", "utf-8")
        descriptor = describe_uploaded_asset(root, "alice", {"path": str(note)})
        provider = RecordingProvider()

        handle_request(
            {
                "user": "alice",
                "source": "web",
                "session_id": "file-only-history",
                "prompt": "",
                "uploaded_files": [descriptor],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )
        handle_request(
            {
                "user": "alice",
                "source": "web",
                "session_id": "file-only-history",
                "prompt": "继续",
                "uploaded_files": [],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )

        messages = [
            item
            for item in provider.requests[1].input
            if isinstance(item, MessageItem)
        ]
        self.assertEqual([str(item.role) for item in messages], ["user", "assistant", "user"])
        self.assertIn(descriptor["path"], text_from_content(messages[0].content))

    def test_auto_chat_route_uses_dedicated_model_without_explicit_declaration(self) -> None:
        config = {
            "provider": {"type": "chat", "input_modalities": ["text"]},
            "multimodal_routing": {"vision": "auto"},
        }
        self.assertEqual(
            select_vision_route(config, {"type": "chat"}, object()),
            "dedicated",
        )

    def test_context_estimator_does_not_count_base64_as_text(self) -> None:
        small = estimate_messages_tokens([{"role": "user", "content": [{
            "type": "image",
            "source": {"kind": "inline_base64", "data": "a" * 64},
        }]}])
        large = estimate_messages_tokens([{"role": "user", "content": [{
            "type": "image",
            "source": {"kind": "inline_base64", "data": "a" * 1_000_000},
        }]}])
        self.assertEqual(small, large)
        self.assertGreater(small, 1000)

    def test_kemo_auto_route_uses_gateway_capability(self) -> None:
        provider = CapabilityProvider(["text", "image"])
        config = {
            "provider": {"type": "kemo"},
            "multimodal_routing": {"vision": "auto"},
        }
        runtime = {"type": "kemo", "base_url": "http://gateway.test", "model": "vision-main"}
        self.assertEqual(select_vision_route(config, runtime, provider), "main")
        self.assertEqual(provider.calls, 1)

    def test_kemo_explicit_text_only_declaration_skips_gateway_guessing(self) -> None:
        provider = CapabilityProvider(["text", "image"])
        config = {
            "provider": {"type": "kemo", "input_modalities": ["text"]},
            "multimodal_routing": {"vision": "auto"},
        }
        runtime = {"type": "kemo", "base_url": "http://other.test", "model": "text-main"}
        self.assertEqual(select_vision_route(config, runtime, provider), "dedicated")
        self.assertEqual(provider.calls, 0)

    def test_failed_gateway_capability_lookup_is_not_negatively_cached(self) -> None:
        class RecoveringCapabilityProvider:
            def __init__(self) -> None:
                self.calls = 0

            def capabilities(self, model: str) -> ModelCapabilities:
                self.calls += 1
                if self.calls == 1:
                    raise ProviderError("temporary failure", retryable=True)
                return ModelCapabilities(
                    model=model,
                    input_modalities=["text", "image"],
                )

        _capability_cache.clear()
        self.addCleanup(_capability_cache.clear)
        provider = RecoveringCapabilityProvider()
        config = {
            "provider": {"type": "kemo"},
            "multimodal_routing": {"vision": "auto"},
        }
        runtime = {
            "type": "kemo",
            "base_url": "http://recovering-gateway.test",
            "model": "vision-main",
        }
        self.assertEqual(select_vision_route(config, runtime, provider), "dedicated")
        self.assertEqual(select_vision_route(config, runtime, provider), "main")
        self.assertEqual(provider.calls, 2)

    def test_dedicated_plugin_uses_configured_vision_model(self) -> None:
        root, descriptor = self.make_root(vision="vision-model")
        from plugins.multimodal import tool

        class FakeVisualProvider:
            def __init__(self):
                self.request = None

            def create(self, request):
                self.request = request
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        MessageItem(
                            id="msg_visual_result",
                            role=MessageRole.ASSISTANT,
                            phase=MessagePhase.FINAL_ANSWER,
                            content=[TextContent(text="图片里有一只猫")],
                        )
                    ],
                )

        fake = FakeVisualProvider()
        with patch.object(tool, "create_provider", return_value=fake):
            result = tool.run(
                "analyze_image",
                [descriptor["asset_id"]],
                "描述主体",
                context={
                    "root": str(root),
                    "user": "alice",
                    "source": "web",
                    "session_id": "dedicated",
                    "uploaded_files": [descriptor],
                },
            )
        self.assertEqual(result["analysis"], "图片里有一只猫")
        self.assertEqual(result["model"], "vision-model")
        self.assertEqual(result["attempts"], 1)
        self.assertEqual(fake.request.model, "vision-model")
        self.assertEqual(fake.request.generation.max_output_tokens, 10_000)

    def test_dedicated_analysis_retries_one_transient_provider_error(self) -> None:
        root, descriptor = self.make_root(vision="vision-model")
        from plugins.multimodal import tool

        class RecoveringVisualProvider:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, request):
                self.calls += 1
                if self.calls == 1:
                    raise ProviderError(
                        "temporary upstream failure",
                        category="upstream_error",
                        status_code=502,
                        retryable=True,
                    )
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[MessageItem(
                        id="msg_recovered_visual_result",
                        role=MessageRole.ASSISTANT,
                        phase=MessagePhase.FINAL_ANSWER,
                        content=[TextContent(text="第二次识别成功")],
                    )],
                )

        fake = RecoveringVisualProvider()
        with (
            patch.object(tool, "create_provider", return_value=fake),
            patch.object(tool, "_wait_before_retry"),
        ):
            result = tool.run(
                "analyze_image",
                [descriptor["asset_id"]],
                "描述图片",
                context={
                    "root": str(root),
                    "user": "alice",
                    "source": "web",
                    "session_id": "retry-vision",
                    "uploaded_files": [descriptor],
                },
            )
        self.assertEqual(fake.calls, 2)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["analysis"], "第二次识别成功")

    def test_kemo_dedicated_analysis_resolves_gateway_asset_outside_current_run(self) -> None:
        root, _ = self.make_root(vision="vision-model")
        config_path = root / "users" / "alice" / "user_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["provider"]["type"] = "kemo"
        config["provider"]["base_url"] = "http://gateway.test"
        config_path.write_text(json.dumps(config), "utf-8")
        from plugins.multimodal import tool

        descriptor = AssetDescriptor(
            id="asset_gateway_image",
            status="ready",
            purpose="input",
            filename="remote.png",
            mime_type="image/png",
            size=len(_PNG),
            checksum_sha256="a" * 64,
        )

        class GatewayProvider:
            def __init__(self) -> None:
                self.request = None
                self.get_calls = []
                self.upload_calls = 0

            def capabilities(self, model: str) -> ModelCapabilities:
                return ModelCapabilities(
                    model=model,
                    input_modalities=["text", "image"],
                    output_modalities=["text"],
                    extensions={"operations": {"vision": {"supported": True}}},
                )

            def get_asset(self, asset_id: str) -> AssetDescriptor:
                self.get_calls.append(asset_id)
                return descriptor

            def wait_asset_ready(self, asset, *, cancel_event=None):
                return asset

            def upload_asset(self, *args, **kwargs):
                self.upload_calls += 1
                raise AssertionError("已有网关 Asset 不应再次上传")

            def create(self, request):
                self.request = request
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[
                        MessageItem(
                            id="msg_remote_visual_result",
                            role=MessageRole.ASSISTANT,
                            phase=MessagePhase.FINAL_ANSWER,
                            content=[TextContent(text="远程 Asset 已识别")],
                        )
                    ],
                )

        fake = GatewayProvider()
        with patch.object(tool, "create_provider", return_value=fake):
            result = tool.run(
                "analyze_image",
                [descriptor.id],
                "描述远程图片",
                context={
                    "root": str(root),
                    "user": "alice",
                    "source": "web",
                    "session_id": "remote-asset",
                    "uploaded_files": [],
                },
            )

        self.assertEqual(fake.get_calls, [descriptor.id])
        self.assertEqual(fake.upload_calls, 0)
        media = fake.request.input[0].content[1]
        self.assertIsInstance(media, ImageContent)
        self.assertEqual(media.asset_id, descriptor.id)
        self.assertEqual(media.checksum_sha256, descriptor.checksum_sha256)
        self.assertEqual(result["analysis"], "远程 Asset 已识别")

    def test_dedicated_analysis_does_not_retry_non_transient_error(self) -> None:
        root, descriptor = self.make_root(vision="vision-model")
        from plugins.multimodal import tool

        class RejectingVisualProvider:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, _request):
                self.calls += 1
                raise ProviderError(
                    "invalid image",
                    category="invalid_request",
                    status_code=400,
                    retryable=False,
                )

        fake = RejectingVisualProvider()
        with patch.object(tool, "create_provider", return_value=fake):
            with self.assertRaises(ProviderError) as raised:
                tool.run(
                    "analyze_image",
                    [descriptor["asset_id"]],
                    "描述图片",
                    context={
                        "root": str(root),
                        "user": "alice",
                        "source": "web",
                        "session_id": "reject-vision",
                        "uploaded_files": [descriptor],
                    },
                )
        self.assertEqual(fake.calls, 1)
        self.assertEqual(raised.exception.attempt_count, 1)

    def test_generation_action_does_not_retry_even_when_error_is_transient(self) -> None:
        from plugins.multimodal import tool

        class FailingProvider:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, _request):
                self.calls += 1
                raise ProviderError(
                    "temporary upstream failure",
                    status_code=503,
                    retryable=True,
                )

        provider = FailingProvider()
        with self.assertRaises(ProviderError) as raised:
            tool._create_with_retry(
                provider,
                object(),
                config={},
                action="generate_image",
                cancel_event=None,
            )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(raised.exception.attempt_count, 1)

    def test_cancellation_stops_multimodal_retry_before_second_attempt(self) -> None:
        from plugins.multimodal import tool

        class FailingProvider:
            def __init__(self) -> None:
                self.calls = 0

            def create(self, _request):
                self.calls += 1
                raise ProviderError("temporary failure", retryable=True)

        provider = FailingProvider()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(ProviderError, "已取消"):
            tool._create_with_retry(
                provider,
                object(),
                config={},
                action="analyze_image",
                cancel_event=cancel,
            )
        self.assertEqual(provider.calls, 1)

    def test_multimodal_timeout_uses_tool_budget_unless_explicitly_configured(self) -> None:
        from plugins.multimodal import tool

        self.assertEqual(
            tool._multimodal_provider_timeout(
                {"provider": {"type": "chat"}},
                {"timeout": 120},
                {"tool_timeout": 240},
            ),
            235,
        )
        self.assertEqual(
            tool._multimodal_provider_timeout(
                {"provider": {"type": "chat", "timeout": 75}},
                {"timeout": 75},
                {"tool_timeout": 240},
            ),
            75,
        )

    def test_dedicated_plugin_accepts_absolute_image_path_as_direct_input(self) -> None:
        root, _ = self.make_root(vision="vision-model")
        from plugins.multimodal import tool

        absolute_image = root / "absolute-screen.png"
        absolute_image.write_bytes(_PNG)

        class FakeVisualProvider:
            def __init__(self):
                self.request = None

            def create(self, request):
                self.request = request
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[MessageItem(
                        id="msg_absolute_visual_result",
                        role=MessageRole.ASSISTANT,
                        phase=MessagePhase.FINAL_ANSWER,
                        content=[TextContent(text="绝对路径图片已识别")],
                    )],
                )

        fake = FakeVisualProvider()
        with patch.object(tool, "create_provider", return_value=fake):
            result = tool.run(
                "analyze_image",
                instruction="描述图片",
                paths=[str(absolute_image.resolve())],
                context={
                    "root": str(root),
                    "user": "alice",
                    "source": "cli",
                    "session_id": "absolute-path",
                    "uploaded_files": [],
                },
            )
        media = fake.request.input[0].content[1]
        self.assertIsInstance(media, ImageContent)
        self.assertEqual(media.source.kind, "inline_base64")
        self.assertEqual(result["paths"], [str(absolute_image.resolve())])
        self.assertRegex(result["asset_ids"][0], r"^asset_[0-9a-f]{32}$")

    def test_inline_image_is_not_sent_to_text_only_main_model(self) -> None:
        root, _ = self.make_root(input_modalities=["text"], vision="vision-model")
        provider = RecordingProvider()
        with self.assertRaisesRegex(Exception, "inline content 图片不能直接发送"):
            handle_request(
                {
                    "user": "alice",
                    "source": "cli",
                    "session_id": "inline-image-text-main",
                    "content": [{
                        "type": "image",
                        "source": {
                            "kind": "inline_base64",
                            "data": "iVBORw0KGgp0ZXN0LWltYWdlLXBheWxvYWQ=",
                        },
                        "mime_type": "image/png",
                    }],
                    "stream": False,
                },
                root=root,
                provider_factory=lambda _: provider,
            )
        self.assertEqual(provider.requests, [])

    def test_external_message_asset_uses_dedicated_route_without_image_on_main(self) -> None:
        root, _ = self.make_root(input_modalities=["text"], vision="vision-model")
        plugin = root / "message" / "out" / "qq"
        files = plugin / "files"
        files.mkdir(parents=True)
        (plugin / "message.json").write_text(
            json.dumps({"bound_user": "alice", "files_dir": "files"}),
            "utf-8",
        )
        external_image = files / "photo.png"
        external_image.write_bytes(_PNG)
        descriptor = describe_message_asset(
            root,
            "alice",
            {"path": "files/photo.png", "name": "photo.png"},
            source="qq",
        )
        provider = RecordingProvider()
        result = handle_request(
            {
                "user": "alice",
                "source": "message:qq",
                "session_id": "external-image",
                "prompt": "识别图片",
                "uploaded_files": [descriptor],
                "stream": False,
            },
            root=root,
            provider_factory=lambda _: provider,
        )
        self.assertEqual(result["text"], "看到了图片")
        chat = kemo_request_to_chat(provider.requests[0])
        user_message = next(
            item for item in reversed(chat.messages) if item["role"] == "user"
        )
        self.assertIsInstance(user_message["content"], str)
        self.assertIn(descriptor["asset_id"], user_message["content"])
        self.assertIn("需要查看时调用 multimodal 工具", user_message["content"])

    def test_kemo_generation_downloads_verified_artifact(self) -> None:
        root, _ = self.make_root()
        config_path = root / "users" / "alice" / "user_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["provider"]["type"] = "kemo"
        config["multimodal_models"]["image_generation"] = "image-model"
        config_path.write_text(json.dumps(config), "utf-8")
        from plugins.multimodal import tool

        payload = _PNG
        checksum = hashlib.sha256(payload).hexdigest()

        class FakeImageProvider:
            def capabilities(self, model):
                return ModelCapabilities(
                    model=model,
                    input_modalities=["text"],
                    output_modalities=["image"],
                    extensions={"operations": {"image_generation": {"supported": True}}},
                )

            def create(self, request):
                return KemoResponse(
                    request_id=request.request_id,
                    status=ResponseStatus.COMPLETED,
                    model=request.model,
                    output=[MessageItem(
                        id="msg_image_result",
                        role=MessageRole.ASSISTANT,
                        phase=MessagePhase.FINAL_ANSWER,
                        content=[ImageContent(
                            asset_id="asset_generated",
                            mime_type="image/png",
                            checksum_sha256=checksum,
                        )],
                    )],
                )

            def get_asset(self, asset_id):
                return AssetDescriptor(
                    id=asset_id,
                    status="ready",
                    purpose="output",
                    filename="result.png",
                    mime_type="image/png",
                    size=len(payload),
                    checksum_sha256=checksum,
                )

            def wait_asset_ready(self, asset, **_kwargs):
                return asset

            def download_asset(self, _asset_id, destination, **_kwargs):
                Path(destination).write_bytes(payload)
                return Path(destination)

        with patch.object(tool, "create_provider", return_value=FakeImageProvider()):
            result = tool.run(
                "generate_image",
                instruction="生成一个测试图片",
                context={
                    "root": str(root),
                    "user": "alice",
                    "source": "web",
                    "session_id": "media-generation",
                    "uploaded_files": [],
                },
            )
        self.assertEqual(result["artifacts"][0]["path"], "result.png")
        self.assertEqual((root / "users" / "alice" / "download" / "result.png").read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
