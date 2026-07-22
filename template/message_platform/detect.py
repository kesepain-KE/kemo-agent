"""{{PLATFORM}} 健康检测适配器。"""

from __future__ import annotations

from typing import Any


def check(
    raw_config: dict[str, Any],
    current_state: dict[str, Any],
) -> dict[str, Any]:
    """检测平台连接健康状态。

    health 可取 unknown / healthy / degraded / dead。
    """
    return {**current_state, "health": "unknown"}
