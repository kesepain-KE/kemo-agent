"""设备令牌、用户密码、短期会话和内存限流。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def trusted_proxy_networks(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        entries = tuple(item.strip() for item in value.split(",") if item.strip())
    elif isinstance(value, (list, tuple)):
        entries = tuple(str(item).strip() for item in value if str(item).strip())
    elif value in (None, ""):
        entries = ()
    else:
        raise ValueError("trusted_proxies 必须是字符串或字符串列表")
    normalized: list[str] = []
    for entry in entries:
        try:
            normalized.append(str(ipaddress.ip_network(entry, strict=False)))
        except ValueError as exc:
            raise ValueError(f"trusted_proxies 包含无效 IP 或网段：{entry}") from exc
    return tuple(normalized)


def resolve_client_ip(peer: str, forwarded_for: str, trusted_proxies: tuple[str, ...]) -> str:
    """Honor X-Forwarded-For only when the direct peer is explicitly trusted."""

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


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def token_ok(auth_header: str, config: dict[str, Any]) -> bool:
    if not auth_header or not auth_header.startswith("Bearer "):
        return False
    raw = auth_header[7:].strip()
    expected = str(config.get("token_sha256", "")).strip().lower()
    if not raw or len(expected) != 64:
        return False
    actual = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return hmac.compare_digest(actual, expected)


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, maximum: int, window_seconds: int = 60) -> tuple[bool, int]:
        if maximum <= 0:
            return True, 0
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._events[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= maximum:
                retry = max(1, int(window_seconds - (now - bucket[0])))
                return False, retry
            bucket.append(now)
        return True, 0


class UserStore:
    def __init__(self, path: Path, iterations: int = 310_000) -> None:
        self.path = path
        self.iterations = max(100_000, int(iterations))

    def _load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def verify(self, username: str, password: str) -> bool:
        record = self._load().get(username)
        if not isinstance(record, dict) or not record.get("enabled", True):
            return False
        try:
            salt = bytes.fromhex(str(record["salt"]))
            expected = bytes.fromhex(str(record["hash"]))
            iterations = int(record.get("iterations", self.iterations))
        except (KeyError, TypeError, ValueError):
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)

    def set_password(self, username: str, password: str, enabled: bool = True) -> None:
        if not username or len(password) < 10:
            raise ValueError("username 不能为空，password 至少 10 个字符")
        data = self._load()
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.iterations)
        data[username] = {
            "salt": salt.hex(),
            "hash": digest.hex(),
            "iterations": self.iterations,
            "enabled": bool(enabled),
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


@dataclass(frozen=True)
class Session:
    username: str
    expires_at: int


class SessionManager:
    def __init__(self, secret: str, ttl_seconds: int = 7200) -> None:
        self.secret = (secret or secrets.token_hex(32)).encode("utf-8")
        self.ttl_seconds = max(300, int(ttl_seconds))
        self._active: dict[str, Session] = {}
        self._lock = threading.Lock()

    def issue(self, username: str) -> tuple[str, int]:
        expires_at = int(time.time()) + self.ttl_seconds
        payload = _b64encode(json.dumps({"u": username, "e": expires_at, "n": secrets.token_hex(8)}, separators=(",", ":")).encode("utf-8"))
        signature = _b64encode(hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).digest())
        token = f"{payload}.{signature}"
        with self._lock:
            self._active[token] = Session(username, expires_at)
            self._prune_locked()
        return token, expires_at

    def verify(self, token: str) -> Session | None:
        if not token or "." not in token:
            return None
        payload, signature = token.rsplit(".", 1)
        expected = _b64encode(hmac.new(self.secret, payload.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            decoded = json.loads(_b64decode(payload))
            username = str(decoded["u"])
            expires_at = int(decoded["e"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            return None
        if expires_at <= int(time.time()):
            with self._lock:
                self._active.pop(token, None)
            return None
        with self._lock:
            active = self._active.get(token)
        if active is None or active.username != username or active.expires_at != expires_at:
            return None
        return active

    def revoke(self, token: str) -> None:
        with self._lock:
            self._active.pop(token, None)

    def _prune_locked(self) -> None:
        now = int(time.time())
        expired = [key for key, value in self._active.items() if value.expires_at <= now]
        for key in expired:
            self._active.pop(key, None)
