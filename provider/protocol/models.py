"""Pydantic models for the kemo unified provider interaction protocol."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from provider.protocol.enums import (
    ItemStatus,
    MeasurementMode,
    MessagePhase,
    MessageRole,
    ResponseStatus,
)


PROTOCOL_VERSION = "1.0"
SUPPORTED_PROTOCOL_MAJOR = 1
_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,127}$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class ProtocolModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        use_enum_values=True,
    )


class ExtensionModel(ProtocolModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class MediaSource(ProtocolModel):
    kind: Literal[
        "object_store",
        "url",
        "data_url",
        "provider_file_id",
        "inline_base64",
    ]
    uri: str | None = None
    provider: str | None = None
    file_id: str | None = None
    data: str | None = None
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> "MediaSource":
        if self.kind in {"object_store", "url", "data_url"} and not self.uri:
            raise ValueError(f"source.kind={self.kind} 要求 uri")
        if self.kind == "provider_file_id" and not (self.provider and self.file_id):
            raise ValueError("provider_file_id 要求 provider 和 file_id")
        if self.kind == "inline_base64" and not self.data:
            raise ValueError("inline_base64 要求 data")
        return self


class TextContent(ProtocolModel):
    type: Literal["text"] = "text"
    text: str
    language: str | None = None


class AssetContent(ProtocolModel):
    asset_id: str | None = None
    source: MediaSource | None = None
    mime_type: str | None = None
    checksum_sha256: str | None = None

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("asset_id 必须是稳定标识符，不能是本地路径")
        return value

    @model_validator(mode="after")
    def validate_reference(self) -> "AssetContent":
        if not self.asset_id and self.source is None:
            raise ValueError("媒体内容至少需要 asset_id 或 source")
        return self


class ImageContent(AssetContent):
    type: Literal["image"] = "image"
    detail: Literal["auto", "low", "high"] = "auto"
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class AudioContent(AssetContent):
    type: Literal["audio"] = "audio"
    duration_ms: int | None = Field(default=None, ge=0)
    transcript: str | None = None


class VideoDerived(ProtocolModel):
    transcript_asset_id: str | None = None
    keyframe_asset_ids: list[str] = Field(default_factory=list)
    timeline_asset_id: str | None = None


class VideoContent(AssetContent):
    type: Literal["video"] = "video"
    duration_ms: int | None = Field(default=None, ge=0)
    derived: VideoDerived | None = None


class FileContent(AssetContent):
    type: Literal["file"] = "file"
    filename: str | None = None


class JsonContent(ProtocolModel):
    type: Literal["json"] = "json"
    data: Any
    schema_name: str | None = None


class ReferenceContent(ProtocolModel):
    type: Literal["reference"] = "reference"
    target_id: str
    label: str | None = None


ContentBlock = Annotated[
    Union[
        TextContent,
        ImageContent,
        AudioContent,
        VideoContent,
        FileContent,
        JsonContent,
        ReferenceContent,
    ],
    Field(discriminator="type"),
]


class ItemBase(ExtensionModel):
    id: str
    status: ItemStatus = ItemStatus.COMPLETED
    created_at: datetime = Field(default_factory=_now)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError("id 必须是 1-128 位稳定标识符")
        return value


class MessageItem(ItemBase):
    type: Literal["message"] = "message"
    role: MessageRole
    phase: MessagePhase | None = None
    content: list[ContentBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_phase(self) -> "MessageItem":
        if self.role == MessageRole.USER and self.phase is not None:
            raise ValueError("user message 不允许 phase")
        return self

    @classmethod
    def text(
        cls,
        role: MessageRole | str,
        text: str,
        *,
        phase: MessagePhase | str | None = None,
        item_id: str | None = None,
    ) -> "MessageItem":
        return cls(
            id=item_id or _identifier("msg"),
            role=role,
            phase=phase,
            content=[TextContent(text=text)],
        )


class ProviderState(ProtocolModel):
    kind: Literal["encrypted", "opaque"]
    data: str
    provider: str
    model: str | None = None
    version: str | None = None
    expires_at: datetime | None = None


class ReasoningItem(ItemBase):
    type: Literal["reasoning"] = "reasoning"
    summary: str | None = None
    content: str | None = None
    provider_state: ProviderState | None = None
    token_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_body(self) -> "ReasoningItem":
        if not (self.summary or self.content or self.provider_state):
            raise ValueError("reasoning 至少需要 summary、content 或 provider_state")
        return self


class ToolCallItem(ItemBase):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_raw: str | None = None
    parse_error: dict[str, Any] | None = None

    @field_validator("call_id", "name")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("工具 call_id/name 不能为空")
        return value.strip()


class ToolResultItem(ItemBase):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    is_error: bool = False
    content: list[ContentBlock] = Field(min_length=1)


Item = Annotated[
    Union[MessageItem, ReasoningItem, ToolCallItem, ToolResultItem],
    Field(discriminator="type"),
]


class ReasoningConfig(ProtocolModel):
    enabled: bool = False
    effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] = "none"
    return_mode: Literal["none", "summary", "content", "auto"] = Field(
        default="none", alias="return"
    )
    context: Literal["none", "current_turn", "all_turns", "auto"] = "auto"


class GenerationConfig(ProtocolModel):
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    top_p: float | None = None
    parallel_tool_calls: bool = True


class AudioOutputConfig(ProtocolModel):
    format: str = "mp3"
    voice: str = "default"


class ImageOutputConfig(ProtocolModel):
    format: str = "png"
    size: str = "1024x1024"


class VideoOutputConfig(ProtocolModel):
    format: str = "mp4"
    duration_seconds: float | None = Field(default=None, gt=0)


class OutputConfig(ProtocolModel):
    modalities: list[Literal["text", "audio", "image", "video"]] = Field(
        default_factory=lambda: ["text"], min_length=1
    )
    audio: AudioOutputConfig | None = None
    image: ImageOutputConfig | None = None
    video: VideoOutputConfig | None = None


class ToolDefinition(ExtensionModel):
    type: Literal["function"] = "function"
    name: str
    description: str
    parameters: dict[str, Any]
    strict: bool = True
    permission: str | None = None


class ReasoningCapabilities(ProtocolModel):
    supported: bool = False
    efforts: list[str] = Field(default_factory=list)
    summary: bool = False
    persisted_state: bool = False


class ToolCapabilities(ProtocolModel):
    function_calling: bool = True
    parallel_calls: bool = False
    multimodal_results: bool = False


class ModelCapabilities(ExtensionModel):
    model: str
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    streaming: bool = True
    reasoning: ReasoningCapabilities = Field(default_factory=ReasoningCapabilities)
    tools: ToolCapabilities = Field(default_factory=ToolCapabilities)
    structured_output: bool = False


class Measurement(ProtocolModel):
    mode: MeasurementMode = MeasurementMode.UNKNOWN
    exact: bool = False
    exact_fields: list[str] = Field(default_factory=list)
    estimated_fields: list[str] = Field(default_factory=list)


class MediaUsage(ProtocolModel):
    input_images: int | None = Field(default=None, ge=0)
    input_audio_seconds: float | None = Field(default=None, ge=0)
    input_video_seconds: float | None = Field(default=None, ge=0)
    output_audio_seconds: float | None = Field(default=None, ge=0)
    output_images: int | None = Field(default=None, ge=0)
    output_video_seconds: float | None = Field(default=None, ge=0)


class StageUsage(ExtensionModel):
    stage: str
    provider: str
    model: str
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    measurement: Measurement = Field(default_factory=Measurement)
    media: MediaUsage = Field(default_factory=MediaUsage)


class Usage(ProtocolModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    visible_output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    measurement: Measurement = Field(default_factory=Measurement)
    media: MediaUsage = Field(default_factory=MediaUsage)
    stages: list[StageUsage] = Field(default_factory=list)
    provider_raw: dict[str, Any] = Field(default_factory=dict)


class UnifiedError(ProtocolModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    retry_after_ms: int | None = Field(default=None, ge=0)
    provider_status: int | None = None
    provider_request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class KemoRequest(ExtensionModel):
    protocol_version: str = PROTOCOL_VERSION
    request_id: str = Field(default_factory=lambda: _identifier("req"))
    parent_request_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    model: str
    stream: bool = True
    system_prompt: str
    reasoning: ReasoningConfig | None = None
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    tools: list[ToolDefinition] = Field(default_factory=list)
    input: list[Item]
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        try:
            major = int(value.split(".", 1)[0])
        except (TypeError, ValueError) as exc:
            raise ValueError("protocol_version 必须是 MAJOR.MINOR") from exc
        if major != SUPPORTED_PROTOCOL_MAJOR:
            raise ValueError(f"不支持的协议主版本：{value}")
        return value

    @field_validator("request_id", "parent_request_id")
    @classmethod
    def validate_request_ids(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            raise ValueError("request_id 必须是 1-128 位稳定标识符")
        return value

    @model_validator(mode="after")
    def validate_items(self) -> "KemoRequest":
        item_ids: set[str] = set()
        call_ids: set[str] = set()
        for index, item in enumerate(self.input):
            if item.id in item_ids:
                raise ValueError(f"input[{index}].id 重复：{item.id}")
            item_ids.add(item.id)
            if isinstance(item, ToolCallItem):
                if item.call_id in call_ids:
                    raise ValueError(f"tool_call.call_id 重复：{item.call_id}")
                call_ids.add(item.call_id)
            elif isinstance(item, ToolResultItem) and item.call_id not in call_ids:
                raise ValueError(f"tool_result 无匹配 tool_call：{item.call_id}")
        return self


class IncompleteDetails(ProtocolModel):
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class KemoResponse(ExtensionModel):
    protocol_version: str = PROTOCOL_VERSION
    id: str = Field(default_factory=lambda: _identifier("resp"))
    request_id: str
    object: Literal["kemo.response"] = "kemo.response"
    status: ResponseStatus
    model: str
    output: list[Item] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    error: UnifiedError | None = None
    incomplete_details: IncompleteDetails | None = None
    provider_response_id: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> "KemoResponse":
        if self.status == ResponseStatus.FAILED and self.error is None:
            raise ValueError("failed response 必须包含 error")
        if self.status == ResponseStatus.REQUIRES_ACTION and not any(
            isinstance(item, ToolCallItem) for item in self.output
        ):
            raise ValueError("requires_action response 必须包含 tool_call")
        if self.status == ResponseStatus.INCOMPLETE and self.incomplete_details is None:
            raise ValueError("incomplete response 必须包含 incomplete_details")
        ids = [item.id for item in self.output]
        if len(ids) != len(set(ids)):
            raise ValueError("response.output item id 不得重复")
        return self


def text_from_content(content: list[ContentBlock]) -> str:
    return "".join(item.text for item in content if isinstance(item, TextContent))


def final_messages(response: KemoResponse) -> list[MessageItem]:
    return [
        item
        for item in response.output
        if isinstance(item, MessageItem)
        and item.role == MessageRole.ASSISTANT
        and item.phase == MessagePhase.FINAL_ANSWER
    ]
