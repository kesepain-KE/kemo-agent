"""Pydantic models for the kemo unified provider interaction protocol."""

from __future__ import annotations

import base64
import binascii
import math
import re
import uuid
from collections.abc import Iterator, Mapping
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

try:
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
except ImportError:  # pragma: no cover - the runtime dependency is bundled by kemo
    Draft202012Validator = None  # type: ignore[assignment]
    SchemaError = ValueError  # type: ignore[assignment,misc]

from provider.protocol.enums import (
    ErrorCode,
    IncompleteReason,
    ItemStatus,
    MeasurementMode,
    MessagePhase,
    MessageRole,
    ResponseStatus,
    ToolChoiceMode,
)


PROTOCOL_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSIONS = (PROTOCOL_VERSION,)
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
USER_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "max"})
DEFAULT_REASONING_EFFORT = "medium"
_MAX_REASONING_EFFORT_LENGTH = 64
MAX_INLINE_IMAGE_BYTES = 1024 * 1024
MAX_JSON_SCHEMA_DEPTH = 32
MAX_JSON_SCHEMA_NODES = 1000


def _validate_json_schema_definition(
    schema: dict[str, Any], *, label: str, enforce_limits: bool = True
) -> dict[str, Any]:
    """Validate the deliberately bounded JSON Schema subset used on the wire.

    Kemo never dereferences arbitrary network locations.  Only local ``#/$defs``
    references are accepted and the schema is checked with the Draft 2020-12
    meta-schema before it reaches an adapter.  Keeping this at model construction
    time prevents malformed schemas from surviving until a provider call.
    """

    if not isinstance(schema, dict) or not schema:
        raise ValueError(f"{label} 必须是非空 JSON Schema 对象")
    if schema.get("type") != "object":
        raise ValueError(f"{label} 根必须显式 type=object")
    if not enforce_limits:
        # Tool argument validation owns the bounded walk and its public error
        # shape.  Still reject remote references without recursing through a
        # potentially adversarially deep user schema here.
        pending: list[Any] = [schema]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                ref = value.get("$ref")
                if ref is not None and (
                    not isinstance(ref, str) or not ref.startswith("#/$defs/")
                ):
                    raise ValueError(f"{label} 只允许本地 #/$defs 引用")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return schema

    nodes = 0
    local_refs: list[str] = []

    def walk(value: Any, depth: int, path: str) -> None:
        nonlocal nodes
        nodes += 1
        if enforce_limits and nodes > MAX_JSON_SCHEMA_NODES:
            raise ValueError(f"{label} 节点数超过 {MAX_JSON_SCHEMA_NODES} 限制")
        if enforce_limits and depth > MAX_JSON_SCHEMA_DEPTH:
            raise ValueError(f"{label} 深度超过 {MAX_JSON_SCHEMA_DEPTH} 限制")
        if isinstance(value, dict):
            ref = value.get("$ref")
            if ref is not None and (
                not isinstance(ref, str) or not ref.startswith("#/$defs/")
            ):
                raise ValueError(f"{label} 只允许本地 #/$defs 引用")
            if isinstance(ref, str):
                local_refs.append(ref)
            for key, child in value.items():
                walk(child, depth + 1, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, depth + 1, f"{path}[{index}]")

    walk(schema, 0, label)
    if Draft202012Validator is None:  # pragma: no cover
        raise ValueError("JSON Schema Draft 2020-12 校验器不可用")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise ValueError(f"{label} 不是合法的 Draft 2020-12 schema: {exc.message}") from exc
    definitions = schema.get("$defs", {})
    for ref in local_refs:
        name = ref[len("#/$defs/"):]
        if not isinstance(definitions, dict) or name not in definitions:
            raise ValueError(f"{label} 引用了不存在的本地定义：{ref}")
    return schema


def validate_structured_output_text(text: str, config: "StructuredOutputConfig") -> str | None:
    """Return a diagnostic string when completed text violates structured output."""

    import json

    try:
        value = json.loads(
            text,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"非有限 JSON 数字：{token}")
            ),
        )
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return f"输出不是合法 JSON：{exc}"
    if config.type == "json_object":
        if not isinstance(value, dict):
            return "json_object 输出必须是 JSON object"
        return None
    assert config.schema_ is not None
    try:
        Draft202012Validator(config.schema_).validate(value)
    except Exception as exc:  # ValidationError is intentionally not a public dependency
        return f"输出不符合 structured schema：{exc}"
    return None


def _validate_identifier(value: str, prefix: str, label: str = "id") -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        raise ValueError(f"{label} 总长度必须为 1-128 字符")
    if not value.startswith(prefix):
        raise ValueError(f"{label} 必须使用 {prefix} 前缀")
    suffix = value[len(prefix):]
    if not suffix or not _ID_RE.fullmatch(suffix):
        raise ValueError(f"{label} 后缀只允许字母、数字、下划线和连字符")
    return value


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


def validate_kemo_protocol_version(value: Any) -> str:
    """Accept only explicitly supported Kemo wire versions."""

    if value not in SUPPORTED_PROTOCOL_VERSIONS:
        raise ValueError(
            f"不支持的 protocol_version：{value!r}；支持 {SUPPORTED_PROTOCOL_VERSIONS}"
        )
    return str(value)


class ProtocolModel(BaseModel, Mapping[str, Any]):
    model_config = ConfigDict(
        extra="forbid",
        # JSON ingress is strict at the transport/parser boundary; Python
        # mapping inputs remain compatible with adapter normalization (enum
        # strings and RFC3339 timestamp strings are intentionally accepted).
        # L1 numeric/bool constraints are enforced by field validators and
        # bounded tool-argument parsing below.
        strict=False,
        allow_inf_nan=False,
        populate_by_name=True,
        use_enum_values=True,
        validate_default=True,
    )

    @field_serializer("*", when_used="json")
    def serialize_wire_value(self, value: Any) -> Any:
        """Emit all protocol timestamps as UTC with millisecond precision.

        Validation still accepts any timezone-aware RFC3339 value, but the
        wire contract has one representation.  A wildcard serializer keeps
        direct ``model_dump_json`` calls (including SSE events) consistent
        with the shared serialization helpers.
        """

        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise ValueError("协议时间必须带时区")
            value = value.astimezone(timezone.utc)
            value = value.replace(microsecond=(value.microsecond // 1000) * 1000)
        return value

    def model_post_init(self, __context: Any) -> None:
        # Keep Python objects and their wire round-trip representation aligned
        # as well: protocol timestamps are UTC/millisecond values, not local
        # timezone or microsecond-specific values that only become normalized
        # at serialization time.
        for name, value in self.__dict__.items():
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    raise ValueError("协议时间必须带时区")
                normalized = value.astimezone(timezone.utc).replace(
                    microsecond=(value.microsecond // 1000) * 1000
                )
                object.__setattr__(self, name, normalized)

    def __getitem__(self, key: str) -> Any:
        if key in type(self).model_fields:
            return getattr(self, key)
        for name, field_info in type(self).model_fields.items():
            if field_info.alias == key:
                return getattr(self, name)
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return iter(
            field_info.alias or name
            for name, field_info in type(self).model_fields.items()
        )

    def __len__(self) -> int:
        return len(type(self).model_fields)


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
        if self.kind in {"object_store", "url", "data_url"}:
            if not self.uri or any(
                value is not None for value in (self.provider, self.file_id, self.data)
            ):
                raise ValueError(f"source.kind={self.kind} 只允许非空 uri")
        elif self.kind == "provider_file_id":
            if not (self.provider and self.file_id) or any(
                value is not None for value in (self.uri, self.data)
            ):
                raise ValueError("provider_file_id 只允许 provider + file_id")
        elif self.kind == "inline_base64":
            if not self.data or any(
                value is not None for value in (self.uri, self.provider, self.file_id)
            ):
                raise ValueError("inline_base64 只允许非空 data")
        if self.expires_at is not None and self.expires_at.tzinfo is None:
            raise ValueError("expires_at 必须带时区")
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
        return (
            _validate_identifier(value, "asset_", "asset_id")
            if value is not None
            else None
        )

    @model_validator(mode="after")
    def validate_reference(self) -> "AssetContent":
        if not self.asset_id and self.source is None:
            raise ValueError("媒体内容至少需要 asset_id 或 source")
        if self.checksum_sha256 is not None:
            checksum = self.checksum_sha256.strip().casefold()
            if not _SHA256_RE.fullmatch(checksum):
                raise ValueError("checksum_sha256 必须是 64 位十六进制 SHA-256")
            self.checksum_sha256 = checksum
        if self.source is not None and self.source.kind in {"inline_base64", "data_url"}:
            if getattr(self, "type", None) != "image":
                raise ValueError("只有图片允许 data_url/inline_base64")
            payload = self.source.data
            if self.source.kind == "data_url":
                match = re.fullmatch(
                    r"data:([^;,]+);base64,(.+)", self.source.uri or "", re.DOTALL
                )
                if match is None:
                    raise ValueError("data_url 必须是 Base64 Data URL")
                if self.mime_type and self.mime_type.casefold() != match.group(1).casefold():
                    raise ValueError("data_url MIME 与 mime_type 不一致")
                payload = match.group(2)
            try:
                decoded = base64.b64decode(payload or "", validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("内联图片必须是有效 Base64") from exc
            if not decoded or len(decoded) > MAX_INLINE_IMAGE_BYTES:
                raise ValueError("内联图片解码后必须为 1..1048576 字节")
        return self


class ImageContent(AssetContent):
    type: Literal["image"] = "image"
    detail: Literal["auto", "low", "high"] = "auto"
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class AudioContent(AssetContent):
    type: Literal["audio"] = "audio"
    duration_ms: int | None = Field(default=None, ge=0)
    format: str | None = None
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


class RefusalContent(ProtocolModel):
    type: Literal["refusal"] = "refusal"
    text: str = Field(min_length=1)


ContentBlock = Annotated[
    Union[
        TextContent,
        ImageContent,
        AudioContent,
        VideoContent,
        FileContent,
        JsonContent,
        ReferenceContent,
        RefusalContent,
    ],
    Field(discriminator="type"),
]


class TopLogprob(ProtocolModel):
    token: str
    logprob: float
    bytes: list[int] | None = None

    @field_validator("logprob")
    @classmethod
    def validate_logprob(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("logprob 必须是有限数值")
        return value

    @field_validator("bytes")
    @classmethod
    def validate_bytes(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(
            type(item) is not int or not 0 <= item <= 255 for item in value
        ):
            raise ValueError("logprob bytes 必须为 0..255 整数")
        return value


class TokenLogprob(TopLogprob):
    top: list[TopLogprob] = Field(default_factory=list, max_length=20)


class MessageLogprobs(ProtocolModel):
    content: list[TokenLogprob] = Field(default_factory=list)


class Annotation(ProtocolModel):
    type: Literal["url_citation", "file_citation"]
    content_index: int = Field(ge=0)
    url: str | None = None
    file_id: str | None = None
    title: str | None = None
    start_index: int | None = Field(default=None, ge=0)
    end_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_annotation(self) -> "Annotation":
        if self.type == "url_citation":
            if not self.url or self.file_id is not None:
                raise ValueError("url_citation 只允许非空 url")
        elif not self.file_id or self.url is not None:
            raise ValueError("file_citation 只允许非空 file_id")
        if (self.start_index is None) != (self.end_index is None):
            raise ValueError("start_index/end_index 必须同时出现")
        if self.start_index is not None and self.start_index > self.end_index:
            raise ValueError("citation 索引必须 start <= end")
        return self


class ItemBase(ProtocolModel):
    id: str
    status: ItemStatus = ItemStatus.COMPLETED
    created_at: datetime | None = None

    @model_validator(mode="after")
    def validate_item_base(self) -> "ItemBase":
        prefixes = {
            "message": "msg_",
            "reasoning": "rs_",
            "tool_call": "call_",
            "tool_result": "result_",
        }
        _validate_identifier(self.id, prefixes[self.type], f"{self.type}.id")
        if self.created_at is not None and self.created_at.tzinfo is None:
            raise ValueError("created_at 必须带时区")
        return self


class MessageItem(ItemBase):
    type: Literal["message"] = "message"
    role: MessageRole
    phase: MessagePhase | None = None
    content: list[ContentBlock] = Field(default_factory=list)
    refusal: str | None = None
    logprobs: MessageLogprobs | None = None
    annotations: list[Annotation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_phase(self) -> "MessageItem":
        if self.role in {MessageRole.USER, MessageRole.DEVELOPER} and self.phase is not None:
            raise ValueError("user/developer message 不允许 phase")
        if (
            self.refusal is not None or self.logprobs is not None or self.annotations
        ) and self.role != MessageRole.ASSISTANT:
            raise ValueError("refusal/logprobs/annotations 只允许 assistant")
        if self.refusal and any(isinstance(block, RefusalContent) for block in self.content):
            raise ValueError("MessageItem.refusal 与 RefusalContent 不能同时出现")
        if any(isinstance(block, RefusalContent) for block in self.content) and self.role != MessageRole.ASSISTANT:
            raise ValueError("RefusalContent 只允许 assistant message")
        if not self.content and not (self.refusal and self.refusal.strip()):
            raise ValueError("最终 MessageItem 必须包含 content 或 refusal")
        for annotation in self.annotations:
            if (
                annotation.content_index >= len(self.content)
                or not isinstance(self.content[annotation.content_index], TextContent)
            ):
                raise ValueError("annotation.content_index 必须指向 TextContent")
            if (
                annotation.end_index is not None
                and annotation.end_index > len(self.content[annotation.content_index].text)
            ):
                raise ValueError("annotation 索引超出 Unicode code point 文本范围")
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
    data: str = Field(min_length=1)
    provider: str = Field(min_length=1)
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

    @model_validator(mode="after")
    def validate_call_id(self) -> "ToolCallItem":
        _validate_identifier(self.call_id, "callid_", "call_id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.name):
            raise ValueError("工具 name 只允许 1-64 位 [A-Za-z0-9_-]")
        return self


class ToolResultItem(ItemBase):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    is_error: bool = False
    content: list[ContentBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_result_id(self) -> "ToolResultItem":
        _validate_identifier(self.call_id, "callid_", "call_id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.name):
            raise ValueError("工具 name 只允许 1-64 位 [A-Za-z0-9_-]")
        return self


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

    @model_validator(mode="after")
    def validate_enabled(self) -> "ReasoningConfig":
        if not self.enabled and (self.effort != "none" or self.return_mode != "none"):
            raise ValueError("reasoning.enabled=false 时 effort/return 必须为 none")
        if self.enabled and self.effort == "none":
            raise ValueError("reasoning.enabled=true 时 effort 不能为 none")
        return self


class LogprobsConfig(ProtocolModel):
    enabled: bool = True
    top_k: int = Field(default=0, ge=0, le=20)


class GenerationConfig(ProtocolModel):
    max_output_tokens: int | None = Field(default=None, ge=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    top_k: int | None = Field(default=None, ge=1)
    stop: list[str] | None = Field(default=None, min_length=1, max_length=4)
    n: int = Field(default=1, ge=1, le=128)
    seed: int | None = None
    logit_bias: dict[str, float] | None = None
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    logprobs: LogprobsConfig | None = None
    verbosity: Literal["low", "medium", "high"] | None = None

    @model_validator(mode="after")
    def validate_generation(self) -> "GenerationConfig":
        if self.stop is not None and any(not item for item in self.stop):
            raise ValueError("stop 必须是 1-4 个非空字符串")
        if self.logit_bias is not None and any(
            not math.isfinite(value) or not -100 <= value <= 100
            for value in self.logit_bias.values()
        ):
            raise ValueError("logit_bias 值必须在 [-100,100] 且有限")
        return self


class StructuredOutputConfig(ProtocolModel):
    type: Literal["json_schema", "json_object"]
    schema_name: str | None = Field(default=None, max_length=64)
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")
    strict: bool = False

    @model_validator(mode="after")
    def validate_shape(self) -> "StructuredOutputConfig":
        if self.type == "json_schema":
            if not self.schema_name or not self.schema_:
                raise ValueError("json_schema 要求非空 schema_name/schema")
            _validate_json_schema_definition(self.schema_, label="structured_output.schema")
        elif self.schema_name is not None or self.schema_ is not None or self.strict:
            raise ValueError("json_object 禁止 schema/schema_name/strict")
        return self


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

    @model_validator(mode="after")
    def validate_definition(self) -> "ToolDefinition":
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.name):
            raise ValueError("工具 name 只允许 1-64 位 [A-Za-z0-9_-]")
        # Tool argument execution owns its provider-specific depth/size error
        # envelope; retain a valid (possibly very deep) schema here so callers
        # receive the documented ``schema_limit`` diagnostic at execution.
        _validate_json_schema_definition(
            self.parameters, label="function.parameters", enforce_limits=False
        )
        return self


class ToolChoice(ProtocolModel):
    mode: ToolChoiceMode = ToolChoiceMode.AUTO
    name: str | None = None
    allowed_tools: list[str] | None = None
    allowed_mode: Literal["auto", "required"] | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> "ToolChoice":
        if self.mode == ToolChoiceMode.NAMED:
            if not self.name or self.allowed_tools is not None or self.allowed_mode is not None:
                raise ValueError("named 要求 name，且禁止 allowed_tools/allowed_mode")
        elif self.mode == ToolChoiceMode.ALLOWED:
            if not self.allowed_tools or self.allowed_mode is None or self.name is not None:
                raise ValueError("allowed 要求 allowed_tools + allowed_mode，且禁止 name")
            if len(self.allowed_tools) != len(set(self.allowed_tools)):
                raise ValueError("allowed_tools 不得重复")
        elif self.name is not None or self.allowed_tools is not None or self.allowed_mode is not None:
            raise ValueError("auto/none/required 禁止 name/allowed_tools/allowed_mode")
        return self


class ServiceConfig(ProtocolModel):
    tier: str | None = None
    prompt_cache_key: str | None = None
    safety_identifier: str | None = Field(default=None, max_length=64)
    store: bool | None = None


class PredictionConfig(ProtocolModel):
    type: Literal["content"] = "content"
    content: str | list[ContentBlock]

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str | list[ContentBlock]) -> str | list[ContentBlock]:
        if not value:
            raise ValueError("prediction.content 不能为空")
        return value


class ReasoningCapabilities(ProtocolModel):
    supported: bool = False
    efforts: list[str] = Field(default_factory=list)
    summary: bool = False
    persisted_state: bool = False
    contexts: list[Literal["none", "current_turn", "all_turns", "auto"]] = Field(
        default_factory=lambda: ["none", "auto"]
    )
    returns: list[Literal["none", "summary", "content", "auto"]] = Field(
        default_factory=lambda: ["none", "auto"]
    )

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
        if not self.supported and (self.efforts or self.summary or self.persisted_state):
            raise ValueError("reasoning.supported=false 时高级能力必须关闭")
        if self.summary != ("summary" in self.returns):
            raise ValueError("reasoning.summary 必须与 returns 包含 summary 一致")
        return self


class ToolCapabilities(ProtocolModel):
    function_calling: bool = False
    parallel_calls: bool = False
    multimodal_results: bool = False
    tool_choice_modes: list[ToolChoiceMode] = Field(
        default_factory=lambda: [ToolChoiceMode.AUTO, ToolChoiceMode.NONE]
    )

    @model_validator(mode="after")
    def validate_modes(self) -> "ToolCapabilities":
        if len(self.tool_choice_modes) != len(set(self.tool_choice_modes)):
            raise ValueError("tool_choice_modes 不得重复")
        if not self.function_calling and (
            self.parallel_calls
            or any(
                mode not in {ToolChoiceMode.AUTO, ToolChoiceMode.NONE}
                for mode in self.tool_choice_modes
            )
        ):
            raise ValueError("function_calling=false 时不能声明并行或强制工具模式")
        return self


class StructuredOutputCapabilities(ProtocolModel):
    supported: bool = False
    strict: bool = False
    schema_subset: list[Literal["json_object", "json_schema"]] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_support_contract(self) -> "StructuredOutputCapabilities":
        if not self.supported and (self.strict or self.schema_subset):
            raise ValueError(
                "structured_output.supported=false 时 strict/schema_subset 必须关闭"
            )
        if self.strict and not self.supported:
            raise ValueError("structured_output.strict=true 必须同时 supported=true")
        if len(self.schema_subset) != len(set(self.schema_subset)):
            raise ValueError("structured_output.schema_subset 不得重复")
        return self


class DecodingCapabilities(ProtocolModel):
    temperature: bool = False
    top_p: bool = False
    verbosity: bool = False
    seed: bool = False
    logit_bias: bool = False
    presence_penalty: bool = False
    frequency_penalty: bool = False
    top_k: bool = False
    stop: bool = False
    logprobs: bool = False
    top_logprobs_max: int = Field(default=0, ge=0, le=20)


class LimitsCapabilities(ProtocolModel):
    context_window: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    max_tools: int | None = Field(default=None, gt=0)
    max_parallel_tools: int | None = Field(default=None, gt=0)
    max_images: int | None = Field(default=None, gt=0)
    max_choices: int = Field(default=1, ge=1, le=128)


class MetricsCapabilities(ProtocolModel):
    input_price_per_million: float | None = Field(default=None, ge=0)
    output_price_per_million: float | None = Field(default=None, ge=0)
    currency: str | None = None


class EmbeddingCapabilities(ProtocolModel):
    """Kemo 向量模型能力声明。"""

    input_types: list[Literal["query", "document"]]
    default_dimensions: int = Field(gt=0)
    supported_dimensions: list[int] = Field(default_factory=list)
    max_batch_size: int = Field(gt=0)
    max_input_tokens_per_item: int | None = Field(default=None, gt=0)
    normalization: Literal["always", "optional", "never", "unknown"] = "unknown"
    supports_truncate: bool = False


class RerankCapabilities(ProtocolModel):
    """Kemo 重排序模型能力声明。"""

    max_documents: int = Field(gt=0)
    max_query_tokens: int | None = Field(default=None, gt=0)
    max_document_tokens: int | None = Field(default=None, gt=0)
    supports_return_documents: bool = True
    score_semantics: Literal["higher_is_more_relevant"] = "higher_is_more_relevant"
    supports_score_threshold: bool = False


class ModelCapabilities(ExtensionModel):
    protocol_version: str
    model: str = Field(min_length=1)
    provider_id: str = Field(min_length=1)
    provider_model: str = Field(min_length=1)
    task: Literal["llm", "embedding", "rerank"] = "llm"
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])
    output_modalities: list[str] = Field(default_factory=lambda: ["text"])
    streaming: bool = False
    reasoning: ReasoningCapabilities = Field(default_factory=ReasoningCapabilities)
    tools: ToolCapabilities = Field(default_factory=ToolCapabilities)
    structured_output: StructuredOutputCapabilities = Field(
        default_factory=StructuredOutputCapabilities
    )
    decoding: DecodingCapabilities = Field(default_factory=DecodingCapabilities)
    limits: LimitsCapabilities = Field(default_factory=LimitsCapabilities)
    supports_multiple_choices: bool = False
    supports_prediction: bool = False
    service_tiers: list[str] = Field(default_factory=list)
    metrics: MetricsCapabilities | None = None
    embedding: EmbeddingCapabilities | None = None
    rerank: RerankCapabilities | None = None

    @model_validator(mode="after")
    def validate_task_capabilities(self) -> "ModelCapabilities":
        validate_kemo_protocol_version(self.protocol_version)
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
        if self.supports_multiple_choices != (self.limits.max_choices >= 2):
            raise ValueError(
                "supports_multiple_choices 必须与 limits.max_choices 是否至少为 2 一致"
            )
        if len(self.service_tiers) != len(set(self.service_tiers)):
            raise ValueError("service_tiers 不得重复")
        if self.task == "llm" and not self.output_modalities:
            raise ValueError("LLM capabilities 必须声明 output_modalities")
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
    protocol_version: str
    supported_protocol_versions: list[str]
    object: Literal["kemo.model_list"] = "kemo.model_list"
    count: int = Field(ge=0)
    data: list[ModelCatalogItem]

    @model_validator(mode="after")
    def validate_catalog(self) -> "ModelCatalogResponse":
        validate_kemo_protocol_version(self.protocol_version)
        if (
            not self.supported_protocol_versions
            or self.protocol_version not in self.supported_protocol_versions
        ):
            raise ValueError("supported_protocol_versions 必须非空且包含当前版本")
        if self.count != len(self.data):
            raise ValueError("catalog count 必须等于 data 长度")
        if len({item.id for item in self.data}) != len(self.data):
            raise ValueError("catalog model id 必须唯一")
        return self


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
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    measurement: Measurement = Field(default_factory=Measurement)
    media: MediaUsage | None = None


class Usage(ProtocolModel):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    visible_output_tokens: int | None = Field(default=None, ge=0)
    audio_input_tokens: int | None = Field(default=None, ge=0)
    audio_output_tokens: int | None = Field(default=None, ge=0)
    accepted_prediction_tokens: int | None = Field(default=None, ge=0)
    rejected_prediction_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    measurement: Measurement = Field(default_factory=Measurement)
    media: MediaUsage | None = None
    stages: list[StageUsage] = Field(default_factory=list)
    provider_raw: dict[str, Any] = Field(default_factory=dict)


class EmbeddingInput(ProtocolModel):
    """One text item sent to the gateway embedding endpoint."""

    id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmbeddingRequest(ProtocolModel):
    protocol_version: str
    request_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1)
    input_type: Literal["query", "document"]
    inputs: list[EmbeddingInput] = Field(min_length=1, max_length=2048)
    dimensions: int | None = Field(default=None, gt=0)
    normalize: bool | None = None
    truncate: Literal["none", "end", "start"] = "none"
    provider_options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "EmbeddingRequest":
        validate_kemo_protocol_version(self.protocol_version)
        _validate_identifier(self.request_id, "req_", "request_id")
        ids = [item.id for item in self.inputs]
        if len(ids) != len(set(ids)):
            raise ValueError("embedding input id 必须唯一")
        return self


class EmbeddingData(ProtocolModel):
    id: str
    index: int = Field(ge=0)
    vector: list[float]

    @field_validator("vector")
    @classmethod
    def validate_vector(cls, value: list[float]) -> list[float]:
        if not value or any(not math.isfinite(item) for item in value):
            raise ValueError("embedding vector 必须是非空有限数值数组")
        return value


class EmbeddingResponse(ProtocolModel):
    protocol_version: str = PROTOCOL_VERSION
    object: Literal["kemo.embedding_list"] = "kemo.embedding_list"
    request_id: str
    model: str
    model_version: str | None = None
    vector_space_id: str
    dimensions: int = Field(gt=0)
    data: list[EmbeddingData]
    truncated_ids: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    provider_response_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_data(self) -> "EmbeddingResponse":
        if not self.request_id.startswith("req_"):
            raise ValueError("embedding request_id 必须使用 req_ 前缀")
        if len({item.id for item in self.data}) != len(self.data):
            raise ValueError("embedding data id 必须唯一")
        if [item.index for item in self.data] != list(range(len(self.data))):
            raise ValueError("embedding data.index 必须从 0 连续递增")
        if any(len(item.vector) != self.dimensions for item in self.data):
            raise ValueError("embedding vector 维度与 dimensions 不一致")
        if len(set(self.truncated_ids)) != len(self.truncated_ids):
            raise ValueError("truncated_ids 不得重复")
        if set(self.truncated_ids) & {item.id for item in self.data}:
            raise ValueError("truncated_ids 不得包含已返回 data id")
        return self


class RerankDocument(ProtocolModel):
    """A document supplied to the gateway rerank endpoint."""

    id: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=2_000_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RerankRequest(ProtocolModel):
    protocol_version: str
    request_id: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1)
    query: str = Field(min_length=1, max_length=2_000_000)
    documents: list[RerankDocument] = Field(min_length=1, max_length=4096)
    top_n: int | None = Field(default=None, gt=0)
    return_documents: bool = False
    score_threshold: float | None = None
    provider_options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_documents(self) -> "RerankRequest":
        validate_kemo_protocol_version(self.protocol_version)
        _validate_identifier(self.request_id, "req_", "request_id")
        ids = [item.id for item in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("rerank document id 必须唯一")
        if self.top_n is not None and self.top_n > len(self.documents):
            raise ValueError("top_n 不能超过 documents 数量")
        if self.score_threshold is not None and not math.isfinite(self.score_threshold):
            raise ValueError("score_threshold 必须是有限数值")
        return self


class RerankResultItem(ProtocolModel):
    rank: int = Field(ge=1)
    document_id: str
    index: int = Field(ge=0)
    relevance_score: float
    document: RerankDocument | None = None

    @field_validator("relevance_score")
    @classmethod
    def validate_score(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("relevance_score 必须是有限数值")
        return value


class RerankResponse(ProtocolModel):
    protocol_version: str = PROTOCOL_VERSION
    object: Literal["kemo.rerank"] = "kemo.rerank"
    request_id: str
    model: str
    model_version: str | None = None
    score_semantics: Literal["higher_is_more_relevant"] = "higher_is_more_relevant"
    results: list[RerankResultItem]
    filtered_count: int = Field(default=0, ge=0)
    usage: Usage = Field(default_factory=Usage)
    provider_response_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_results(self) -> "RerankResponse":
        if not self.request_id.startswith("req_"):
            raise ValueError("rerank request_id 必须使用 req_ 前缀")
        if len({item.document_id for item in self.results}) != len(self.results):
            raise ValueError("rerank document_id 必须唯一")
        if len({item.index for item in self.results}) != len(self.results):
            raise ValueError("rerank result.index 必须唯一")
        if [item.rank for item in self.results] != list(range(1, len(self.results) + 1)):
            raise ValueError("rerank rank 必须从 1 连续递增")
        if self.filtered_count < 0:
            raise ValueError("filtered_count 不能小于 0")
        return self


class UnifiedError(ProtocolModel):
    type: str
    code: ErrorCode
    message: str
    retryable: bool = False
    retry_after_ms: int | None = Field(default=None, ge=0)
    retry_scope: Literal["same_sequence", "new_sequence"] = "same_sequence"
    provider_status: int | None = None
    provider_request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class KemoRequest(ExtensionModel):
    protocol_version: str
    request_id: str
    parent_request_id: str | None = None
    attempt: int = Field(default=1, ge=1)
    model: str
    stream: bool = True
    system_prompt: str = ""
    input: list[Item] = Field(default_factory=list)
    tools: list[ToolDefinition] = Field(default_factory=list)
    tool_choice: ToolChoice = Field(default_factory=ToolChoice)
    parallel_tool_calls: bool = True
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    reasoning: ReasoningConfig | None = None
    structured_output: StructuredOutputConfig | None = None
    output: OutputConfig = Field(default_factory=OutputConfig)
    prediction: PredictionConfig | None = None
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(cls, **kwargs: Any) -> "KemoRequest":
        """SDK factory that explicitly supplies negotiated wire fields."""

        kwargs.setdefault("protocol_version", PROTOCOL_VERSION)
        kwargs.setdefault("request_id", _identifier("req"))
        return cls(**kwargs)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        return validate_kemo_protocol_version(value)

    @field_validator("request_id", "parent_request_id")
    @classmethod
    def validate_request_ids(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_identifier(
                value,
                "req_",
                "request_id" if value is not None else "parent_request_id",
            )
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
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("tools.name 必须唯一")
        known_names = set(names)
        if (
            self.tool_choice.mode == ToolChoiceMode.NAMED
            and self.tool_choice.name not in known_names
        ):
            raise ValueError("tool_choice.name 不在 tools")
        if self.tool_choice.mode == ToolChoiceMode.ALLOWED and not set(
            self.tool_choice.allowed_tools or []
        ) <= known_names:
            raise ValueError("allowed_tools 包含未知工具")
        if not self.tools and self.tool_choice.mode not in {
            ToolChoiceMode.AUTO,
            ToolChoiceMode.NONE,
        }:
            raise ValueError("tools 为空时 tool_choice 只能 auto/none")
        if self.generation.n > 1 and (
            self.stream
            or self.tools
            or calls
            or self.structured_output is not None
        ):
            raise ValueError("n>1 仅支持非流式、无工具、无 structured_output")
        if (
            self.stream
            and self.generation.logprobs is not None
            and self.generation.logprobs.enabled
        ):
            raise ValueError("流式请求不支持 logprobs")
        return self


class IncompleteDetails(ProtocolModel):
    reason: IncompleteReason
    message: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_reason(self) -> "IncompleteDetails":
        if self.reason == IncompleteReason.OTHER and not self.details:
            raise ValueError("incomplete reason=other 时 details 必须非空")
        return self


class ErrorEnvelope(ProtocolModel):
    """HTTP error body shared by every Kemo 2.0 public endpoint."""

    protocol_version: str = PROTOCOL_VERSION
    object: Literal["kemo.error"] = "kemo.error"
    error: UnifiedError
    request_id: str | None = None

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        return validate_kemo_protocol_version(value)

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_identifier(value, "req_", "request_id")
        return value


class AssetDescriptor(ExtensionModel):
    """Authenticated Kemo Asset metadata returned by the gateway."""

    protocol_version: str
    id: str
    object: Literal["kemo.asset"] = "kemo.asset"
    status: Literal["uploading", "processing", "ready", "failed", "deleted"]
    purpose: Literal["input", "output"]
    filename: str | None = Field(default=None, max_length=255)
    mime_type: str | None = None
    size: int | None = Field(default=None, ge=0)
    checksum_sha256: str | None = None
    created_at: datetime
    expires_at: datetime
    error: UnifiedError | None = None

    @model_validator(mode="after")
    def validate_asset_descriptor(self) -> "AssetDescriptor":
        validate_kemo_protocol_version(self.protocol_version)
        _validate_identifier(self.id, "asset_", "asset.id")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("asset 时间必须带时区")
        if self.expires_at < self.created_at:
            raise ValueError("asset.expires_at 不能早于 created_at")
        if self.status == "ready" and any(
            value is None for value in (self.mime_type, self.size, self.checksum_sha256)
        ):
            raise ValueError("ready asset 必须包含 mime_type/size/checksum_sha256")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed asset 必须包含 error")
        if self.status != "failed" and self.error is not None:
            raise ValueError("只有 failed asset 可以包含 error")
        if self.checksum_sha256 is not None and not _SHA256_RE.fullmatch(
            self.checksum_sha256
        ):
            raise ValueError("checksum_sha256 必须是 64 位小写十六进制")
        return self

class ServiceReceipt(ProtocolModel):
    tier: str | None = None
    store_applied: bool | None = None


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
    incomplete_details: IncompleteDetails | None = None
    provider_response_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now().astimezone())
    # A response is not complete merely because its object was constructed.
    # In particular, the in-flight query representation is incomplete/running
    # and must omit completed_at until the terminal CAS is committed.
    completed_at: datetime | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    system_fingerprint: str | None = None
    service: ServiceReceipt | None = None
    choice_index: int = Field(default=0, ge=0)
    choice_count: int = Field(default=1, ge=1)

    @field_validator("protocol_version")
    @classmethod
    def validate_protocol_version(cls, value: str) -> str:
        return validate_kemo_protocol_version(value)

    @model_validator(mode="after")
    def validate_status(self) -> "KemoResponse":
        _validate_identifier(self.id, "resp_", "response.id")
        _validate_identifier(self.request_id, "req_", "request_id")
        if self.choice_index >= self.choice_count:
            raise ValueError("choice_index 必须小于 choice_count")
        if self.created_at.tzinfo is None or (
            self.completed_at is not None and self.completed_at.tzinfo is None
        ):
            raise ValueError("响应时间必须带时区")
        running = (
            self.incomplete_details is not None
            and self.incomplete_details.reason == IncompleteReason.RUNNING
        )
        # Direct SDK/provider constructors frequently omit the timestamp
        # because they create a terminal result synchronously.  Materialize
        # the completion boundary from the already-created execution time;
        # running/in-flight responses remain the only valid shape with a
        # null completed_at.  Wire serializers therefore never emit a
        # terminal response with a missing completion timestamp.
        if not running and self.completed_at is None:
            self.completed_at = self.created_at
        if not running and self.completed_at is None:
            raise ValueError("终态响应必须包含 completed_at")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("completed_at 不能早于 created_at")
        if self.latency_ms is not None and self.completed_at is None:
            raise ValueError("latency_ms 要求 completed_at")
        if self.status == ResponseStatus.FAILED and self.error is None:
            raise ValueError("failed response 必须包含 error")
        if self.status != ResponseStatus.FAILED and self.error is not None:
            raise ValueError("只有 failed response 可以包含 error")
        if self.status == ResponseStatus.REQUIRES_ACTION and not any(
            isinstance(item, ToolCallItem) for item in self.output
        ):
            raise ValueError("requires_action response 必须包含 tool_call")
        if self.status == ResponseStatus.INCOMPLETE and self.incomplete_details is None:
            raise ValueError("incomplete response 必须包含 incomplete_details")
        if self.status != ResponseStatus.INCOMPLETE and self.incomplete_details is not None:
            raise ValueError("只有 incomplete response 可以包含 incomplete_details")
        if running and self.completed_at is not None:
            raise ValueError("running 查询不能包含 completed_at")
        ids = [item.id for item in self.output]
        if len(ids) != len(set(ids)):
            raise ValueError("response.output item id 不得重复")
        if self.status == ResponseStatus.COMPLETED and not self.output:
            raise ValueError("completed response 必须包含完成结果")
        for item in self.output:
            if item.status == ItemStatus.IN_PROGRESS:
                raise ValueError("终态 output 不得包含 in_progress Item")
            if isinstance(item, ToolResultItem):
                raise ValueError("response.output 不允许 ToolResultItem")
            if isinstance(item, MessageItem):
                if item.role != MessageRole.ASSISTANT:
                    raise ValueError("response.output message 必须使用 assistant role")
                _validate_output_media_item(item)
        return self


class KemoResponseBatch(ExtensionModel):
    protocol_version: str = PROTOCOL_VERSION
    object: Literal["kemo.response_batch"] = "kemo.response_batch"
    request_id: str
    model: str
    responses: list[KemoResponse] = Field(min_length=2, max_length=128)
    usage: Usage = Field(default_factory=Usage)

    @model_validator(mode="after")
    def validate_batch(self) -> "KemoResponseBatch":
        validate_kemo_protocol_version(self.protocol_version)
        _validate_identifier(self.request_id, "req_", "request_id")
        count = len(self.responses)
        if [response.choice_index for response in self.responses] != list(range(count)):
            raise ValueError("batch choice_index 必须连续升序")
        if len({response.id for response in self.responses}) != count:
            raise ValueError("batch response.id 必须唯一")
        if any(
            response.choice_count != count
            or response.request_id != self.request_id
            or response.model != self.model
            or response.protocol_version != self.protocol_version
            for response in self.responses
        ):
            raise ValueError("batch 候选共享字段或 choice_count 不一致")
        return self


ResponseEnvelope = Annotated[
    Union[KemoResponse, KemoResponseBatch], Field(discriminator="object")
]


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
