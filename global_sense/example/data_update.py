#!/usr/bin/env python3
"""数据更新入口 — 运行此文件即刷新 sense.md 的内容。

调用方式:
    python data_update.py

该脚本负责从各数据源采集实时信息，将结果写入 sense.md。
运行后系统下次组装 prompt 时会自动读取 sense.md 的更新内容。
"""

import json
from datetime import datetime
from pathlib import Path


def collect_data() -> dict:
    """采集数据（对接硬件/API/系统接口等）"""
    # ↓ 这里对接实际的传感器、系统监控、外部 API 等
    return {
        "cpu": {"usage": 23, "temp": 52, "load": [1.2, 0.8, 0.6]},
        "memory": {"total_gb": 32.0, "used_gb": 18.7},
        "disk": {"total_gb": 512, "used_gb": 296},
        "network": {"ip": "192.168.1.100", "rx_mbps": 12.3, "tx_mbps": 3.1},
    }


def render_markdown(data: dict, status: str = "正常") -> str:
    """将数据渲染为 markdown"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cpu = data["cpu"]
    mem = data["memory"]
    disk = data["disk"]
    net = data["network"]
    return f"""# 系统资源感知

> 最后更新: {now}
> 状态: {status}

## CPU
- 使用率: {cpu['usage']}%
- 温度: {cpu['temp']}°C
- 负载(1min/5min/15min): {cpu['load'][0]} / {cpu['load'][1]} / {cpu['load'][2]}

## 内存
- 总量: {mem['total_gb']:.1f} GB
- 已用: {mem['used_gb']:.1f} GB
- 可用: {mem['total_gb'] - mem['used_gb']:.1f} GB

## 磁盘 (C:)
- 总量: {disk['total_gb']} GB
- 已用: {disk['used_gb']} GB ({disk['used_gb'] * 100 // disk['total_gb']}%)
- 可用: {disk['total_gb'] - disk['used_gb']} GB

## 网络
- 主要接口: eth0
- IP: {net['ip']}
- 上下行速率: {net['rx_mbps']} Mbps / {net['tx_mbps']} Mbps
"""


def update() -> None:
    base = Path(__file__).resolve().parent
    data = collect_data()
    md_content = render_markdown(data)
    (base / "sense.md").write_text(md_content, encoding="utf-8")
    # 同步更新 sense.json 的时间戳
    json_path = base / "sense.json"
    meta = json.loads(json_path.read_text(encoding="utf-8"))
    meta["recent_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["health"] = "正常"
    json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[感知模块 example] 更新完成 → {md_content}")


if __name__ == "__main__":
    update()
