"""Capability-scoped multimodal operations for current-Run assets."""

from __future__ import annotations

from pathlib import Path
import random
import time
from typing import Any

from provider.factory import create_provider, provider_request_slot
from provider.protocol.enums import MessageRole, ResponseStatus
from provider.protocol.models import (
    AudioOutputConfig,
    AudioContent,
    FileContent,
    GenerationConfig,
    ImageContent,
    ImageOutputConfig,
    KemoRequest,
    MessageItem,
    OutputConfig,
    TextContent,
    VideoContent,
    VideoOutputConfig,
)
from provider.protocol.assets import AssetDescriptor
from provider.schema import ProviderError
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
_RETRY_SAFE_ACTIONS = frozenset(
    {"analyze_image", "transcribe_audio", "analyze_video"}
)
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_MAX_TRANSIENT_ATTEMPTS = 2
_RETRY_BASE_SECONDS = 0.5
_TOOL_TIMEOUT_RESERVE_SECONDS = 5.0


def _provider_response_error(response: Any) -> ProviderError:
    error = getattr(response, "error", None)
    message = str(getattr(error, "message", "") or "").strip()
    category = str(
        getattr(error, "type", "")
        or getattr(error, "code", "")
        or "provider_response_error"
    )
    status_code = getattr(error, "provider_status", None)
    retryable = bool(getattr(error, "retryable", False))
    exc = ProviderError(
        message or f"多模态模型返回非成功状态：{response.status}",
        category=category,
        status_code=status_code if isinstance(status_code, int) else None,
        retryable=retryable,
    )
    retry_after_ms = getattr(error, "retry_after_ms", None)
    if isinstance(retry_after_ms, int) and retry_after_ms >= 0:
        exc.retry_after_ms = retry_after_ms
    return exc


def _is_transient_error(exc: BaseException) -> bool:
    if not isinstance(exc, ProviderError):
        return False
    if exc.retryable:
        return True
    return exc.status_code in _TRANSIENT_STATUS_CODES


def _wait_before_retry(exc: BaseException, attempt: int, cancel_event: Any) -> None:
    raw_retry_after = getattr(exc, "retry_after_ms", None)
    if isinstance(raw_retry_after, int) and raw_retry_after >= 0:
        delay = min(10.0, raw_retry_after / 1000.0)
    else:
        base = _RETRY_BASE_SECONDS * (2 ** max(0, attempt - 1))
        delay = min(2.0, base) + random.uniform(0.0, min(0.25, base * 0.25))
    if cancel_event is not None and callable(getattr(cancel_event, "wait", None)):
        if cancel_event.wait(delay):
            raise ProviderError(
                "多模态调用已取消",
                category="cancelled",
                retryable=False,
            )
        return
    time.sleep(delay)


def _create_with_retry(
    provider: Any,
    request: KemoRequest,
    *,
    config: dict[str, Any],
    action: str,
    cancel_event: Any,
) -> tuple[Any, int]:
    max_attempts = (
        _MAX_TRANSIENT_ATTEMPTS if action in _RETRY_SAFE_ACTIONS else 1
    )
    for attempt in range(1, max_attempts + 1):
        try:
            with provider_request_slot(config, cancel_event=cancel_event):
                response = provider.create(request)
            if response.status != ResponseStatus.COMPLETED:
                raise _provider_response_error(response)
            return response, attempt
        except ProviderError as exc:
            exc.attempt_count = attempt
            if attempt >= max_attempts or not _is_transient_error(exc):
                raise
            _wait_before_retry(exc, attempt, cancel_event)
    raise RuntimeError("多模态调用未产生结果")


def _multimodal_provider_timeout(
    config: dict[str, Any],
    runtime: dict[str, Any],
    context: dict[str, Any],
) -> float:
    provider_config = config.get("provider") or {}
    configured = (
        isinstance(provider_config, dict) and "timeout" in provider_config
    )
    raw_tool_timeout = context.get("tool_timeout", 240.0)
    try:
        tool_timeout = float(raw_tool_timeout)
    except (TypeError, ValueError):
        tool_timeout = 240.0
    budget = max(1.0, tool_timeout - _TOOL_TIMEOUT_RESERVE_SECONDS)
    if configured:
        try:
            provider_timeout = float(runtime.get("timeout", 120.0))
        except (TypeError, ValueError):
            provider_timeout = 120.0
        return min(max(1.0, provider_timeout), budget)
    return min(600.0, budget)


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


def _asset_kind_from_mime(mime_type: str) -> str:
    normalized = str(mime_type or "").casefold()
    if normalized.startswith("image/"):
        return "image"
    if normalized.startswith("audio/"):
        return "audio"
    if normalized.startswith("video/"):
        return "video"
    return "file"


def _remote_content(
    descriptor: AssetDescriptor,
    *,
    expected_kind: str | None,
) -> ImageContent | AudioContent | VideoContent | FileContent:
    """Build a Kemo media block for an already-owned gateway Asset."""

    kind = _asset_kind_from_mime(descriptor.mime_type)
    if expected_kind is not None and kind != expected_kind:
        raise ValueError(
            f"附件类型不匹配：期望 {expected_kind}，实际为 {kind}"
        )
    common = {
        "asset_id": descriptor.id,
        "mime_type": descriptor.mime_type,
        "checksum_sha256": descriptor.checksum_sha256,
    }
    if kind == "image":
        return ImageContent(**common)
    if kind == "audio":
        return AudioContent(**common)
    if kind == "video":
        return VideoContent(**common)
    return FileContent(filename=descriptor.filename, **common)


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
    runtime["timeout"] = _multimodal_provider_timeout(config, runtime, context)
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
    local_asset_ids = {
        str(item.get("asset_id") or "")
        for item in descriptors
        if isinstance(item, dict) and str(item.get("asset_id") or "")
    }
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
                # A Kemo asset may have been created by the gateway (for example
                # in an earlier run or by another client).  Such an id is not a
                # local Run attachment and must be resolved through the gateway,
                # rather than being forced through RunAssetResolver.
                if asset_id not in local_asset_ids:
                    get_asset = getattr(provider, "get_asset", None)
                    wait_asset_ready = getattr(provider, "wait_asset_ready", None)
                    if callable(get_asset) and callable(wait_asset_ready):
                        try:
                            with provider_request_slot(
                                config, cancel_event=cancel_event
                            ):
                                remote = wait_asset_ready(
                                    get_asset(asset_id),
                                    cancel_event=cancel_event,
                                )
                        except ProviderError as exc:
                            # A 404 means this is not a gateway asset; fall back
                            # to the normal current-Run attachment validation.
                            # Auth, expiry, network and other errors must surface.
                            if exc.status_code != 404:
                                raise
                        else:
                            media.append(
                                _remote_content(
                                    remote,
                                    expected_kind=(
                                        None
                                        if action == "generate_video"
                                        else expected_kind
                                    ),
                                )
                            )
                            role = (
                                str(asset_roles[index])
                                if asset_roles is not None
                                else "source" if index == 0 else "reference"
                            )
                            multimodal_assets.append(
                                {"asset_id": str(remote.id), "role": role}
                            )
                            ids[index] = str(remote.id)
                            continue
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
        generation=GenerationConfig(max_output_tokens=10_000),
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
    response, attempts = _create_with_retry(
        provider,
        request,
        config=config,
        action=action,
        cancel_event=cancel_event,
    )
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
        "attempts": attempts,
        "usage": response.usage.model_dump(mode="json", exclude_none=True),
    }
    if action in {"analyze_image", "analyze_video"}:
        result["analysis"] = text
    if action == "transcribe_audio":
        result["transcript"] = text
    return result
