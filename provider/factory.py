"""供应商工厂。"""

from __future__ import annotations

from contextlib import contextmanager
import math
import threading
import time
from typing import Any, Iterator

from provider.adapters.base import ProviderAdapter
from provider.adapters.chat_bridge import ChatBridgeProvider
from provider.kemo_gateway import KemoGatewayProvider


class ProviderCongestionError(RuntimeError):
    """等待 Provider 全局并发槽位超时。"""


_provider_semaphore: threading.BoundedSemaphore | None = None
_provider_semaphore_limit = 0
_provider_waiting = 0
_provider_semaphore_lock = threading.RLock()


def get_provider_semaphore(max_concurrent: int = 10) -> threading.BoundedSemaphore:
    """获取可在空闲时重配置的进程级 Provider 信号量。"""

    if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int):
        raise ValueError("Provider 最大并发数必须是正整数")
    if max_concurrent < 1:
        raise ValueError("Provider 最大并发数必须至少为 1")
    global _provider_semaphore, _provider_semaphore_limit
    with _provider_semaphore_lock:
        if _provider_semaphore is None:
            _provider_semaphore = threading.BoundedSemaphore(max_concurrent)
            _provider_semaphore_limit = max_concurrent
        elif _provider_semaphore_limit != max_concurrent:
            available = int(getattr(_provider_semaphore, "_value", 0))
            active = max(0, _provider_semaphore_limit - available)
            if active == 0 and _provider_waiting == 0:
                _provider_semaphore = threading.BoundedSemaphore(max_concurrent)
                _provider_semaphore_limit = max_concurrent
        return _provider_semaphore


def provider_semaphore_status(config: dict[str, Any] | None = None) -> dict[str, int]:
    """返回 Provider 总闸状态；提供配置时会先初始化空闲总闸。"""

    if config is not None:
        max_concurrent, _ = _provider_runtime_limits(config)
        get_provider_semaphore(max_concurrent)

    with _provider_semaphore_lock:
        if _provider_semaphore is None:
            return {
                "active_requests": 0,
                "max_requests": 0,
                "available_requests": 0,
                "waiting_estimate": 0,
            }
        available = max(0, int(getattr(_provider_semaphore, "_value", 0)))
        return {
            "active_requests": max(0, _provider_semaphore_limit - available),
            "max_requests": _provider_semaphore_limit,
            "available_requests": available,
            "waiting_estimate": max(0, _provider_waiting),
        }


def _provider_runtime_limits(config: dict[str, Any]) -> tuple[int, float]:
    runtime = config.get("provider_runtime") or {}
    if not isinstance(runtime, dict):
        runtime = {}
    raw_max = runtime.get("max_concurrent_requests", 10)
    if isinstance(raw_max, bool):
        raw_max = 10
    try:
        max_concurrent = int(raw_max)
    except (TypeError, ValueError):
        max_concurrent = 10
    if max_concurrent < 1:
        max_concurrent = 10
    raw_timeout = runtime.get("request_semaphore_timeout", 300.0)
    if isinstance(raw_timeout, bool):
        raw_timeout = 300.0
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 300.0
    if not math.isfinite(timeout) or timeout < 1.0:
        timeout = 300.0
    return max_concurrent, timeout


@contextmanager
def provider_request_slot(
    config: dict[str, Any],
    *,
    cancel_event: threading.Event | None = None,
) -> Iterator[None]:
    """限制一次真实 Provider 请求；工具执行期间不占用槽位。"""

    max_concurrent, timeout = _provider_runtime_limits(config)
    semaphore = get_provider_semaphore(max_concurrent)
    acquired = semaphore.acquire(blocking=False)
    if not acquired:
        global _provider_waiting
        with _provider_semaphore_lock:
            _provider_waiting += 1
        deadline = time.monotonic() + timeout
        try:
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    raise ProviderCongestionError("等待 Provider 并发槽位时请求已取消")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderCongestionError(
                        f"Provider 并发已满（{max_concurrent}），等待 {timeout:g}s 超时"
                    )
                acquired = semaphore.acquire(timeout=min(0.1, remaining))
        finally:
            with _provider_semaphore_lock:
                _provider_waiting = max(0, _provider_waiting - 1)
    try:
        yield
    finally:
        if acquired:
            semaphore.release()


def create_provider(config: dict[str, Any]) -> ProviderAdapter:
    provider_type = str(config.get("type") or "").strip().lower()
    if provider_type == "chat":
        return ChatBridgeProvider(config=config)
    if provider_type == "kemo":
        return KemoGatewayProvider(config=config)
    raise ValueError(f"不支持的 provider.type：{provider_type!r}")
