from __future__ import annotations

import asyncio
from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from web.api_contracts import ResponseObject
from web.service import WebRunService


class DecisionAnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: StrictStr = Field(default="", max_length=80)
    text: StrictStr = Field(default="", max_length=8000)

    @model_validator(mode="after")
    def one_answer(self):
        if bool(self.option_id) == bool(self.text.strip()):
            raise ValueError("请选择一个预设选项，或填写自定义回复")
        return self


class DecisionOption(ResponseObject):
    id: str
    label: str
    description: str = ""
    recommended: bool = False


class UserDecision(ResponseObject):
    decision_id: str
    integration_id: str
    title: str
    question: str
    options: list[DecisionOption]
    mode: str
    status: str
    created_at: float
    expires_at: float
    answer: dict | None
    resolution: str


class DecisionsResponse(ResponseObject):
    user: str
    source: str
    session_id: str
    enabled: bool
    revision: int
    decisions: list[UserDecision]


class DecisionAnswerResponse(ResponseObject):
    decision: UserDecision


def register_decision_routes(app: FastAPI, backend: WebRunService):
    @app.get("/api/users/{user}/sessions/{session_id}/decisions", response_model=DecisionsResponse)
    async def decisions(user: str, session_id: str, source: str = Query(default="web")):
        return await asyncio.to_thread(backend.decisions, user, session_id, source=source)

    @app.post("/api/users/{user}/sessions/{session_id}/decisions/{decision_id}/answer",
              response_model=DecisionAnswerResponse)
    async def answer(user: str, session_id: str, decision_id: str, body: DecisionAnswerBody,
                     source: str = Query(default="web")):
        return await asyncio.to_thread(
            backend.answer_decision, user, session_id, decision_id, source=source,
            option_id=body.option_id, text=body.text,
        )
