"""Pure configuration/version helpers shared by the settings service.

The service mixin owns request validation and persistence; this module owns
side-effect-free redaction/merge helpers and the remote version probe.
"""

from __future__ import annotations

import json
import math
import socket
from typing import Any
import urllib.error
import urllib.request

from web.constants import _REDACTED, _SENSITIVE_CONFIG_KEYS
from web.errors import InvalidRequestError


class _VersionCheckFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fetch_remote_version_manifest(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "kemo-agent-web-version-check"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _VersionCheckFailure(
                "remote_manifest_missing",
                "云端 version.json 不存在，请检查发布分支是否完整。",
            ) from exc
        raise _VersionCheckFailure(
            "remote_http_error",
            f"GitHub 返回 HTTP {exc.code}，请稍后重新检查。",
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        raise _VersionCheckFailure(
            "remote_timeout",
            "连接 GitHub 超时，请检查服务器网络后重试。",
        ) from exc
    except urllib.error.URLError as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "无法连接 GitHub，请检查服务器网络或代理设置。",
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise _VersionCheckFailure(
            "remote_unreachable",
            "读取云端版本信息失败，请检查服务器网络后重试。",
        ) from exc
    except json.JSONDecodeError as exc:
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 格式错误，暂时无法比较版本。",
        ) from exc
    if not isinstance(payload, dict):
        raise _VersionCheckFailure(
            "invalid_remote_manifest",
            "云端 version.json 不是有效对象，暂时无法比较版本。",
        )
    return payload


def _positive_float_or_default(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) and parsed > 0 else default


def _contains_redacted_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_redacted_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_redacted_placeholder(item) for item in value)
    return value == _REDACTED


def _merge_patch(target: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    result = dict(target)
    for key, value in changes.items():
        if not isinstance(key, str) or not key:
            raise InvalidRequestError("配置字段名必须是非空字符串")
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_patch(result[key], value)
        else:
            result[key] = value
    return result


def _is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return lowered in _SENSITIVE_CONFIG_KEYS or lowered.endswith("_secret")


def _redact_config(value: Any, path: tuple[str, ...] = ()) -> tuple[Any, list[str]]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        redacted: list[str] = []
        for key, item in value.items():
            rendered = str(key)
            current = (*path, rendered)
            if _is_sensitive_key(rendered):
                result[rendered] = _REDACTED
                redacted.append(".".join(current))
                continue
            clean, nested = _redact_config(item, current)
            result[rendered] = clean
            redacted.extend(nested)
        return result, redacted
    if isinstance(value, list):
        result = []
        redacted: list[str] = []
        for index, item in enumerate(value):
            clean, nested = _redact_config(item, (*path, str(index)))
            result.append(clean)
            redacted.extend(nested)
        return result, redacted
    return value, []


