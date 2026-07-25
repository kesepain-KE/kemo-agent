"""Capability-scoped multimodal operations for current-Run assets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from provider.factory import create_provider, provider_request_slot
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import (
    AudioOutputConfig,
    GenerationConfig,
    ImageOutputConfig,
    KemoRequest,
    MessageItem,
    OutputConfig,
    TextContent,
    VideoOutputConfig,
)
from run.attachments import RunAssetResolver, describe_local_asset
from run.config import load_config, provider_runtime_config
from run.media_outputs import persist_response_media


_ACTION_CAPABILITY = {
    "analyze_image": "vision",
    "generate_image": "image_generation",
    "edit_image": "image_edit",
    "transcribe_audio": "audio_transcription",
    "generate_speech": "speech_generation",
    "convert_speech": "speech_to_speech",
    "analyze_video": "video_understanding",
    "generate_video": "video_generation",
}
_INPUT_KIND = {
    "analyze_image": "image",
    "edit_image": "image",
    "transcribe_audio": "audio",
    "convert_speech": "audio",
    "analyze_video": "video",
}
_OUTPUT_MODALITY = {
    "generate_image": "image",
    "edit_image": "image",
    "generate_speech": "audio",
    "convert_speech": "audio",
    "generate_video": "video",
}


def _assistant_text(response: Any) -> str:
    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if not isinstance(item, MessageItem) or item.role != MessageRole.ASSISTANT:
            continue
        parts.extend(
            block.text
            for block in item.content
            if isinstance(block, TextContent) and block.text
        )
    return "\n".join(parts).strip()


def _operation_supported(capabilities: Any, capability: str) -> bool:
    operations = (getattr(capabilities, "extensions", {}) or {}).get("operations")
    if not isinstance(operations, dict):
        return False
    value = operations.get(capability)
    if isinstance(value, dict):
        return value.get("supported") is True
    return value is True


def _output_config(
    action: str,
    *,
    output_format: str,
    voice: str,
    size: str,
    duration_seconds: float | None,
) -> OutputConfig:
    modality = _OUTPUT_MODALITY.get(action, "text")
    if modality == "image":
        return OutputConfig(
            modalities=["image"],
            image=ImageOutputConfig(
                format=output_format or "png",
                size=size or "1024x1024",
            ),
        )
    if modality == "audio":
        return OutputConfig(
            modalities=["audio"],
            audio=AudioOutputConfig(
                format=output_format or "mp3",
                voice=voice or "default",
            ),
        )
    if modality == "video":
        return OutputConfig(
            modalities=["video"],
            video=VideoOutputConfig(
                format=output_format or "mp4",
                duration_seconds=duration_seconds,
            ),
        )
    return OutputConfig(modalities=["text"])


def run(
    action: str,
    asset_ids: list[str] | None = None,
    instruction: str = "",
    detail: str = "auto",
    output_format: str = "",
    voice: str = "",
    size: str = "",
    duration_seconds: float | None = None,
    asset_roles: list[str] | None = None,
    paths: list[str] | None = None,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
    if action not in _ACTION_CAPABILITY:
        raise ValueError(f"不支持的 action：{action}")
    ids = [str(value) for value in (asset_ids or []) if str(value)]
    local_paths = [str(value).strip() for value in (paths or []) if str(value).strip()]
    normalized_instruction = str(instruction or "").strip()
    if not normalized_instruction:
        raise ValueError("instruction 不能为空")
    expected_kind = _INPUT_KIND.get(action)
    input_count = len(ids) + len(local_paths)
    if input_count > 8:
        raise ValueError("asset_ids 与 paths 合计最多 8 项")
    if expected_kind and (input_count < 1 or input_count > 8):
        raise ValueError(
            f"{action} 的 asset_ids 与 paths 合计必须包含 1 至 8 个 {expected_kind} 资产"
        )
    if action in {"generate_image", "generate_speech"} and input_count:
        raise ValueError(f"{action} 不接受输入资产")
    if asset_roles is not None and len(asset_roles) != input_count:
        raise ValueError("asset_roles 数量必须与 asset_ids 和 paths 的总数一致")

    root = Path(str(context["root"])).resolve()
    user = str(context["user"])
    config = load_config(user, root)
    runtime = provider_runtime_config(config)
    provider_type = str(runtime.get("type") or "")
    capability = _ACTION_CAPABILITY[action]
    if provider_type != "kemo" and action != "analyze_image":
        raise ValueError(
            f"{action} 只在 provider.type=kemo 的完整多模态协议中可用；"
            "Chat 模式只保证图片识别"
        )
    models = config.get("multimodal_models") or {}
    if not isinstance(models, dict):
        raise ValueError("multimodal_models 必须是对象")
    model = str(models.get(capability) or "").strip()
    if not model:
        raise ValueError(f"尚未配置 multimodal_models.{capability} 专用模型")
    runtime["model"] = model
    provider = create_provider(runtime)
    cancel_event = context.get("cancel_event")

    if provider_type == "kemo":
        with provider_request_slot(config, cancel_event=cancel_event):
            declared = provider.capabilities(model)
        if not _operation_supported(declared, capability):
            raise ValueError(
                f"Kemo 网关未在 extensions.operations 中声明 {capability}=supported"
            )
        input_modalities = set(getattr(declared, "input_modalities", []) or [])
        output_modalities = set(getattr(declared, "output_modalities", []) or [])
        if expected_kind and expected_kind not in input_modalities:
            raise ValueError(f"模型 {model} 未声明 {expected_kind} 输入能力")
        output_modality = _OUTPUT_MODALITY.get(action)
        if output_modality and output_modality not in output_modalities:
            raise ValueError(f"模型 {model} 未声明 {output_modality} 输出能力")

    descriptors = context.get("uploaded_files") or []
    if not isinstance(descriptors, list):
        raise ValueError("当前 Run 的附件上下文无效")
    local_descriptors = [
        describe_local_asset(root, user, {"path": value}) for value in local_paths
    ]
    descriptors = [*descriptors, *local_descriptors]
    ids.extend(str(item["asset_id"]) for item in local_descriptors)
    resolver = RunAssetResolver(root, user, descriptors)
    media = []
    multimodal_assets: list[dict[str, str]] = []
    if ids:
        if provider_type == "chat":
            media = [
                resolver.image_content(asset_id, provider="chat", detail=detail)
                for asset_id in ids
            ]
        else:
            for index, asset_id in enumerate(ids):
                path, verified = resolver.local_asset(
                    asset_id,
                    expected_kind=(
                        None if action == "generate_video" else expected_kind
                    ),
                )
                with provider_request_slot(config, cancel_event=cancel_event):
                    remote = provider.upload_asset(
                        path,
                        metadata={
                            "user": user,
                            "session_id": str(context.get("session_id") or ""),
                            "purpose": "input",
                            "capability": capability,
                        },
                        idempotency_key=f"{asset_id}:{capability}",
                        checksum_sha256=str(verified["checksum_sha256"]),
                        mime_type=str(verified["mime_type"]),
                        cancel_event=cancel_event,
                    )
                    remote = provider.wait_asset_ready(
                        remote,
                        cancel_event=cancel_event,
                    )
                media.append(
                    resolver.remote_content(
                        asset_id,
                        remote_asset_id=str(remote.id),
                    )
                )
                role = (
                    str(asset_roles[index])
                    if asset_roles is not None
                    else "source" if index == 0 else "reference"
                )
                multimodal_assets.append({"asset_id": str(remote.id), "role": role})

    request = KemoRequest(
        model=model,
        stream=False,
        system_prompt=(
            "你是独立的多模态处理模块，只执行本次指定操作，不调用工具。"
            "必须如实处理媒体；无法确认时明确说明，不得猜测。"
        ),
        generation=GenerationConfig(max_output_tokens=4096),
        output=_output_config(
            action,
            output_format=output_format,
            voice=voice,
            size=size,
            duration_seconds=duration_seconds,
        ),
        input=[
            MessageItem(
                id="msg_multimodal_input",
                role=MessageRole.USER,
                content=[TextContent(text=normalized_instruction), *media],
            )
        ],
        metadata={
            "user": user,
            "source": str(context.get("source") or ""),
            "session_id": str(context.get("session_id") or ""),
            "capability": capability,
            **(
                {"multimodal": {"assets": multimodal_assets}}
                if multimodal_assets
                else {}
            ),
        },
    )
    with provider_request_slot(config, cancel_event=cancel_event):
        response = provider.create(request)
    if response.status != ResponseStatus.COMPLETED:
        error = getattr(response, "error", None)
        message = getattr(error, "message", "") if error is not None else ""
        raise RuntimeError(message or f"多模态模型返回非成功状态：{response.status}")
    with provider_request_slot(config, cancel_event=cancel_event):
        artifacts = persist_response_media(
            provider,
            response,
            root=root,
            user=user,
            cancel_event=cancel_event,
        )
    text = _assistant_text(response)
    if _OUTPUT_MODALITY.get(action) is None and not text:
        raise RuntimeError("多模态模型没有返回可用的文本结果")
    if _OUTPUT_MODALITY.get(action) is not None and not artifacts:
        raise RuntimeError("多模态模型没有返回可下载的媒体 Asset")
    result = {
        "action": action,
        "text": text,
        "artifacts": artifacts,
        "asset_ids": ids,
        "paths": local_paths,
        "model": model,
        "usage": response.usage.model_dump(mode="json", exclude_none=True),
    }
    if action in {"analyze_image", "analyze_video"}:
        result["analysis"] = text
    if action == "transcribe_audio":
        result["transcript"] = text
    return result
