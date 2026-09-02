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


class SessionClientBody(BaseModel):
    client_id: str = ""


class SessionUndoLastRoundBody(BaseModel):
    expected_round: int = Field(ge=1)
    prompt: str = Field(min_length=1, max_length=1_000_000)


class LongTaskPreferenceBody(BaseModel):
    enabled: StrictBool


class SoulBody(BaseModel):
    content: str


class TextBody(BaseModel):
    content: str


class MemoryWriteBody(BaseModel):
    content: str
    tier: str | None = None


class PreferencesBody(BaseModel):
    theme: str | None = None
    font_size: str | None = None


class SkillToggleBody(BaseModel):
    enabled: bool


class DeleteManyBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=10_000)


class RestartBody(BaseModel):
    port: int = Field(ge=1, le=65535)
    force: bool = False
