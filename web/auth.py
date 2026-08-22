"""Web authentication configuration and signed-session state helpers."""

from __future__ import annotations

import hmac
import ipaddress
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any


_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_SESSION_KEY = "kemo_web_auth"
WEB_SESSION_MAX_AGE_SECONDS = 2 * 60 * 60
WEB_PARTIAL_SESSION_MAX_AGE_SECONDS = 5 * 60


class WebAuthConfigError(ValueError):
    pass


class WebAuthError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        status: int,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.headers = dict(headers or {})


def _env_nonnegative_int(
    source: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    raw = str(source.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise WebAuthConfigError(f"{name} 必须是整数") from exc
    if value < 0:
        raise WebAuthConfigError(f"{name} 不能小于 0")
    return value


def _env_positive_int(
    source: Mapping[str, str],
    name: str,
    default: int,
) -> int:
    value = _env_nonnegative_int(source, name, default)
    if value < 1:
        raise WebAuthConfigError(f"{name} 必须大于 0")
    return value


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    raw = str(source.get(name, "")).strip().casefold()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise WebAuthConfigError(f"{name} 必须是 true/false")


def _trusted_proxies(value: str) -> tuple[str, ...]:
    entries = tuple(item.strip() for item in str(value).split(",") if item.strip())
    for entry in entries:
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError as exc:
            raise WebAuthConfigError(
                f"WEB_AUTH_TRUSTED_PROXIES 包含无效 IP 或网段：{entry}"
            ) from exc
    return entries


@dataclass(frozen=True, slots=True)
class WebAuthConfig:
    access_token: str = ""
    username: str = ""
    password: str = ""
    session_secret: str = ""
    cookie_name: str = "kemo_agent_session"
    cookie_secure: bool = False
    ip_max_failures: int = 0
    ip_window_seconds: int = 600
    ip_lock_seconds: int = 900
    trusted_proxies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        username = self.username.strip()
        cookie_name = self.cookie_name.strip() or "kemo_agent_session"
        session_secret = self.session_secret or secrets.token_hex(32)
        object.__setattr__(self, "username", username)
        object.__setattr__(self, "cookie_name", cookie_name)
        object.__setattr__(self, "session_secret", session_secret)
        object.__setattr__(self, "trusted_proxies", tuple(self.trusted_proxies))
        if bool(username) != bool(self.password):
            raise WebAuthConfigError("WEB_USERNAME 与 WEB_PASSWORD 必须同时配置或同时留空")
        if not _COOKIE_NAME.fullmatch(cookie_name):
            raise WebAuthConfigError("WEB_SESSION_COOKIE_NAME 不是合法的 Cookie 名称")
        if self.ip_max_failures < 0:
            raise WebAuthConfigError("WEB_AUTH_IP_MAX_FAILURES 不能小于 0")
        if self.ip_window_seconds < 1:
            raise WebAuthConfigError("WEB_AUTH_IP_WINDOW_SECONDS 必须大于 0")
        if self.ip_lock_seconds < 1:
            raise WebAuthConfigError("WEB_AUTH_IP_LOCK_SECONDS 必须大于 0")
        _trusted_proxies(",".join(self.trusted_proxies))

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "WebAuthConfig":
        source = os.environ if environ is None else environ
        return cls(
            access_token=source.get("WEB_ACCESS_TOKEN", ""),
            username=source.get("WEB_USERNAME", ""),
            password=source.get("WEB_PASSWORD", ""),
            session_secret=source.get("WEB_SESSION_SECRET", ""),
            cookie_name=source.get("WEB_SESSION_COOKIE_NAME", "kemo_agent_session"),
            cookie_secure=_env_bool(source, "WEB_SESSION_COOKIE_SECURE", False),
            ip_max_failures=_env_nonnegative_int(
                source, "WEB_AUTH_IP_MAX_FAILURES", 0
            ),
            ip_window_seconds=_env_positive_int(
                source, "WEB_AUTH_IP_WINDOW_SECONDS", 600
            ),
            ip_lock_seconds=_env_positive_int(
                source, "WEB_AUTH_IP_LOCK_SECONDS", 900
            ),
            trusted_proxies=_trusted_proxies(
                source.get("WEB_AUTH_TRUSTED_PROXIES", "")
            ),
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

    @property
    def requires_both(self) -> bool:
        return self.token_enabled and self.password_enabled

    def public_summary(self) -> dict[str, bool]:
        return {
            "enabled": self.enabled,
            "token_enabled": self.token_enabled,
            "password_enabled": self.password_enabled,
            "session_cookie_configured": bool(self.enabled and self.cookie_name),
            "ip_rate_limit_enabled": self.ip_max_failures > 0,
        }


def _same_secret(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


class WebAuthenticator:
    def __init__(self, config: WebAuthConfig) -> None:
        self.config = config

    def _state(self, session: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        state = session.get(_SESSION_KEY) if session is not None else None
        return state if isinstance(state, dict) else None

    def is_authenticated(self, session: Mapping[str, Any] | None = None) -> bool:
        if not self.config.enabled:
            return True
        state = self._state(session)
        if not state or state.get("authenticated") is not True:
            return False
        method = state.get("method")
        if self.config.requires_both:
            return method == "token+password"
        if self.config.token_enabled:
            return method == "token"
        return method == "password"

    def token_stage_verified(self, session: Mapping[str, Any] | None = None) -> bool:
        if not self.config.requires_both:
            return False
        state = self._state(session)
        if not state or state.get("stage") != "password":
            return False
        try:
            expires_at = int(state.get("expires_at") or 0)
        except (TypeError, ValueError):
            return False
        return expires_at > int(time.time())

    def stage(self, session: Mapping[str, Any] | None = None) -> str:
        if not self.config.enabled:
            return "none"
        if self.is_authenticated(session):
            return "authenticated"
        if self.config.requires_both and self.token_stage_verified(session):
            return "password"
        if self.config.token_enabled:
            return "token"
        return "password"

    def status(self, session: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "authenticated": self.is_authenticated(session),
            "methods": {
                "token": self.config.token_enabled,
                "password": self.config.password_enabled,
            },
            "stage": self.stage(session),
            "requires_both": self.config.requires_both,
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
    def establish_token_stage(session: MutableMapping[str, Any]) -> None:
        now = int(time.time())
        session.clear()
        session[_SESSION_KEY] = {
            "authenticated": False,
            "stage": "password",
            "issued_at": now,
            "expires_at": now + WEB_PARTIAL_SESSION_MAX_AGE_SECONDS,
        }

    @staticmethod
    def logout(session: MutableMapping[str, Any]) -> None:
        session.clear()


class AuthFailureLimiter:
    """Thread-safe, in-memory failed-authentication limiter keyed by IP and stage."""

    def __init__(self, config: WebAuthConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], list[float]] = {}
        self._locked_until: dict[tuple[str, str], float] = {}

    @property
    def enabled(self) -> bool:
        return self.config.ip_max_failures > 0

    def _trim_locked(self, key: tuple[str, str], now: float) -> None:
        cutoff = now - self.config.ip_window_seconds
        failures = [value for value in self._failures.get(key, ()) if value >= cutoff]
        if failures:
            self._failures[key] = failures
        else:
            self._failures.pop(key, None)
        if self._locked_until.get(key, 0) <= now:
            self._locked_until.pop(key, None)

    def _raise_locked(self, retry_after: float) -> None:
        seconds = max(1, math.ceil(retry_after))
        raise WebAuthError(
            "auth_rate_limited",
            "认证失败次数过多，请稍后再试",
            429,
            headers={"Retry-After": str(seconds)},
        )

    def check(self, client_ip: str, stage: str) -> None:
        if not self.enabled:
            return
        key = (client_ip, stage)
        now = time.monotonic()
        with self._lock:
            self._trim_locked(key, now)
            locked_until = self._locked_until.get(key, 0)
            if locked_until > now:
                self._raise_locked(locked_until - now)

    def failure(self, client_ip: str, stage: str) -> None:
        if not self.enabled:
            return
        key = (client_ip, stage)
        now = time.monotonic()
        with self._lock:
            self._trim_locked(key, now)
            failures = self._failures.setdefault(key, [])
            failures.append(now)
            if len(failures) >= self.config.ip_max_failures:
                locked_until = now + self.config.ip_lock_seconds
                self._locked_until[key] = locked_until
                self._raise_locked(self.config.ip_lock_seconds)

    def success(self, client_ip: str, stage: str) -> None:
        if not self.enabled:
            return
        key = (client_ip, stage)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)

    def clear_ip(self, client_ip: str) -> None:
        if not self.enabled:
            return
        with self._lock:
            for key in [item for item in self._failures if item[0] == client_ip]:
                self._failures.pop(key, None)
            for key in [item for item in self._locked_until if item[0] == client_ip]:
                self._locked_until.pop(key, None)


def resolve_client_ip(
    peer: str,
    forwarded_for: str,
    trusted_proxies: tuple[str, ...],
) -> str:
    """Use X-Forwarded-For only when the direct peer is explicitly trusted."""

    fallback = str(peer or "unknown").strip() or "unknown"
    try:
        peer_address = ipaddress.ip_address(fallback)
    except ValueError:
        return fallback
    networks = tuple(ipaddress.ip_network(item, strict=False) for item in trusted_proxies)
    if not networks or not any(peer_address in network for network in networks):
        return str(peer_address)
    forwarded = [item.strip() for item in str(forwarded_for).split(",") if item.strip()]
    try:
        chain = [ipaddress.ip_address(item) for item in forwarded]
    except ValueError:
        return str(peer_address)
    for address in reversed(chain):
        if any(address in network for network in networks):
            continue
        return str(address)
    return str(peer_address)
