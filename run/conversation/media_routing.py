"""Media and provider request preparation for conversation runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class PreparedProviderRequest:
    config: dict[str, Any]
    context_policy: Any
    source_policy: Any
    runtime_provider: dict[str, Any]
    provider: Any
    uploaded_descriptors: list[dict[str, Any]]
    history_attachments: list[dict[str, Any]]
    provider_media: list[Any]
    direct_asset_ids: set[str]
    vision_route: str | None
    uploaded_file_context: str
    durable_user_content_blocks: list[Any]
    agent_runner: Any
    window_path: Path
    archive_window: dict[str, Any]

def prepare_provider_request(
    request: dict[str, Any],
    *,
    base: Path,
    user: str,
    source: str,
    session_id: str,
    content_blocks: list[Any],
    dependencies: Any,
    cancel_event: Any,
) -> PreparedProviderRequest:
    import importlib
    _runtime = importlib.import_module("run.conversation.runtime")
    AgentRunner = _runtime.AgentRunner
    AttachmentError = _runtime.AttachmentError
    AudioContent = _runtime.AudioContent
    ContextPolicy = _runtime.ContextPolicy
    EngineError = _runtime.EngineError
    FileContent = _runtime.FileContent
    ImageContent = _runtime.ImageContent
    MainAgentSourcePolicy = _runtime.MainAgentSourcePolicy
    ProviderError = _runtime.ProviderError
    RunAssetResolver = _runtime.RunAssetResolver
    TextContent = _runtime.TextContent
    VideoContent = _runtime.VideoContent
    _content_display = _runtime._content_display
    history_attachment_descriptors = _runtime.history_attachment_descriptors
    load_config = _runtime.load_config
    main_model_supports_input = _runtime.main_model_supports_input
    prepare_window = _runtime.prepare_window
    project_root = _runtime.project_root
    provider_request_slot = _runtime.provider_request_slot
    provider_runtime_config = _runtime.provider_runtime_config
    select_vision_route = _runtime.select_vision_route
    _uploaded_file_context = _runtime._uploaded_file_context

    config = load_config(user, base)
    context_policy = ContextPolicy.from_config(config)
    source_policy = MainAgentSourcePolicy.from_config(config)
    runtime_provider = provider_runtime_config(config)
    provider = dependencies.provider_factory(runtime_provider)
    uploaded_descriptors = [
        dict(item)
        for item in (request.get("uploaded_files") or [])
        if isinstance(item, dict)
    ]
    history_attachments = history_attachment_descriptors(uploaded_descriptors)
    image_descriptors = [
        item
        for item in uploaded_descriptors
        if str(item.get("media_kind") or "") == "image"
        or bool(item.get("is_image"))
    ]
    inline_images = [
        item for item in content_blocks if isinstance(item, ImageContent)
    ]
    vision_route: str | None = None
    provider_media: list[ImageContent | AudioContent | VideoContent | FileContent] = []
    direct_asset_ids: set[str] = set()
    resolver = RunAssetResolver(base, user, uploaded_descriptors)
    if image_descriptors or inline_images:
        vision_route = select_vision_route(
            config,
            runtime_provider,
            provider,
            cancel_event=cancel_event,
        )
        if vision_route == "dedicated" and inline_images:
            raise EngineError(
                "主模型未声明图片输入能力，inline content 图片不能直接发送；"
                "请将图片登记为运行资产，或把本地路径交给 multimodal 工具"
            )
        if vision_route == "main":
            try:
                if str(runtime_provider.get("type") or "") == "chat":
                    provider_media.extend(
                        resolver.image_content(
                            str(item["asset_id"]),
                            provider="chat",
                        )
                        for item in image_descriptors
                    )
                    direct_asset_ids.update(
                        str(item["asset_id"]) for item in image_descriptors
                    )
            except AttachmentError as exc:
                raise EngineError(str(exc)) from None
    inline_media_kinds = {
        "audio"
        if isinstance(item, AudioContent)
        else "video"
        if isinstance(item, VideoContent)
        else "file"
        for item in content_blocks
        if isinstance(item, (AudioContent, VideoContent, FileContent))
    }
    for media_kind in sorted(inline_media_kinds):
        if not main_model_supports_input(
            config,
            runtime_provider,
            provider,
            media_kind,
            cancel_event=cancel_event,
        ):
            raise EngineError(
                f"主模型未声明 {media_kind} 输入能力，不能直接接收 inline content；"
                "请将媒体登记为运行资产后调用 multimodal 工具"
            )
    if uploaded_descriptors and str(runtime_provider.get("type") or "") == "kemo":
        upload_asset = getattr(provider, "upload_asset", None)
        wait_asset_ready = getattr(provider, "wait_asset_ready", None)
        for item in uploaded_descriptors:
            asset_id = str(item.get("asset_id") or "")
            media_kind = str(item.get("media_kind") or "file")
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
            if not asset_id or not should_direct:
                continue
            if not callable(upload_asset) or not callable(wait_asset_ready):
                raise EngineError("Kemo Provider 未实现完整多模态 Asset 客户端")
            try:
                path, verified = resolver.local_asset(
                    asset_id, expected_kind=media_kind
                )
                with provider_request_slot(config, cancel_event=cancel_event):
                    remote = upload_asset(
                        path,
                        metadata={
                            "user": user,
                            "session_id": session_id,
                            "purpose": "input",
                            "capability": "conversation",
                        },
                        idempotency_key=asset_id,
                        checksum_sha256=str(verified["checksum_sha256"]),
                        mime_type=str(verified["mime_type"]),
                        cancel_event=cancel_event,
                    )
                    remote = wait_asset_ready(
                        remote,
                        cancel_event=cancel_event,
                    )
                provider_media.append(
                    resolver.remote_content(
                        asset_id,
                        remote_asset_id=str(remote.id),
                    )
                )
                direct_asset_ids.add(asset_id)
            except (AttachmentError, ProviderError) as exc:
                raise EngineError(str(exc)) from None
    uploaded_file_context = _uploaded_file_context(
        request,
        vision_route=vision_route,
        direct_asset_ids=direct_asset_ids,
    )
    durable_user_content_blocks = list(content_blocks)
    if uploaded_file_context:
        # Keep a stable attachment reference in history, but never persist
        # transient inline Base64 or remote Provider Asset blocks generated
        # for this request. This also guarantees that attachment-only rounds
        # remain valid MessageItems when the next round rebuilds context.
        durable_user_content_blocks.append(
            TextContent(text=uploaded_file_context)
        )
    agent_runner = AgentRunner(
        base,
        user,
        config=config,
        provider_factory=dependencies.provider_factory,
    )
    window_path, archive_window, _ = prepare_window(base, user, source, session_id)

    return PreparedProviderRequest(
        config=config,
        context_policy=context_policy,
        source_policy=source_policy,
        runtime_provider=runtime_provider,
        provider=provider,
        uploaded_descriptors=uploaded_descriptors,
        history_attachments=history_attachments,
        provider_media=provider_media,
        direct_asset_ids=direct_asset_ids,
        vision_route=vision_route,
        uploaded_file_context=uploaded_file_context,
        durable_user_content_blocks=durable_user_content_blocks,
        agent_runner=agent_runner,
        window_path=window_path,
        archive_window=archive_window,
    )
