from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from provider.protocol.assets import AssetDescriptor
from provider.protocol.models import ModelCapabilities
from run.extensions import describe_uploaded_asset
from run.conversation import GuidanceInput
from run.conversation import prepare_guidance
from run.extensions import clear_model_capability_cache


class _KemoMediaProvider:
    def __init__(self) -> None:
        self.uploads: list[str] = []

    def capabilities(self, model: str) -> ModelCapabilities:
        return ModelCapabilities(
            model=model,
            input_modalities=["text", "image", "audio", "video", "file"],
            output_modalities=["text"],
        )

    def upload_asset(
        self,
        path: Path,
        *,
        metadata,
        idempotency_key: str,
        checksum_sha256: str,
        mime_type: str,
        cancel_event=None,
    ) -> AssetDescriptor:
        del idempotency_key, cancel_event
        self.uploads.append(path.name)
        return AssetDescriptor(
            id=f"asset_remote_{len(self.uploads)}",
            status="ready",
            purpose="input",
            filename=path.name,
            mime_type=mime_type,
            size=path.stat().st_size,
            checksum_sha256=checksum_sha256,
            metadata=dict(metadata),
        )

    def wait_asset_ready(self, asset: AssetDescriptor, *, cancel_event=None) -> AssetDescriptor:
        del cancel_event
        return asset


class GuidanceRuntimeTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_model_capability_cache()

    def test_kemo_guidance_routes_audio_video_and_file_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload = root / "users" / "alice" / "file_upload"
            upload.mkdir(parents=True)
            (upload / "voice.mp3").write_bytes(b"ID3" + b"\0" * 32)
            (upload / "clip.mp4").write_bytes(b"\0\0\0\x18ftypisom" + b"\0" * 24)
            (upload / "notes.md").write_text("guidance notes", "utf-8")
            descriptors = [
                describe_uploaded_asset(
                    root,
                    "alice",
                    {"path": f"users/alice/file_upload/{name}"},
                )
                for name in ("voice.mp3", "clip.mp4", "notes.md")
            ]
            provider = _KemoMediaProvider()
            runtime_provider = {
                "type": "kemo",
                "base_url": "https://gateway.test",
                "api_key": "test-key",
                "model": "omni-model",
            }
            config = {
                "provider": {
                    **runtime_provider,
                    "input_modalities": ["text", "image", "audio", "video", "file"],
                }
            }

            prepared = prepare_guidance(
                [GuidanceInput(
                    id="guidance_media",
                    text="检查这些媒体",
                    uploaded_files=descriptors,
                )],
                root=root,
                user="alice",
                session_id="session-1",
                config=config,
                runtime_provider=runtime_provider,
                provider=provider,
            )

            self.assertEqual(provider.uploads, ["voice.mp3", "clip.mp4", "notes.md"])
            content = prepared.messages[0]["content"]
            self.assertIsInstance(content, list)
            self.assertEqual(
                [block["type"] for block in content],
                ["text", "audio", "video", "file"],
            )
            self.assertEqual(len(prepared.uploaded_descriptors), 3)
            safe_files = prepared.inputs[0].uploaded_files
            self.assertEqual([item["media_kind"] for item in safe_files], ["audio", "video", "file"])
            self.assertTrue(all("path" not in item for item in safe_files))


if __name__ == "__main__":
    unittest.main()
