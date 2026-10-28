"""Local, network-free fixtures for native media input tests."""
from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any
from types import SimpleNamespace

from provider.adapters.compat import chat_response_to_kemo
from provider.protocol.models import ModelCapabilities
from provider.schema import ChatResponse
from run.extensions import MediaInputHost, clear_model_capability_cache
from run.tools import discover_tools

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PROJECT = Path(__file__).resolve().parents[1]


class MediaProvider:
    def __init__(self, responses=None, *, modalities=None, max_images=16, max_input_tokens=None):
        self.responses = list(responses or [])
        self.requests: list[Any] = []
        self.uploads: list[Any] = []
        self.modalities = modalities or ["text", "image", "audio", "video", "file"]
        self.max_images = max_images
        self.max_input_tokens = max_input_tokens

    def capabilities(self, model, **kwargs):
        return ModelCapabilities.model_validate({
            "protocol_version": "2.0", "model": model,
            "provider_id": "test", "provider_model": model,
            "input_modalities": self.modalities,
            "limits": {"max_images": self.max_images, "max_input_tokens": self.max_input_tokens},
        })

    def create(self, request):
        self.requests.append(request.model_copy(deep=True))
        value = self.responses.pop(0) if self.responses else ChatResponse(text="done")
        if isinstance(value, BaseException):
            raise value
        return chat_response_to_kemo(value, request)

    def upload_asset(self, path, **kwargs):
        self.uploads.append((path, kwargs))
        return SimpleNamespace(id=f"asset_remote_{len(self.uploads)}", expires_at=None)

    def wait_asset_ready(self, value, **kwargs):
        return value


def make_root(root: Path, *, provider_type="chat", route="auto"):
    clear_model_capability_cache()
    for directory in ("config", "users/alice/download", "users/alice/history"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROJECT / "plugins" / "attach_media", root / "plugins" / "attach_media")
    runtime = {
        "type": provider_type, "base_url": "http://127.0.0.1:1/v1",
        "api_key": "test-key", "model": "vision-main", "stream": False,
        "input_modalities": (
            ["text", "image"] if provider_type == "chat"
            else ["text", "image", "audio", "video", "file"]
        ),
    }
    config = {
        "provider": runtime,
        "tools": {"enabled": True, "timeout": 15, "max_iterations": 8},
        "multimodal_routing": {"vision": route},
        "memory": {"enabled": False},
    }
    (root / "config" / "global_config.json").write_text(json.dumps(config), "utf-8")
    (root / "users/alice/user_config.json").write_text(
        json.dumps({
            "schema_version": 1, "provider": runtime,
            "multimodal_routing": {"vision": route},
        }), "utf-8"
    )
    image = root / "users/alice/download/screen.png"
    image.write_bytes(PNG)
    return config, image


def make_host(root: Path, config: dict, provider=None, **kwargs):
    return MediaInputHost(
        root=root, user=kwargs.pop("user", "alice"),
        scope=kwargs.pop("scope", ("main", "web", "session", "run")),
        state=kwargs.pop("state", {}), config=config,
        runtime_provider=kwargs.pop("runtime_provider", config["provider"]),
        provider=provider or MediaProvider(), session_id="session",
        known=kwargs.pop("known", []), user_text=kwargs.pop("user_text", ""),
        **kwargs,
    )


def enqueue(host, arguments, *, records=None, call_id="callid_attach"):
    from run.extensions import request_media_attachment

    return host.accept(
        discover_tools(host.root, host.user).get("attach_media"),
        arguments, request_media_attachment(**arguments),
        call_id=call_id, tool_records=records or [],
    )


def native_blocks(request):
    return [
        block for item in request.input
        for block in getattr(item, "content", [])
        if getattr(block, "type", None) in {"image", "audio", "video", "file"}
    ]
