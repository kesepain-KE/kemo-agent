"""Chat 模型发现：只用服务端已保存配置，不调用生成接口或向浏览器暴露凭据。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from web.errors import ProviderAccessError, ProviderDiscoveryError


class _NoModelRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 模型端点不应重定向；尤其不能把 Authorization 转发给其他站点。
        return None


def discover_chat_models(config: dict[str, Any]) -> list[dict[str, str]]:
    base_url = str(config.get("base_url") or "").rstrip("/")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ProviderDiscoveryError("Chat Provider 地址无效，请检查已保存配置")
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={
            "Authorization": f"Bearer {config.get('api_key') or ''}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        timeout = min(20.0, max(1.0, float(config.get("timeout", 120))))
        opener = urllib.request.build_opener(_NoModelRedirect())
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("model response too large")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("expected OpenAI-compatible data list")
        models: dict[str, dict[str, str]] = {}
        for item in payload["data"]:
            if not isinstance(item, dict):
                raise ValueError("invalid model entry")
            model_id = item.get("id")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 256:
                raise ValueError("invalid model id")
            if any(ord(char) < 32 for char in model_id):
                raise ValueError("invalid model id")
            models[model_id] = {
                "id": model_id,
                "object": "model",
                "owned_by": (checked_55 if isinstance(checked_55 := item.get("owned_by"), str) else ""),
            }
        return list(models.values())
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ProviderAccessError("Chat API 密钥无效或没有获取模型列表的权限", status=exc.code) from exc
        raise ProviderDiscoveryError("Chat 模型列表获取失败，请检查 Provider 的 /models 接口") from exc
    except (OSError, ValueError, TypeError) as exc:
        # 不转发上游正文、异常详情或密钥到客户端。
        raise ProviderDiscoveryError("Chat 模型列表获取失败，请检查 Provider 配置后重试") from exc
