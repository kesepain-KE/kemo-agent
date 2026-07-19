#!/usr/bin/env python3
"""此模块只负责硬件通讯，不参与 prompt 注入，与智能体无关。

data_update.py 和 start_expand.py 会导入这里的函数来对接真实硬件。
"""

import subprocess
import time


def send_mqtt(topic: str, payload: str) -> bool:
    """通过 MQTT 发送指令到智能灯网关"""
    # 实际实现
    return True


def read_serial() -> dict:
    """从串口读取灯光状态"""
    # 实际对接串口硬件
    time.sleep(0.1)
    return {}


def zigbee_scan() -> list:
    """扫描 ZigBee 网络中的设备"""
    return []
