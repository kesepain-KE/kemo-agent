"""Read-only Kemo gateway status collection and chart rendering."""

from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "gateway_config.json"
MANIFEST_PATH = BASE_DIR / "expand.json"
INPUT_PATH = BASE_DIR / "input_data.md"
LAST_RUN_PATH = BASE_DIR / "_last_run.json"
DATA_PATH = BASE_DIR / "data" / "gateway_status.json"
CHART_PATH = BASE_DIR / "artifacts" / "gateway_status.png"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class GatewayStatusError(RuntimeError):
    """A secret-safe extension failure."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Keep the STATUS_TOKEN on the explicitly configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


@dataclass(frozen=True)
class GatewayConfig:
    base_url: str
    status_token: str
    timeout_seconds: int = 15
    ranking_limit: int = 20
    log_limit: int = 20

    @property
    def status_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/status"


def _atomic_text(path: Path, content: str, *, sensitive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if sensitive:
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
        os.replace(temporary, path)
        if sensitive:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: Any, *, sensitive: bool = False) -> None:
    _atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        sensitive=sensitive,
    )


def _integer(value: Any, *, name: str, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise GatewayStatusError(f"{name} 必须是整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise GatewayStatusError(f"{name} 必须是整数") from exc
    if parsed < minimum or parsed > maximum:
        raise GatewayStatusError(f"{name} 必须在 {minimum}..{maximum} 之间")
    return parsed


def _normalized_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        raise GatewayStatusError("缺少 Kemo 网关 base_url")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in raw):
        raise GatewayStatusError("base_url 不允许包含空白符或控制字符")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GatewayStatusError("base_url 必须是有效的 http 或 https 地址")
    if parsed.username or parsed.password:
        raise GatewayStatusError("base_url 不允许包含用户名或密码")
    if parsed.query or parsed.fragment:
        raise GatewayStatusError("base_url 不允许包含查询参数或片段")
    return raw


def config_from_mapping(value: dict[str, Any]) -> GatewayConfig:
    if not isinstance(value, dict):
        raise GatewayStatusError("网关拓展配置必须是 JSON 对象")
    token = str(value.get("status_token") or "").strip()
    if not token:
        raise GatewayStatusError("缺少网关独立 STATUS_TOKEN")
    if len(token) > 4096 or any(ord(character) < 32 or ord(character) == 127 for character in token):
        raise GatewayStatusError("STATUS_TOKEN 格式无效")
    return GatewayConfig(
        base_url=_normalized_base_url(value.get("base_url")),
        status_token=token,
        timeout_seconds=_integer(
            value.get("timeout_seconds"), name="timeout_seconds", default=15, minimum=2, maximum=60
        ),
        ranking_limit=_integer(
            value.get("ranking_limit"), name="ranking_limit", default=20, minimum=1, maximum=100
        ),
        log_limit=_integer(
            value.get("log_limit"), name="log_limit", default=20, minimum=1, maximum=100
        ),
    )


def load_config() -> GatewayConfig | None:
    if not CONFIG_PATH.is_file():
        return None
    try:
        payload = json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GatewayStatusError("gateway_config.json 无法读取或不是有效 JSON") from exc
    return config_from_mapping(payload)


def _config_payload(config: GatewayConfig) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "base_url": config.base_url,
        "status_token": config.status_token,
        "timeout_seconds": config.timeout_seconds,
        "ranking_limit": config.ranking_limit,
        "log_limit": config.log_limit,
    }


def configuration_status() -> dict[str, Any]:
    config = load_config()
    return {
        "ok": True,
        "active": config is not None,
        "base_url": config.base_url if config else "",
        "status_token_configured": bool(config),
        "timeout_seconds": config.timeout_seconds if config else 15,
        "ranking_limit": config.ranking_limit if config else 20,
        "log_limit": config.log_limit if config else 20,
    }


def _error_detail(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail[:300]
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:300]
    return ""


def _open_status_request(
    request: urllib.request.Request,
    *,
    timeout: int,
    context: ssl.SSLContext | None,
):
    handlers: list[Any] = [_RejectRedirects()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers).open(request, timeout=timeout)


def fetch_status(config: GatewayConfig, *, target_date: str | None = None) -> dict[str, Any]:
    params: dict[str, str | int] = {
        "ranking_limit": config.ranking_limit,
        "log_limit": config.log_limit,
    }
    if target_date:
        try:
            date_type.fromisoformat(target_date)
        except ValueError as exc:
            raise GatewayStatusError("date 必须是 YYYY-MM-DD") from exc
        params["date"] = target_date
    url = f"{config.status_url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Authorization": f"Bearer {config.status_token}",
            "Accept": "application/json",
            "User-Agent": "kemo-agent-gateway-status-expand/1.0",
        },
    )
    context = ssl.create_default_context() if url.startswith("https://") else None
    try:
        with _open_status_request(
            request,
            timeout=config.timeout_seconds,
            context=context,
        ) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc.read(4096)).replace(config.status_token, "***")
        suffix = f"：{detail}" if detail else ""
        raise GatewayStatusError(f"网关状态接口返回 HTTP {exc.code}{suffix}") from exc
    except urllib.error.URLError as exc:
        raise GatewayStatusError(f"无法连接 Kemo 网关状态接口：{type(exc.reason).__name__}") from exc
    except TimeoutError as exc:
        raise GatewayStatusError("连接 Kemo 网关状态接口超时") from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise GatewayStatusError("网关状态响应超过 8 MB 安全上限")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GatewayStatusError("网关状态接口没有返回有效 JSON") from exc
    if not isinstance(payload, dict) or payload.get("object") != "kemo.gateway_status":
        raise GatewayStatusError("响应不是 kemo.gateway_status 状态快照")
    return sanitize_snapshot(payload)


def _safe_dict(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value.get(key) for key in keys if key in value}


_METRIC_KEYS = (
    "calls", "successes", "failures", "cancellations", "incompletes", "running",
    "replay_count", "success_rate", "average_latency_ms", "cache_hit_rate",
    "cache_eligible_samples", "token_coverage", "tokens",
)
_LOG_KEYS = (
    "started_at", "finished_at", "task", "provider_id", "model", "provider_model",
    "gateway_key_id", "status", "error_code", "error_type", "latency_ms",
)
_TOKEN_KEYS = (
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
    "visible_output_tokens", "total_tokens",
)


def _clean_metrics(value: Any, *, identifier: bool = False) -> dict[str, Any]:
    cleaned = _safe_dict(value, (("id",) if identifier else ()) + _METRIC_KEYS)
    cleaned["tokens"] = _safe_dict(cleaned.get("tokens"), _TOKEN_KEYS)
    coverage = cleaned.get("token_coverage")
    cleaned["token_coverage"] = {
        str(key)[:64]: item
        for key, item in coverage.items()
        if isinstance(key, str) and isinstance(item, (int, float)) and not isinstance(item, bool)
    } if isinstance(coverage, dict) else {}
    return cleaned


def _clean_log(value: Any) -> dict[str, Any]:
    cleaned = _safe_dict(value, _LOG_KEYS)
    cleaned["tokens"] = _safe_dict(value.get("tokens"), _TOKEN_KEYS) if isinstance(value, dict) else {}
    usage = value.get("usage") if isinstance(value, dict) else None
    cleaned["usage"] = _safe_dict(usage, ("mode", "exact"))
    return cleaned


def _ranking(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    result: list[dict[str, Any]] = []
    for item in items[:100]:
        if not isinstance(item, dict):
            continue
        result.append(_clean_metrics(item, identifier=True))
    return result


def _logs(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    return [_clean_log(item) for item in items[:100] if isinstance(item, dict)]


def sanitize_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist only explicitly allow-listed, secret-free status fields."""
    runtime = _safe_dict(
        payload.get("runtime"),
        ("instance_id", "phase", "active_executions", "started_at", "drain_reason"),
    )
    version_raw = payload.get("version")
    version = _safe_dict(
        version_raw,
        ("status", "update_available", "source", "checked_at", "message"),
    )
    if isinstance(version_raw, dict):
        version["local"] = _safe_dict(
            version_raw.get("local"), ("version", "protocol_version", "build", "notes")
        )
        version["remote"] = _safe_dict(
            version_raw.get("remote"), ("version", "protocol_version", "build", "notes")
        )
    registry_raw = payload.get("registry") if isinstance(payload.get("registry"), dict) else {}
    providers: list[dict[str, Any]] = []
    for item in registry_raw.get("providers", []) if isinstance(registry_raw, dict) else []:
        if isinstance(item, dict):
            provider = _safe_dict(item, ("provider_id", "enabled"))
            provider["registered_models"] = [
                str(model)[:256] for model in item.get("registered_models", [])[:500]
                if isinstance(model, str)
            ] if isinstance(item.get("registered_models"), list) else []
            providers.append(provider)
    enabled_models = []
    for item in registry_raw.get("enabled_models", []) if isinstance(registry_raw, dict) else []:
        if isinstance(item, dict):
            enabled_models.append(_safe_dict(item, ("model", "provider_id")))
    control_raw = payload.get("control") if isinstance(payload.get("control"), dict) else {}
    statistics_raw = payload.get("statistics") if isinstance(payload.get("statistics"), dict) else {}
    rankings_raw = statistics_raw.get("rankings") if isinstance(statistics_raw.get("rankings"), dict) else {}
    logs_raw = payload.get("logs") if isinstance(payload.get("logs"), dict) else {}
    last_invocation = logs_raw.get("last_invocation")
    return {
        "object": "kemo.gateway_status.safe_snapshot",
        "generated_at": payload.get("generated_at"),
        "protocol_version": payload.get("protocol_version"),
        "runtime": runtime,
        "version": version,
        "registry": {
            "providers": providers,
            "registered_provider_ids": [
                str(item)[:128] for item in registry_raw.get("registered_provider_ids", [])[:100]
                if isinstance(item, str)
            ]
            if isinstance(registry_raw, dict) and isinstance(registry_raw.get("registered_provider_ids"), list)
            else [],
            "enabled_models": enabled_models,
        },
        "control": {
            "disabled_providers": [
                str(item)[:128] for item in control_raw.get("disabled_providers", [])[:100]
                if isinstance(item, str)
            ] if isinstance(control_raw.get("disabled_providers"), list) else [],
            "disabled_models": [
                str(item)[:256] for item in control_raw.get("disabled_models", [])[:500]
                if isinstance(item, str)
            ] if isinstance(control_raw.get("disabled_models"), list) else [],
        },
        "statistics": {
            "date": statistics_raw.get("date"),
            "timezone": statistics_raw.get("timezone"),
            "summary": _clean_metrics(statistics_raw.get("summary")),
            "token_cache_rate": statistics_raw.get("token_cache_rate"),
            "rankings": {
                "providers": _ranking(rankings_raw.get("providers")),
                "models": _ranking(rankings_raw.get("models")),
                "gateway_keys": _ranking(rankings_raw.get("gateway_keys")),
            },
        },
        "logs": {
            "recent": _logs(logs_raw.get("recent")),
            "successful": _logs(logs_raw.get("successful")),
            "failed": _logs(logs_raw.get("failed")),
            "last_invocation": _clean_log(last_invocation)
            if isinstance(last_invocation, dict) else None,
        },
    }


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _integer_text(value: Any) -> str:
    return f"{int(_number(value)):,}"


def _percent(value: Any) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return "--"
    ratio = float(value)
    if 0 <= ratio <= 1:
        ratio *= 100
    return f"{ratio:.1f}%"


def public_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = snapshot.get("runtime") or {}
    version = snapshot.get("version") or {}
    statistics = snapshot.get("statistics") or {}
    summary = statistics.get("summary") or {}
    registry = snapshot.get("registry") or {}
    providers = registry.get("providers") or []
    failures = (snapshot.get("logs") or {}).get("failed") or []
    return {
        "generated_at": snapshot.get("generated_at"),
        "statistics_date": statistics.get("date"),
        "statistics_timezone": statistics.get("timezone"),
        "runtime_phase": runtime.get("phase"),
        "active_executions": runtime.get("active_executions", 0),
        "version_status": version.get("status"),
        "update_available": version.get("update_available"),
        "calls": summary.get("calls", 0),
        "successes": summary.get("successes", 0),
        "failures": summary.get("failures", 0),
        "success_rate": summary.get("success_rate"),
        "average_latency_ms": summary.get("average_latency_ms"),
        "cache_hit_rate": statistics.get("token_cache_rate"),
        "tokens": summary.get("tokens") or {},
        "providers_total": len(providers),
        "providers_enabled": sum(1 for item in providers if item.get("enabled") is True),
        "enabled_models": len(registry.get("enabled_models") or []),
        "recent_failure_codes": [item.get("error_code") for item in failures[:5] if item.get("error_code")],
    }


def _markdown(snapshot: dict[str, Any], *, collected_at: str) -> str:
    summary = public_summary(snapshot)
    tokens = summary["tokens"] if isinstance(summary["tokens"], dict) else {}
    version_text = str(summary.get("version_status") or "unknown")
    if summary.get("update_available") is True:
        version_text += "（发现更新）"
    failures = summary.get("recent_failure_codes") or []
    failure_text = "、".join(str(item) for item in failures) if failures else "无"
    return (
        "# Kemo 网关运行状态\n\n"
        f"> 最近采集：{collected_at} · 统计日：{summary.get('statistics_date') or '未知'} "
        f"({summary.get('statistics_timezone') or '未知时区'})\n\n"
        "## 运行概览\n\n"
        "| 项目 | 状态 |\n|---|---|\n"
        f"| 网关阶段 | {summary.get('runtime_phase') or 'unknown'} |\n"
        f"| 活动执行 | {_integer_text(summary.get('active_executions'))} |\n"
        f"| 版本检查 | {version_text} |\n"
        f"| Provider | {summary.get('providers_enabled', 0)} / {summary.get('providers_total', 0)} 已启用 |\n"
        f"| 可用模型 | {_integer_text(summary.get('enabled_models'))} |\n\n"
        "## 当日调用\n\n"
        "| 调用 | 成功 | 失败 | 成功率 | 平均延迟 | 缓存命中率 | 总 Token |\n"
        "|---:|---:|---:|---:|---:|---:|---:|\n"
        f"| {_integer_text(summary.get('calls'))} | {_integer_text(summary.get('successes'))} | "
        f"{_integer_text(summary.get('failures'))} | {_percent(summary.get('success_rate'))} | "
        f"{_integer_text(summary.get('average_latency_ms'))} ms | {_percent(summary.get('cache_hit_rate'))} | "
        f"{_integer_text(tokens.get('total_tokens'))} |\n\n"
        f"最近失败代码：{failure_text}\n\n"
        "## 本地资源\n\n"
        "- 脱敏快照：`data/gateway_status.json`\n"
        "- 状态图表：`artifacts/gateway_status.png`\n\n"
        "> 这里只保存白名单过滤后的只读状态，不包含 STATUS_TOKEN、模型调用密钥、Provider 密钥、请求正文或系统提示词。\n"
    )


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _render_chart(snapshot: dict[str, Any], target: Path) -> None:
    from PIL import Image, ImageDraw

    width, height = 1600, 900
    image = Image.new("RGB", (width, height), "#0b1020")
    draw = ImageDraw.Draw(image)
    title_font = _font(46, bold=True)
    heading_font = _font(25, bold=True)
    value_font = _font(34, bold=True)
    body_font = _font(20)
    small_font = _font(16)
    summary = public_summary(snapshot)
    stats = (snapshot.get("statistics") or {}).get("summary") or {}
    rankings = ((snapshot.get("statistics") or {}).get("rankings") or {}).get("providers") or []

    draw.text((64, 42), "Kemo Gateway Status", fill="#f7f8ff", font=title_font)
    subtitle = f"{summary.get('statistics_date') or '--'}  |  phase: {summary.get('runtime_phase') or 'unknown'}  |  generated: {snapshot.get('generated_at') or '--'}"
    draw.text((67, 104), subtitle, fill="#8e99b8", font=small_font)

    cards = [
        ("Calls", _integer_text(summary.get("calls")), "#7c6cff"),
        ("Success rate", _percent(summary.get("success_rate")), "#35d399"),
        ("Avg latency", f"{_integer_text(summary.get('average_latency_ms'))} ms", "#56b6ff"),
        ("Cache hit", _percent(summary.get("cache_hit_rate")), "#f4bd50"),
    ]
    card_y, card_w, gap = 155, 340, 28
    for index, (label, value, accent) in enumerate(cards):
        x = 64 + index * (card_w + gap)
        draw.rounded_rectangle((x, card_y, x + card_w, card_y + 145), radius=22, fill="#151c31", outline="#26304c", width=2)
        draw.rectangle((x, card_y, x + 8, card_y + 145), fill=accent)
        draw.text((x + 30, card_y + 25), label, fill="#98a4c3", font=body_font)
        draw.text((x + 30, card_y + 67), value, fill="#f7f8ff", font=value_font)

    left = (64, 338, 980, 832)
    right = (1012, 338, 1536, 832)
    draw.rounded_rectangle(left, radius=24, fill="#151c31", outline="#26304c", width=2)
    draw.rounded_rectangle(right, radius=24, fill="#151c31", outline="#26304c", width=2)
    draw.text((94, 365), "Provider calls", fill="#f7f8ff", font=heading_font)
    draw.text((1042, 365), "Token & runtime", fill="#f7f8ff", font=heading_font)

    rows = rankings[:7]
    max_calls = max((_number(item.get("calls")) for item in rows), default=1.0) or 1.0
    for index, item in enumerate(rows):
        y = 425 + index * 52
        label = str(item.get("id") or "unknown")[:32]
        calls = _number(item.get("calls"))
        draw.text((94, y), label, fill="#cbd3ea", font=body_font)
        draw.text((875, y), _integer_text(calls), fill="#cbd3ea", font=body_font, anchor="ra")
        draw.rounded_rectangle((390, y + 5, 835, y + 24), radius=9, fill="#222b47")
        draw.rounded_rectangle((390, y + 5, 390 + max(4, int(445 * calls / max_calls)), y + 24), radius=9, fill="#7c6cff")
    if not rows:
        draw.text((94, 440), "No provider calls for this date", fill="#7783a4", font=body_font)

    tokens = stats.get("tokens") if isinstance(stats.get("tokens"), dict) else {}
    details = [
        ("Input tokens", _integer_text(tokens.get("input_tokens"))),
        ("Cached input", _integer_text(tokens.get("cached_input_tokens"))),
        ("Output tokens", _integer_text(tokens.get("output_tokens"))),
        ("Reasoning", _integer_text(tokens.get("reasoning_tokens"))),
        ("Total tokens", _integer_text(tokens.get("total_tokens"))),
        ("Active executions", _integer_text(summary.get("active_executions"))),
        ("Enabled providers", f"{summary.get('providers_enabled', 0)} / {summary.get('providers_total', 0)}"),
        ("Enabled models", _integer_text(summary.get("enabled_models"))),
    ]
    for index, (label, value) in enumerate(details):
        y = 425 + index * 47
        draw.text((1042, y), label, fill="#98a4c3", font=body_font)
        draw.text((1502, y), value, fill="#f7f8ff", font=body_font, anchor="ra")
        draw.line((1042, y + 34, 1502, y + 34), fill="#222b47", width=1)

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        image.save(temporary, format="PNG", optimize=True)
        with temporary.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _update_manifest(*, active: bool, healthy: bool, update_time: str) -> None:
    payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    payload["open_input"] = active
    payload["input_health"] = "正常" if healthy else "异常"
    if healthy:
        payload["recent_update"] = update_time
    _atomic_json(MANIFEST_PATH, payload)


def _inactive_output(update_time: str) -> None:
    _atomic_text(
        INPUT_PATH,
        "# Kemo 网关运行状态\n\n"
        f"> 最近检查：{update_time}\n\n"
        "当前未激活。只有用户明确要求激活并配置网关地址与独立 `STATUS_TOKEN` 后才会采集。\n",
    )
    _update_manifest(active=False, healthy=True, update_time=update_time)


def _resources() -> list[dict[str, str]]:
    return [
        {"path": "data/gateway_status.json", "kind": "data", "label": "Kemo 网关脱敏状态快照"},
        {"path": "artifacts/gateway_status.png", "kind": "image", "label": "Kemo 网关状态图表"},
    ]


def update_snapshot(*, target_date: str | None = None) -> dict[str, Any]:
    update_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try:
        config = load_config()
        if config is None:
            _inactive_output(update_time)
            result = {"ok": True, "status": "inactive", "time": update_time, "active": False, "resources": []}
            _atomic_json(LAST_RUN_PATH, result)
            return result
        snapshot = fetch_status(config, target_date=target_date)
        _atomic_json(DATA_PATH, snapshot)
        _render_chart(snapshot, CHART_PATH)
        _atomic_text(INPUT_PATH, _markdown(snapshot, collected_at=update_time))
        _update_manifest(active=True, healthy=True, update_time=update_time)
        result = {
            "ok": True,
            "status": "active",
            "time": update_time,
            "active": True,
            "summary": public_summary(snapshot),
            "resources": _resources(),
        }
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        try:
            _update_manifest(active=CONFIG_PATH.is_file(), healthy=False, update_time=update_time)
        except Exception:
            pass
        result = {"ok": False, "status": "failed", "time": update_time, "error": error}
    _atomic_json(LAST_RUN_PATH, result)
    return result


def activate(params: dict[str, Any]) -> dict[str, Any]:
    candidate = config_from_mapping(params)
    # Validate and render before replacing a previously working configuration.
    snapshot = fetch_status(candidate, target_date=str(params.get("date") or "").strip() or None)
    update_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    _atomic_json(DATA_PATH, snapshot)
    _render_chart(snapshot, CHART_PATH)
    _atomic_text(INPUT_PATH, _markdown(snapshot, collected_at=update_time))
    _atomic_json(CONFIG_PATH, _config_payload(candidate), sensitive=True)
    _update_manifest(active=True, healthy=True, update_time=update_time)
    result = {
        "ok": True,
        "status": "active",
        "state_changed": True,
        "base_url": candidate.base_url,
        "status_token_configured": True,
        "summary": public_summary(snapshot),
        "artifacts": [{"path": "artifacts/gateway_status.png", "kind": "image", "name": "kemo-gateway-status.png"}],
    }
    _atomic_json(LAST_RUN_PATH, {**result, "artifacts": [{"path": "artifacts/gateway_status.png", "kind": "image"}]})
    return result


def deactivate() -> dict[str, Any]:
    for path in (CONFIG_PATH, DATA_PATH, CHART_PATH):
        path.unlink(missing_ok=True)
    update_time = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    _inactive_output(update_time)
    result = {"ok": True, "status": "inactive", "active": False, "state_changed": True}
    _atomic_json(LAST_RUN_PATH, result)
    return result
