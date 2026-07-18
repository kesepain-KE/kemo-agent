"""Web authentication configuration and signed-session state helpers."""

from __future__ import annotations

import hmac
import os
import re
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any


_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SESSION_KEY = "kemo_web_auth"


class WebAuthConfigError(ValueError):
    pass


class WebAuthError(RuntimeError):
    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class WebAuthConfig:
    access_token: str = ""
    username: str = ""
    password: str = ""
    session_secret: str = ""
    cookie_name: str = "kemo_agent_session"

    def __post_init__(self) -> None:
        username = self.username.strip()
        cookie_name = self.cookie_name.strip() or "kemo_agent_session"
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "cookie_name", cookie_name)
        if bool(username) != bool(self.password):
            raise WebAuthConfigError("WEB_USERNAME 与 WEB_PASSWORD 必须同时配置或同时留空")
        if self.enabled and not self.session_secret:
            raise WebAuthConfigError("启用 Web 鉴权时必须配置 WEB_SESSION_SECRET")
        if not _COOKIE_NAME.fullmatch(cookie_name):
            raise WebAuthConfigError("WEB_SESSION_COOKIE_NAME 不是合法的 Cookie 名称")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "WebAuthConfig":
        source = os.environ if environ is None else environ
        return cls(
            access_token=source.get("WEB_ACCESS_TOKEN", ""),
            username=source.get("WEB_USERNAME", ""),
            password=source.get("WEB_PASSWORD", ""),
            session_secret=source.get("WEB_SESSION_SECRET", ""),
            cookie_name=source.get("WEB_SESSION_COOKIE_NAME", "kemo_agent_session"),
        )

    @property
    def token_enabled(self) -> bool:
        return bool(self.access_token)

    @property
    def password_enabled(self) -> bool:
        return bool(self.username and self.password)

    @property
    def enabled(self) -> bool:
        return self.token_enabled or self.password_enabled

    def public_summary(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "token_enabled": self.token_enabled,
            "password_enabled": self.password_enabled,
            "session_cookie_configured": bool(self.enabled and self.cookie_name),
        }


def _same_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class WebAuthenticator:
    def __init__(self, config: WebAuthConfig) -> None:
        self.config = config

    def is_authenticated(self, session: Mapping[str, Any] | None = None) -> bool:
        if not self.config.enabled:
            return True
        state = session.get(_SESSION_KEY) if session is not None else None
        return isinstance(state, dict) and state.get("authenticated") is True

    def status(self, session: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "authenticated": self.is_authenticated(session),
            "methods": {
                "token": self.config.token_enabled,
                "password": self.config.password_enabled,
            },
            "session_cookie_configured": bool(
                self.config.enabled and self.config.cookie_name
            ),
        }

    def authenticate_token(self, token: str) -> None:
        if not self.config.token_enabled:
            raise WebAuthError("auth_method_disabled", "Token 认证未启用", 409)
        if not _same_secret(str(token), self.config.access_token):
            raise WebAuthError("invalid_credentials", "认证信息无效", 401)

    def authenticate_password(self, username: str, password: str) -> None:
        if not self.config.password_enabled:
            raise WebAuthError("auth_method_disabled", "账号密码认证未启用", 409)
        username_ok = _same_secret(str(username), self.config.username)
        password_ok = _same_secret(str(password), self.config.password)
        if not (username_ok and password_ok):
            raise WebAuthError("invalid_credentials", "认证信息无效", 401)

    @staticmethod
    def establish(session: MutableMapping[str, Any], method: str) -> None:
        session.clear()
        session[_SESSION_KEY] = {
            "authenticated": True,
            "method": method,
            "issued_at": int(time.time()),
        }

    @staticmethod
    def logout(session: MutableMapping[str, Any]) -> None:
        session.clear()
