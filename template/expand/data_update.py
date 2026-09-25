"""
拓展模块数据采集模板。

== 用途 ==
复制到 */expand/<name>/data_update.py，由 cron 定时调用或手动执行。
RuntimeHost 会在隔离子进程中调用零参数 update()；Windows 后台调用由框架
隐藏终端，模块不需要设置 CREATE_NO_WINDOW，也不要自行启动常驻进程。

== 与 sense/data_update.py 的区别 ==
- 输出文件是 input_data.md（而非 sense.md）
- expand 有操控层（start_expand.py），sense 只有感知层

== 组件面板 ==
- module/panel.values.json：Web 用户配置值，由本模块显式读取，不会自动进入 Prompt
- module/status.json：脱敏运行状态，只供 Web 展示

== 自由实现 ==
本文件只是可替换的最小适配样例。可以保留这些辅助函数、缩成更小入口、
重写整个文件，或调用模块目录内的完整工程。框架只要求零参数 update()
（兼容 main()）最终刷新清单声明的数据出口并返回明确状态，不规定内部结构。
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
INPUT_MD = BASE_DIR / "input_data.md"
STATUS_PATH = BASE_DIR / "_last_run.json"
MANIFEST_PATH = BASE_DIR / "expand.json"
PANEL_VALUES_PATH = BASE_DIR / "module" / "panel.values.json"
PANEL_STATUS_PATH = BASE_DIR / "module" / "status.json"
HOST_TZ = timezone(timedelta(hours=8))  # 北京时间，按需修改
PERSISTENCE_CHECKPOINT_SECONDS = 300


def load_panel_values() -> dict[str, Any]:
    """读取用户配置组件值；这些值不会被框架自动写进 Prompt。"""

    try:
        value = json.loads(PANEL_VALUES_PATH.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def collect(config: dict[str, Any]) -> Any:
    """
    可选样例钩子；也可以删除并让 update() 调用其他内部实现。

    ``config`` 来自组件值文件；只使用真实需要的字段，密钥不得写入返回值或日志。
    可以返回任意 JSON 兼容值用于生成 Prompt 摘要；大型内容保存在模块
    目录内由实际工程自行选择的位置。
    """
    del config
    data: Any = {}
    # TODO: 直接实现极小采集，或导入模块目录内的任意内部工程。
    return data


def render_markdown(data: Any, *, update_time: str) -> str:
    """可选样例：产生小型 Prompt 视图；允许完全替换。"""

    if isinstance(data, str):
        rendered = data.strip() or "暂无数据"
    else:
        rendered = f"```json\n{json.dumps(data, ensure_ascii=False, indent=2, default=str)}\n```"
    del update_time
    return f"# 拓展模块名称\n\n## 数据\n\n{rendered}\n"


def collect_resources(data: Any) -> list[dict[str, str]]:
    """可选样例：声明模块内资源路径，不限制实际保存位置。"""

    del data
    return []


def atomic_write(path: Path, content: str) -> bool:
    try:
        if path.is_file() and path.read_text("utf-8") == content:
            return False
    except (OSError, UnicodeError):
        pass
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        return True
    finally:
        temporary.unlink(missing_ok=True)


def checkpoint_time(now: datetime) -> str:
    second = int(now.timestamp())
    bucket = second - second % PERSISTENCE_CHECKPOINT_SECONDS
    return datetime.fromtimestamp(bucket, tz=HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")


def monotonic_timestamp(previous: Any, candidate: str) -> str:
    previous_text = str(previous or "").strip()
    if not previous_text:
        return candidate
    try:
        previous_value = datetime.strptime(previous_text, "%Y-%m-%d %H:%M:%S")
        candidate_value = datetime.strptime(candidate, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return candidate
    return previous_text if previous_value >= candidate_value else candidate


def write_manifest_health(*, healthy: bool, update_time: str) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("expand.json 顶层必须是 JSON 对象")
    manifest["input_health"] = "正常" if healthy else "异常"
    if healthy:
        manifest["recent_update"] = monotonic_timestamp(
            manifest.get("recent_update"), update_time
        )
    atomic_write(
        MANIFEST_PATH,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )


def write_panel_status(result: dict[str, Any], config: dict[str, Any]) -> None:
    """只写 Web 用户配置页状态，不写入 Prompt 数据出口。"""

    ok = bool(result.get("ok"))
    enabled = bool(config.get("enabled", True))
    endpoint = str(config.get("endpoint") or "").strip()
    mode = str(config.get("mode") or "get").strip().upper()
    timeout = config.get("interval", 10)
    if ok:
        summary = (
            f"采集已完成；组件配置为{'启用' if enabled else '停用'}，"
            f"目标{'已填写' if endpoint else '未填写'}，模式 {mode}，超时 {timeout} 秒。"
        )
    else:
        summary = "采集失败；请查看模块运行诊断。组件状态不会把密钥或原始错误回显到浏览器。"
    atomic_write(
        PANEL_STATUS_PATH,
        json.dumps(
            {
                "phase": "已完成" if ok else "失败",
                "last_run": str(result.get("time") or ""),
                "summary": summary,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
    )


def update():
    """入口：采集 → 写入 → 更新健康状态 → 打印结果。"""
    current = datetime.now(HOST_TZ)
    now = current.strftime("%Y-%m-%d %H:%M:%S")
    checkpoint = checkpoint_time(current)
    panel_values = load_panel_values()
    try:
        data = collect(panel_values)

        content = render_markdown(data, update_time=now)
        changed = atomic_write(INPUT_MD, content)
        write_manifest_health(healthy=True, update_time=now if changed else checkpoint)
        result = {
            "ok": True,
            "time": now,
            "changed": changed,
            "errors": [],
            "size": len(content),
            "resources": collect_resources(data),
        }
    except Exception as exc:
        error = str(exc)
        try:
            write_manifest_health(healthy=False, update_time=now)
        except Exception as health_exc:
            error = f"{error}；写回异常健康状态失败：{health_exc}"
        result = {"ok": False, "time": now, "error": error}
    try:
        write_panel_status(result, panel_values)
    except Exception:
        pass
    atomic_write(
        STATUS_PATH,
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    )

    try:
        print(json.dumps(result))
        sys.stdout.flush()
    except Exception:
        pass

    return result


if __name__ == "__main__":
    result = update()
    sys.exit(0 if result.get("ok") else 1)
