"""Web-facing service adapter over existing Run, history and user APIs."""

from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any, Callable, Iterator

from events import RunEvent
from run.engine import iter_request_events
from run.history import find_window, list_sessions, session_messages
from run.users import list_users


_SESSION_RE = re.compile(r"^[^\x00-\x1f]{1,128}$")


class WebServiceError(RuntimeError):
    code = "internal_error"
    status = 500


class InvalidRequestError(WebServiceError):
    code = "invalid_request"
    status = 400


class NotFoundError(WebServiceError):
    code = "not_found"
    status = 404


class WebRunService:
    """A thin, injectable boundary between HTTP routes and the Run core."""

    def __init__(
        self,
        root: Path,
        *,
        event_source: Callable[..., Iterator[RunEvent]] = iter_request_events,
    ) -> None:
        self.root = root.resolve()
        self.event_source = event_source

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "service": "kemo-agent-web", "version": 1}

    def users(self) -> list[dict[str, str]]:
        return [{"name": user} for user in list_users(self.root)]

    def require_user(self, user: Any) -> str:
        if not isinstance(user, str) or not user.strip():
            raise InvalidRequestError("user 必须是非空字符串")
        name = user.strip()
        if name not in set(list_users(self.root)):
            raise NotFoundError(f"用户不存在：{name}")
        return name

    def require_source(self, source: Any = "web") -> str:
        if source != "web":
            raise InvalidRequestError("Web API 当前仅允许 source=web")
        return "web"

    def require_session_id(self, session_id: Any) -> str:
        if not isinstance(session_id, str):
            raise InvalidRequestError("session_id 必须是字符串")
        value = session_id.strip()
        if not _SESSION_RE.fullmatch(value):
            raise InvalidRequestError("session_id 必须是 1–128 字符且不能包含控制字符")
        return value

    def require_prompt(self, prompt: Any) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise InvalidRequestError("prompt 必须是非空字符串")
        return prompt.strip()

    def sessions(self, user: Any, *, source: Any = "web") -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        return {
            "user": name,
            "source": normalized_source,
            "sessions": list_sessions(self.root, name, normalized_source),
        }

    def history(
        self,
        user: Any,
        session_id: Any,
        *,
        source: Any = "web",
    ) -> dict[str, Any]:
        name = self.require_user(user)
        normalized_source = self.require_source(source)
        normalized_session = self.require_session_id(session_id)
        if find_window(self.root, name, normalized_source, normalized_session) is None:
            raise NotFoundError(f"会话不存在：{normalized_session}")
        return {
            "user": name,
            "source": normalized_source,
            "session_id": normalized_session,
            "messages": session_messages(
                self.root, name, normalized_source, normalized_session
            ),
        }

    def stream_chat(
        self,
        user: Any,
        session_id: Any,
        prompt: Any,
        *,
        cancel_event: threading.Event,
    ) -> Iterator[RunEvent]:
        name = self.require_user(user)
        normalized_session = self.require_session_id(session_id)
        normalized_prompt = self.require_prompt(prompt)
        request = {
            "user": name,
            "source": "web",
            "session_id": normalized_session,
            "prompt": normalized_prompt,
            "stream": True,
        }
        return self.event_source(
            request,
            root=self.root,
            cancel_event=cancel_event,
        )
