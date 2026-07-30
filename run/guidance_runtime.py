"""Prepare structured run-time guidance for the provider/tool boundary."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from provider.factory import provider_request_slot
from provider.protocol.models import (
    AudioContent,
    FileContent,
    ImageContent,
    TextContent,
    VideoContent,
)
from provider.schema import ProviderError
from run.attachments import (
    AttachmentError,
    RunAssetResolver,
    history_attachment_descriptors,
)
from run.guidance import GuidanceInput, normalize_guidance
from run.multimodal import main_model_supports_input, select_vision_route
from run.request_input import content_for_message, uploaded_file_context


@dataclass(slots=True)
class PreparedGuidance:
    messages: list[dict[str, Any]] = field(default_factory=list)
    inputs: list[GuidanceInput] = field(default_factory=list)
    uploaded_descriptors: list[dict[str, Any]] = field(default_factory=list)
    history_details: list[dict[str, Any]] = field(default_factory=list)
    direct_asset_ids: set[str] = field(default_factory=set)


def _descriptor_key(value: dict[str, Any]) -> str:
    return str(value.get("asset_id") or "").strip()


def prepare_guidance(
    values: Iterable[Any],
    *,
    root: Path,
    user: str,
    session_id: str,
    config: dict[str, Any],
    runtime_provider: dict[str, Any],
    provider: Any,
    cancel_event: Any = None,
    known_descriptors: Iterable[dict[str, Any]] = (),
    remote_assets: dict[str, str] | None = None,
) -> PreparedGuidance:
    """Validate and turn guidance envelopes into provider messages.

    The same rules as the initial Web input are used: images can be sent to a
    capable Chat/Kemo model, Kemo can receive other supported media through
    its Asset API, and unsupported media remains available to the multimodal
    or file tools through the run-scoped descriptor list.
    """

    result = PreparedGuidance()
    known_ids = {
        _descriptor_key(item)
        for item in known_descriptors
        if isinstance(item, dict) and _descriptor_key(item)
    }
    prepared_ids: set[str] = set()
    remote_assets = remote_assets if remote_assets is not None else {}

    for raw in values:
        item = normalize_guidance(raw)
        if item is None:
            continue
        descriptors = [
            dict(value)
            for value in item.uploaded_files
            if isinstance(value, dict)
        ]
        resolver = RunAssetResolver(root, user, descriptors)
        verified_descriptors: list[dict[str, Any]] = []
        for descriptor in descriptors:
            asset_id = _descriptor_key(descriptor)
            if not asset_id:
                raise AttachmentError("运行中引导附件缺少 asset_id")
            media_kind = str(descriptor.get("media_kind") or "file")
            # Revalidate every kind, including files that will only be exposed
            # to a tool.  This prevents a queued envelope from smuggling an
            # arbitrary path after the upload has been removed or replaced.
            _, verified = resolver.local_asset(asset_id, expected_kind=media_kind)
            verified_descriptors.append(copy.deepcopy(verified))

        image_descriptors = [
            value
            for value in verified_descriptors
            if str(value.get("media_kind") or "") == "image"
            or bool(value.get("is_image"))
        ]
        vision_route: str | None = None
        if image_descriptors:
            vision_route = select_vision_route(
                config,
                runtime_provider,
                provider,
                cancel_event=cancel_event,
            )

        provider_media: list[
            ImageContent | AudioContent | VideoContent | FileContent
        ] = []
        direct_asset_ids: set[str] = set()
        if image_descriptors and vision_route == "main":
            if str(runtime_provider.get("type") or "") == "chat":
                provider_media.extend(
                    resolver.image_content(str(value["asset_id"]), provider="chat")
                    for value in image_descriptors
                )
                direct_asset_ids.update(
                    str(value["asset_id"]) for value in image_descriptors
                )

        if verified_descriptors and str(runtime_provider.get("type") or "") == "kemo":
            upload_asset = getattr(provider, "upload_asset", None)
            wait_asset_ready = getattr(provider, "wait_asset_ready", None)
            for value in verified_descriptors:
                asset_id = _descriptor_key(value)
                media_kind = str(value.get("media_kind") or "file")
                should_direct = (
                    vision_route == "main"
                    if media_kind == "image"
                    else main_model_supports_input(
                        config,
                        runtime_provider,
                        provider,
                        media_kind,
                        cancel_event=cancel_event,
                    )
                )
                if not should_direct:
                    continue
                if not callable(upload_asset) or not callable(wait_asset_ready):
                    raise ProviderError("Kemo Provider 未实现完整多模态 Asset 客户端")
                remote_id = remote_assets.get(asset_id)
                if not remote_id:
                    path, checked = resolver.local_asset(
                        asset_id,
                        expected_kind=media_kind,
                    )
                    with provider_request_slot(config, cancel_event=cancel_event):
                        remote = upload_asset(
                            path,
                            metadata={
                                "user": user,
                                "session_id": session_id,
                                "purpose": "guidance",
                                "capability": "conversation",
                            },
                            idempotency_key=asset_id,
                            checksum_sha256=str(checked["checksum_sha256"]),
                            mime_type=str(checked["mime_type"]),
                            cancel_event=cancel_event,
                        )
                        remote = wait_asset_ready(remote, cancel_event=cancel_event)
                    remote_id = str(remote.id)
                    remote_assets[asset_id] = remote_id
                provider_media.append(
                    resolver.remote_content(
                        asset_id,
                        remote_asset_id=remote_id,
                    )
                )
                direct_asset_ids.add(asset_id)

        context = uploaded_file_context(
            {"uploaded_files": verified_descriptors},
            vision_route=vision_route,
            direct_asset_ids=direct_asset_ids,
        )
        label = item.text.strip() or "请处理本次新增的输入资产。"
        text = f"[运行中引导]\n- {label}"
        if context:
            text += context
        blocks: list[Any] = [TextContent(text=text)]
        blocks.extend(provider_media)
        result.messages.append(
            {"role": "user", "content": content_for_message(blocks)}
        )
        safe_attachments = history_attachment_descriptors(verified_descriptors)
        acknowledged = GuidanceInput(
            id=item.id,
            text=item.text,
            uploaded_files=safe_attachments,
        )
        result.inputs.append(acknowledged)
        result.history_details.append(acknowledged.history_detail())
        result.direct_asset_ids.update(direct_asset_ids)
        for descriptor in verified_descriptors:
            key = _descriptor_key(descriptor)
            if key and key not in known_ids and key not in prepared_ids:
                result.uploaded_descriptors.append(descriptor)
                prepared_ids.add(key)

    return result
