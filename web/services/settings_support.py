"""Pure configuration/version helpers shared by the settings service.

The service mixin owns request validation and persistence; this module owns
side-effect-free redaction/merge helpers and the remote version probe.
"""

from __future__ import annotations

import json
import math
import re
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


# Null is a runtime value for these memory rules, rather than a request to
# delete the key and inherit its default.
_NULLABLE_MEMORY_FIELDS = {
    *(f"memory.tiers.{tier}.{field}" for tier in ("seven_days", "one_month", "half_year")
      for field in ("days", "upgrade_threshold", "next")),
    "memory.domain_rules.relation.upgrade_threshold",
    "memory.domain_rules.state.upgrade_threshold",
}


def _merge_patch(
    target: dict[str, Any], changes: dict[str, Any], *, prefix: str = "",
) -> dict[str, Any]:
    result = dict(target)
    for key, value in changes.items():
        if not isinstance(key, str) or not key:
            raise InvalidRequestError("配置字段名必须是非空字符串")
        dotted = f"{prefix}.{key}" if prefix else key
        if value is None and dotted not in _NULLABLE_MEMORY_FIELDS:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_patch(result[key], value, prefix=dotted)
        else:
            # Assign plain values as-is so nested nulls still reach the section
            # validators instead of being normalised into empty objects.
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
        list_result = []
        redacted = []
        for index, item in enumerate(value):
            clean, nested = _redact_config(item, (*path, str(index)))
            list_result.append(clean)
            redacted.extend(nested)
        return list_result, redacted
    return value, []




def validate_settings_patch(config: dict[str, Any], changes: dict[str, Any]) -> None:
    """Validate editable configuration at the HTTP write boundary."""
    from run.config import validate_provider_security
    from run.memory import (
        memory_domain_rules, memory_extraction_candidate_limit,
        memory_extraction_input_tokens, memory_extraction_mode, tier_rules,
    )

    boolean_fields = {
        "tools.enabled", "task_plan.auto_retry_on_fix", "cron.enabled",
        "memory.history_read_enabled", "runtime_host.enable_background_scheduler",
        "memory.domain_rules.state.refresh_expiry_on_update",
    }
    # Bounds match the runtime consumers. Fractional seconds are accepted only
    # where the consumer uses a floating point duration.
    number_fields = {
        "memory.important_memory_output_max_chars": (1, None, True),
        "agents.important_memory_review_hours": (1 / 60, None, False),
        "agent_runtime.timeout_survival_seconds": (0, None, False),
        "history_summary.poll_interval": (0.01, None, False),
        "history_summary.max_jobs_per_cycle": (1, None, True),
        "history_summary.max_attempts": (1, None, True),
        "cron.log_retention_days": (0, None, True),
        "task_cron_system.sense_update_rate": (1, None, True),
        "task_cron_system.module_update_timeout": (1, None, True),
        "task_cron_system.runtime_checkpoint_seconds": (0.01, None, False),
        "task_cron_system.success_log_flush_seconds": (0.01, None, False),
        "message.max_workers": (1, None, True),
    }

    def check(patch: dict[str, Any], prefix: str = "") -> None:
        for key, value in patch.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                check(value, path)
            if path in boolean_fields and type(value) is not bool:
                raise ValueError(f"{path} 必须是布尔值")
            if path in number_fields and value is not None:
                minimum, maximum, integer = number_fields[path]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or (integer and type(value) is not int)
                    or value < minimum or (maximum is not None and value > maximum)):
                    raise ValueError(f"{path} 必须是大于等于 {minimum:g} 的有限{'整数' if integer else '数值'}")
            if path == "agents.daily_memory_review_time" and value is not None:
                if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                    raise ValueError(f"{path} 必须是有效的 HH:MM 时间")
            if path == "history_summary.retry_delays_seconds" and value is not None:
                if not isinstance(value, list) or not value or any(type(item) is not int or item < 1 for item in value):
                    raise ValueError(f"{path} 必须是非空正整数数组")
            if path == "plugins.permissions" and value is not None:
                if not isinstance(value, list) or any(not isinstance(item, str) or item not in {"read", "write", "delete", "external"} for item in value):
                    raise ValueError(f"{path} 只允许 read、write、delete、external 数组")

    check(changes)
    if "memory" in changes:
        memory_domain_rules(config)
        memory_extraction_mode(config)
        memory_extraction_input_tokens(config)
        memory_extraction_candidate_limit(config, 1)
        tier_rules(config)
    if "provider" in changes:
        validate_provider_security(config)


def validate_settings_document(config: dict[str, Any]) -> None:
    """Validate one merged document with the rules the runtime itself applies."""
    from provider.retry import RetryManager
    from run.config import (
        media_asset_ttl_seconds, memory_prompt_injection,
        parse_prompt_settings, validate_cardstream_settings,
    )

    validate_cardstream_settings(config)
    media_asset_ttl_seconds(config)
    for kind in ("tiered", "important"):
        memory_prompt_injection(config, kind=kind)
    parse_prompt_settings(config)
    RetryManager(max_retries=(config.get("provider_runtime") or {}).get("max_retries", 3))
