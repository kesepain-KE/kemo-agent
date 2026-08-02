"""Pydantic models for the kemo unified provider interaction protocol."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime
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
USER_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "max"})
DEFAULT_REASONING_EFFORT = "medium"
_MAX_REASONING_EFFORT_LENGTH = 64


def _identifier(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize_reasoning_effort(value: Any) -> str:
    """Return the supported user effort, falling back to the always-on default."""

    effort = str(value or "").strip().lower()
    return effort if effort in USER_REASONING_EFFORTS else DEFAULT_REASONING_EFFORT


def _dynamic_reasoning_effort(value: Any, *, allow_none: bool = False) -> str:
    effort = str(value or "").strip().casefold()
    if (
        not effort
        or len(effort) > _MAX_REASONING_EFFORT_LENGTH
        or any(ord(character) < 32 for character in effort)
        or (effort == "none" and not allow_none)
    ):
        return ""
    return effort


def normalize_kemo_reasoning_effort(value: Any) -> str:
    """Preserve one gateway-declared Kemo effort without vendor-side mapping."""

    effort = _dynamic_reasoning_effort(value)
    return effort or DEFAULT_REASONING_EFFORT


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
    created_at: datetime | None = None

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
    effort: str = "none"
    return_mode: Literal["none", "summary", "content", "auto"] = Field(
        default="none", alias="return"
    )
    context: Literal["none", "current_turn", "all_turns", "auto"] = "auto"

    @field_validator("effort")
    @classmethod
    def validate_effort(cls, value: str) -> str:
        effort = _dynamic_reasoning_effort(value, allow_none=True)
        if not effort:
            raise ValueError("reasoning.effort 必须是有效的网关逻辑档位")
        return effort


class GenerationConfig(ProtocolModel):
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
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


class FileOutputConfig(ProtocolModel):
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=127)


class OutputConfig(ProtocolModel):
    modalities: list[Literal["text", "audio", "image", "video", "file"]] = Field(
        default_factory=lambda: ["text"], min_length=1
    )
    audio: AudioOutputConfig | None = None
    image: ImageOutputConfig | None = None
    video: VideoOutputConfig | None = None
    file: FileOutputConfig | None = None

    @model_validator(mode="after")
    def validate_configs(self) -> "OutputConfig":
        if len(self.modalities) != len(set(self.modalities)):
            raise ValueError("output.modalities 不得重复")
        for modality in ("audio", "image", "video", "file"):
            config = getattr(self, modality)
            requested = modality in self.modalities
            if requested and config is None:
                raise ValueError(f"请求 {modality} 输出时必须提供 output.{modality}")
            if not requested and config is not None:
                raise ValueError(f"output.{modality} 只能在 modalities 包含 {modality} 时提供")
        return self


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

    @field_validator("efforts")
    @classmethod
    def validate_efforts(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            effort = _dynamic_reasoning_effort(raw, allow_none=True)
            if not effort:
                raise ValueError(f"reasoning.efforts 包含无效逻辑档位：{raw!r}")
            if effort == "none":
                continue
            if effort in normalized:
                raise ValueError(f"reasoning.efforts 不得重复：{effort}")
            normalized.append(effort)
        return normalized

    @model_validator(mode="after")
    def validate_supported_efforts(self) -> "ReasoningCapabilities":
        if not self.supported and self.efforts:
            raise ValueError("reasoning.supported=false 时 efforts 必须为空")
        return self


class ToolCapabilities(ProtocolModel):
    function_calling: bool = False
    parallel_calls: bool = False
    multimodal_results: bool = False


class EmbeddingCapabilities(ProtocolModel):
    """Kemo 向量模型能力声明。"""

    input_types: list[Literal["query", "document"]]
    default_dimensions: int = Field(gt=0)
    supported_dimensions: list[int] = Field(default_factory=list)
    max_batch_size: int = Field(gt=0)
    max_input_tokens_per_item: int | None = Field(default=None, gt=0)
    normalization: Literal["always", "optional", "never", "unknown"] = "unknown"


class RerankCapabilities(ProtocolModel):
    """Kemo 重排序模型能力声明。"""

    max_documents: int = Field(gt=0)
    max_query_tokens: int | None = Field(default=None, gt=0)
    max_document_tokens: int | None = Field(default=None, gt=0)
    supports_return_documents: bool = True
    score_semantics: Literal["higher_is_more_relevant"] = "higher_is_more_relevant"


class ModelCapabilities(ExtensionModel):
    model: str
    task: Literal["llm", "embedding", "rerank"] = "llm"
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    streaming: bool = True
    reasoning: ReasoningCapabilities = Field(default_factory=ReasoningCapabilities)
    tools: ToolCapabilities = Field(default_factory=ToolCapabilities)
    structured_output: bool = False
    embedding: EmbeddingCapabilities | None = None
    rerank: RerankCapabilities | None = None

    @model_validator(mode="after")
    def validate_task_capabilities(self) -> "ModelCapabilities":
        if self.task == "embedding" and self.embedding is None:
            raise ValueError("embedding 模型必须声明 embedding capabilities")
        if self.task == "rerank" and self.rerank is None:
            raise ValueError("rerank 模型必须声明 rerank capabilities")
        if self.task != "embedding" and self.embedding is not None:
            raise ValueError("非 embedding 模型不能声明 embedding capabilities")
        if self.task != "rerank" and self.rerank is not None:
            raise ValueError("非 rerank 模型不能声明 rerank capabilities")
        if self.task == "llm":
            self._validate_multimodal_operations()
        return self

    def _validate_multimodal_operations(self) -> None:
        operations = self.extensions.get("operations")
        if operations is None:
            return
        if not isinstance(operations, Mapping):
            raise ValueError("extensions.operations 必须是对象")
        requirements = {
            "conversation": (set(), {"text"}),
            "vision": ({"text", "image"}, {"text"}),
            "image_generation": ({"text"}, {"image"}),
            "image_edit": ({"text", "image"}, {"image"}),
            "audio_transcription": ({"audio"}, {"text"}),
            "speech_generation": ({"text"}, {"audio"}),
            "speech_to_speech": ({"audio"}, {"audio"}),
            "video_understanding": ({"video"}, {"text"}),
            "video_generation": ({"text"}, {"video"}),
        }
        for name, declaration in operations.items():
            if isinstance(declaration, bool):
                supported = declaration
            elif isinstance(declaration, Mapping):
                supported = declaration.get("supported") is True
                if "supported" not in declaration or not isinstance(
                    declaration.get("supported"), bool
                ):
                    raise ValueError(
                        f"extensions.operations.{name}.supported 必须是布尔值"
                    )
            else:
                raise ValueError(
                    f"extensions.operations.{name} 必须是布尔值或对象"
                )
            if not supported or name not in requirements:
                continue
            required_inputs, required_outputs = requirements[name]
            missing_inputs = required_inputs - set(self.input_modalities)
            missing_outputs = required_outputs - set(self.output_modalities)
            if missing_inputs or missing_outputs:
                raise ValueError(
                    f"操作 {name} 与 input_modalities/output_modalities 声明不一致"
                )


class ModelCatalogItem(ProtocolModel):
    id: str
    object: Literal["kemo.model"] = "kemo.model"
    provider_id: str
    provider_model: str
    task: Literal["llm", "embedding", "rerank", "unknown"]
    capabilities_available: bool
    capabilities_url: str


class ModelCatalogResponse(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    object: Literal["kemo.model_list"] = "kemo.model_list"
    count: int = Field(ge=0)
    data: list[ModelCatalogItem]


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


class EmbeddingInput(ProtocolModel):
    """One text item sent to the gateway embedding endpoint."""

    id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingRequest(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1)
    input_type: Literal["query", "document"]
    inputs: list[EmbeddingInput] = Field(min_length=1, max_length=2048)
    dimensions: int | None = Field(default=None, gt=0)
    normalize: bool | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "EmbeddingRequest":
        ids = [item.id for item in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("embedding input id 必须唯一")
        return self


class EmbeddingData(ProtocolModel):
    id: str
    index: int = Field(ge=0)
    vector: list[float]


class EmbeddingResponse(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    object: Literal["kemo.embedding_list"] = "kemo.embedding_list"
    request_id: str
    model: str
    model_version: str | None = None
    vector_space_id: str
    dimensions: int = Field(gt=0)
    data: list[EmbeddingData]
    usage: Usage = Field(default_factory=Usage)
    provider_response_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


class RerankDocument(ProtocolModel):
    """A document supplied to the gateway rerank endpoint."""

    id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankRequest(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    request_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2_000_000)
    documents: list[RerankDocument] = Field(min_length=1, max_length=4096)
    top_n: int | None = Field(default=None, gt=0)
    return_documents: bool = False
    provider_options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_documents(self) -> "RerankRequest":
        ids = [item.id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("rerank document id 必须唯一")
        if self.top_n is not None and self.top_n > len(self.documents):
            raise ValueError("top_n 不能超过 documents 数量")
        return self


class RerankResultItem(ProtocolModel):
    rank: int = Field(ge=1)
    document_id: str
    index: int = Field(ge=0)
    relevance_score: float
    document: RerankDocument | None = None


class RerankResponse(ProtocolModel):
    protocol_version: Literal["1.0"] = "1.0"
    object: Literal["kemo.rerank"] = "kemo.rerank"
    request_id: str
    model: str
    model_version: str | None = None
    score_semantics: Literal["higher_is_more_relevant"] = "higher_is_more_relevant"
    results: list[RerankResultItem]
    usage: Usage = Field(default_factory=Usage)
    provider_response_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)


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
        calls: dict[str, str] = {}
        completed_results: set[str] = set()
        for index, item in enumerate(self.input):
            if item.id in item_ids:
                raise ValueError(f"input[{index}].id 重复：{item.id}")
            item_ids.add(item.id)
            if isinstance(item, ToolCallItem):
                if item.call_id in calls:
                    raise ValueError(f"tool_call.call_id 重复：{item.call_id}")
                calls[item.call_id] = item.name
            elif isinstance(item, ToolResultItem):
                expected_name = calls.get(item.call_id)
                if expected_name is None:
                    raise ValueError(f"tool_result 无匹配 tool_call：{item.call_id}")
                if expected_name != item.name:
                    raise ValueError(f"tool_result.name 与 tool_call 不一致：{item.call_id}")
                if item.call_id in completed_results:
                    raise ValueError(f"tool_result.call_id 重复：{item.call_id}")
                completed_results.add(item.call_id)
        return self


class IncompleteDetails(ProtocolModel):
    """Legacy typed view kept for callers that imported the old helper.

    ``KemoResponse.incomplete_details`` intentionally uses a plain mapping so
    gateway-specific fields are preserved; this class is not used for wire
    validation anymore.
    """

    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


def _validate_output_media_item(
    item: MessageItem, *, require_media: bool = False
) -> None:
    media_blocks = [
        block
        for block in item.content
        if isinstance(block, (ImageContent, AudioContent, VideoContent, FileContent))
    ]
    if require_media and not media_blocks:
        raise ValueError("媒体完成事件必须至少包含一个媒体 Content Block")
    for block in media_blocks:
        if not block.asset_id:
            raise ValueError("响应媒体必须包含可下载的 asset_id")
        if not block.mime_type:
            raise ValueError("响应媒体必须包含真实 mime_type")
        if not block.checksum_sha256:
            raise ValueError("响应媒体必须包含 checksum_sha256")


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
    # The gateway deliberately keeps this payload extensible.  Providers may
    # attach reason-specific fields (for example ``retry_after_ms`` or a
    # provider status) that are not part of the core response contract, so the
    # consumer must not narrow it to the old ``IncompleteDetails`` shape.
    incomplete_details: dict[str, Any] | None = None
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
        if self.status == ResponseStatus.COMPLETED and not self.output:
            raise ValueError("completed response 必须包含完成结果")
        for item in self.output:
            if isinstance(item, MessageItem):
                if item.role != MessageRole.ASSISTANT:
                    raise ValueError("response.output message 必须使用 assistant role")
                _validate_output_media_item(item)
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
