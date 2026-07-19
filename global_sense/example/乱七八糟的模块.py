#!/usr/bin/env python3
"""此模块只负责数据采集，不参与 prompt 注入，与智能体无关。

它可以调用系统命令、硬件驱动、第三方 API 等乱七八糟的途径来采集数据。
data_update.py 会导入这里的函数来组装完整的数据。
"""

import os
import subprocess


def get_cpu_usage() -> float:
    """获取 CPU 使用率（示例桩）"""
    # 实际可能是读取 /proc/stat 或 wmic
    return 23.0


def get_memory_info() -> dict:
    """获取内存信息（示例桩）"""
    return {"total_gb": 32.0, "used_gb": 18.7}


def get_disk_info() -> dict:
    """获取磁盘信息（示例桩）"""
    return {"total_gb": 512, "used_gb": 296}


def get_network_info() -> dict:
    """获取网络信息（示例桩）"""
    return {"ip": "192.168.1.100", "rx_mbps": 12.3, "tx_mbps": 3.1}


def check_health() -> str:
    """健康检查：正常情况下返回"正常""""
    # 此处可以判断温度是否过高、磁盘是否将满等
    return "正常"
