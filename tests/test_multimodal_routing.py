from __future__ import annotations

import json
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.adapters.compat import chat_response_to_kemo, kemo_request_to_chat
from provider.protocol.enums import MessagePhase, MessageRole, ResponseStatus
from provider.protocol.assets import AssetDescriptor
from provider.protocol.models import ImageContent, KemoResponse, MessageItem, ModelCapabilities, TextContent
from provider.schema import ChatResponse
from run.attachments import AttachmentError, UploadedAssetResolver, describe_uploaded_asset
from run.engine import handle_request
from run.context import estimate_messages_tokens
from run.multimodal import configured_input_modalities, select_vision_route


_PNG = b"\x89PNG\r\n\x1a\n" + b"test-image-payload"


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
        self.assertEqual(fake.request.model, "vision-model")

    def test_kemo_generation_downloads_verified_artifact(self) -> None:
        root, _ = self.make_root()
        config_path = root / "users" / "alice" / "user_config.json"
        config = json.loads(config_path.read_text("utf-8"))
        config["provider"]["type"] = "kemo"
        config["multimodal_models"]["image_generation"] = "image-model"
        config_path.write_text(json.dumps(config), "utf-8")
        from plugins.multimodal import tool

        payload = b"\x89PNG\r\n\x1a\n" + b"generated-image"
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
