"""Native input capability checks and account-scoped media preparation."""

from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import uuid
from typing import Any, Iterable

from provider.factory import provider_request_slot
from run.extensions.attachments import AttachmentError, RunAssetResolver
from run.extensions.model_capabilities import (
    capability_cache_identity,
    lookup_model_capabilities,
)
from run.extensions.multimodal import main_model_supports_input, select_vision_route


class NativeMediaTransport:
    def __init__(self) -> None:
        self.remote: dict[tuple[Any, ...], Any] = {}
        self.generations: dict[tuple[Any, ...], str] = {}

    def validate(
        self, entries: Iterable[Any], *, config: dict[str, Any],
        runtime_provider: dict[str, Any], provider: Any,
        cancel_event: Any = None, input_limit: int | None = None,
    ) -> None:
        values = list(entries)
        if cancel_event is not None and cancel_event.is_set():
            raise AttachmentError("媒体装载已取消")
        if not values:
            return
        # A subagent may select a different model/provider. Never borrow the
        # primary model's explicit modality declaration for its actual runtime.
        config = {
            **config,
            "provider": {
                **(config.get("provider") or {}), **runtime_provider,
            },
        }
        kinds = {str(entry.descriptor["media_kind"]) for entry in values}
        if "image" in kinds and select_vision_route(
            config, runtime_provider, provider, cancel_event=cancel_event
        ) != "main":
            raise AttachmentError(
                "当前视觉路由不允许主模型原生看图；请配置主模型图片能力，"
                "或明确选择 multimodal 专用模型工具"
            )
        for kind in kinds - {"image"}:
            if not main_model_supports_input(
                config, runtime_provider, provider, kind, cancel_event=cancel_event
            ):
                raise AttachmentError(f"当前主模型/通道不支持原生 {kind} 输入")
        provider_type = str(runtime_provider.get("type") or "").casefold()
        if provider_type == "chat":
            if kinds != {"image"}:
                raise AttachmentError("Chat 通道只支持原生图片输入")
            if any(
                entry.descriptor["mime_type"] not in {
                    "image/png", "image/jpeg", "image/webp", "image/gif"
                } for entry in values
            ):
                raise AttachmentError("Chat 图片通道只支持 JPEG、PNG、WEBP 和 GIF")
        elif provider_type == "kemo":
            if not all(callable(getattr(provider, name, None)) for name in (
                "upload_asset", "wait_asset_ready"
            )):
                raise AttachmentError("Kemo Provider 未实现原生媒体 Asset 客户端")
            declared = lookup_model_capabilities(
                config, runtime_provider, provider, cancel_event=cancel_event
            ).capabilities
            limit = declared.limits.max_images if declared is not None else None
            if limit is not None and sum(
                entry.descriptor["media_kind"] == "image" for entry in values
            ) > limit:
                raise AttachmentError(f"图片数量超过当前模型上限 {limit}")
        else:
            raise AttachmentError("当前 Provider 不支持原生媒体装载")
        # A media-only input that already exceeds the hard limit cannot be
        # rescued by dropping history. Full context guarding remains in host.
        reserved = sum(
            1024 + len(entry.note) + len(str(entry.descriptor["name"]))
            for entry in values
        )
        if input_limit is not None and reserved >= input_limit:
            raise AttachmentError("装载媒体超过当前请求上下文预算")

    def validate_request(
        self, messages: list[dict[str, Any]], *, config: dict[str, Any],
        runtime_provider: dict[str, Any], provider: Any, cancel_event: Any = None,
    ) -> None:
        if str(runtime_provider.get("type") or "").casefold() != "kemo":
            return
        declared = lookup_model_capabilities(
            config, runtime_provider, provider, cancel_event=cancel_event
        ).capabilities
        limit = declared.limits.max_images if declared is not None else None
        count = sum(
            block.get("type") == "image"
            for message in messages
            for block in (message.get("content") or [])
            if isinstance(block, dict)
        )
        if limit is not None and count > limit:
            raise AttachmentError(f"请求图片总数超过当前模型上限 {limit}")

    def content(
        self, entry: Any, *, resolver: RunAssetResolver,
        verified: dict[str, Any], config: dict[str, Any],
        runtime_provider: dict[str, Any], provider: Any, user: str,
        session_id: str, cancel_event: Any = None,
    ) -> dict[str, Any]:
        asset_id = str(verified["asset_id"])
        if str(runtime_provider.get("type") or "").casefold() == "chat":
            block = resolver.image_content(
                asset_id, provider="chat", detail=entry.detail
            )
            assert block.source is not None and block.source.data is not None
            if hashlib.sha256(base64.b64decode(block.source.data)).hexdigest() != verified["checksum_sha256"]:
                raise AttachmentError("图片在编码期间发生变化，请重新登记")
            return block.model_dump(mode="json", exclude_none=True)
        key = (
            *capability_cache_identity(runtime_provider), str(runtime_provider.get("model")),
            user, asset_id, str(verified["checksum_sha256"]),
        )
        remote = self.remote.get(key)
        expires_at = getattr(remote, "expires_at", None)
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            remote = None
            # Expired uploads must not replay the gateway's old idempotent result.
            self.remote.pop(key, None)
            self.generations[key] = "_" + uuid.uuid4().hex
        if remote is None:
            path, _ = resolver.local_asset(asset_id)
            with provider_request_slot(config, cancel_event=cancel_event):
                remote = provider.upload_asset(
                    path,
                    metadata={
                        "user": user, "session_id": session_id,
                        "purpose": "input", "capability": "conversation",
                    },
                    idempotency_key=(
                        entry.attachment_id + "_" + asset_id + self.generations.get(key, "")
                    ),
                    checksum_sha256=str(verified["checksum_sha256"]),
                    mime_type=str(verified["mime_type"]),
                    cancel_event=cancel_event,
                )
                remote = provider.wait_asset_ready(remote, cancel_event=cancel_event)
            checksum = getattr(remote, "checksum_sha256", None)
            if checksum is not None and checksum != verified["checksum_sha256"]:
                raise AttachmentError("网关媒体校验和与本地文件不一致")
            self.remote[key] = remote
        block = resolver.remote_content(
            asset_id, remote_asset_id=str(getattr(remote, "id"))
        )
        if verified["media_kind"] == "image":
            block = block.model_copy(update={"detail": entry.detail})
        return block.model_dump(mode="json", exclude_none=True)
