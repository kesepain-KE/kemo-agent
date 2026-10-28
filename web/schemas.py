"""Web API 请求体模型。

这些模型集中描述 HTTP 边界，不依赖具体路由注册位置。旧入口
``web.app`` 继续导入并暴露它们，以保持现有导入兼容性。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictBool, model_validator


class ChatBody(BaseModel):
    user: str
    source: str = Field(default="web", pattern="^(web|app)$")
    session_id: str
    prompt: str = ""
    content: list[dict[str, Any]] = Field(default_factory=list)
    uploaded_files: list[str] = Field(default_factory=list, max_length=20)
    run_id: str = ""
    plan_id: str = ""
    client_id: str = ""

    @model_validator(mode="after")
    def require_input(self) -> "ChatBody":
        if (
            not self.prompt.strip()
            and not self.content
            and not self.uploaded_files
            and not self.plan_id.strip()
        ):
            raise ValueError("prompt、content、uploaded_files 和 plan_id 不能同时为空")
        return self


class LoginBody(BaseModel):
    username: str
    password: str


class TokenLoginBody(BaseModel):
    token: str


class GuidanceBody(BaseModel):
    user: str
    source: str = Field(default="web", pattern="^(web|app)$")
    session_id: str = Field(min_length=1, max_length=128)
    guidance_id: str = Field(default="", max_length=160)
    guidance: str = Field(default="", max_length=1_000_000)
    uploaded_files: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_input(self) -> "GuidanceBody":
        if not self.guidance.strip() and not self.uploaded_files:
            raise ValueError("guidance 和 uploaded_files 不能同时为空")
        return self


class RunCancelBody(BaseModel):
    user: str
    source: str = Field(default="web", pattern="^(web|app)$")
    session_id: str = Field(min_length=1, max_length=128)


class SessionRenameBody(BaseModel):
    title: str


class SessionMarksBody(BaseModel):
    model_config = {"extra": "forbid"}
    pinned: StrictBool | None = None
    favorite: StrictBool | None = None

    @model_validator(mode="after")
    def require_marks(self) -> "SessionMarksBody":
        if not self.model_fields_set or any(
            getattr(self, field) is None for field in self.model_fields_set
        ):
            raise ValueError("请提供 pinned 或 favorite 布尔值，不允许 null")
        return self


class SessionClientBody(BaseModel):
    client_id: str = ""
    lease_sequence: int | None = Field(default=None, strict=True, ge=1, le=9_007_199_254_740_991)


class SessionUndoLastRoundBody(BaseModel):
    expected_round: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=1_000_000)


class LongTaskPreferenceBody(BaseModel):
    enabled: StrictBool


class SoulBody(BaseModel):
    content: str


class TextBody(BaseModel):
    content: str


class KnowledgeClassificationBody(BaseModel):
    category: str = Field(default="", max_length=80)


class MemoryWriteBody(BaseModel):
    content: str
    tier: str | None = None


class PreferencesBody(BaseModel):
    theme: str | None = None
    font_size: str | None = None

    @model_validator(mode="before")
    @classmethod
    def language_is_user_config(cls, value: Any) -> Any:
        if isinstance(value, dict) and {"language", "background"}.intersection(value):
            raise ValueError("语言与背景请通过用户配置 web_ui 保存")
        return value


class SkillToggleBody(BaseModel):
    enabled: bool


class ModulePanelValuesBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    clear_secrets: list[str] = Field(default_factory=list, max_length=64)


class ModulePanelActionBody(BaseModel):
    command: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)


class DeleteManyBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=10_000)


class RestartBody(BaseModel):
    port: int = Field(ge=1, le=65535)
    force: bool = False
