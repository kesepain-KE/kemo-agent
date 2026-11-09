"""感知模块：可切换时区的时间观测。

采集本机系统时钟，按组件面板选定的时区换算，输出有界的时间观测快照，
不依赖运行环境时区，不产生任何外部副作用。

观测时区来自 ``module/panel.values.json``（由 Web 感知页的组件面板写入）；
缺失、损坏或不可用时回退到 ``Asia/Shanghai``，并在出口与状态里说明回退原因。

框架调用零参数 ``update()``（兼容 ``main()``）：
- 原子重写 ``sense.md``（进入 System Prompt 的观测出口）；
- 写回 ``module.yaml`` 的 ``recent_update`` 与 ``health``；
- 写 ``module/status.json`` 供组件面板只读展示；
- 记录 ``_last_run.json`` 便于排查。
"""

from __future__ import annotations

import json
import yaml
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent
SENSE_MD_PATH = BASE_DIR / "sense.md"
MANIFEST_PATH = BASE_DIR / "module.yaml"
STATUS_PATH = BASE_DIR / "_last_run.json"
PANEL_VALUES_PATH = BASE_DIR / "module" / "panel.values.json"
MODULE_STATUS_PATH = BASE_DIR / "module" / "status.json"

DEFAULT_TIMEZONE = "Asia/Shanghai"
BEIJING_TZ = timezone(timedelta(hours=8))
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
MINUTE_FORMAT = "%Y-%m-%d %H:%M"
CHECKPOINT_SECONDS = 300
WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def atomic_write(path: Path, content: str) -> bool:
    """在同一目录原子替换文本文件，避免 Prompt 读到半份内容。"""

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


def load_timezone_name() -> str:
    """读取组件面板保存的时区；文件缺失、损坏或为空时回退默认时区。"""

    try:
        stored = json.loads(PANEL_VALUES_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return DEFAULT_TIMEZONE
    if not isinstance(stored, dict):
        return DEFAULT_TIMEZONE
    value = stored.get("timezone")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DEFAULT_TIMEZONE


def resolve_timezone(name: str) -> tuple[Any, str, str]:
    """解析时区名，返回 ``(tzinfo, 生效时区名, 回退说明)``。

    回退必须显式记录：配置里写了查不到的时区名时，出口要说明实际生效的时区，
    不能让观测值看起来仍来自用户配置。
    """

    try:
        return ZoneInfo(name), name, ""
    except Exception as exc:  # 时区库缺失或名称非法时走回退
        reason = f"配置时区 {name!r} 不可用（{type(exc).__name__}）"
    try:
        return (
            ZoneInfo(DEFAULT_TIMEZONE),
            DEFAULT_TIMEZONE,
            f"{reason}，已回退到 {DEFAULT_TIMEZONE}",
        )
    except Exception:  # 连默认时区都查不到时用固定偏移兜底
        return (
            BEIJING_TZ,
            "UTC+08:00（固定偏移）",
            f"{reason}，已回退到 UTC+08:00 固定偏移",
        )


def collect(tzinfo: Any) -> dict[str, str]:
    """读取本机时钟，换算为观测时区时间并附参照时区时间。"""

    now = datetime.now(tzinfo)
    utc = now.astimezone(timezone.utc)
    beijing = now.astimezone(BEIJING_TZ)
    iso_year, iso_week, _ = now.isocalendar()
    offset = now.utcoffset() or timedelta(0)
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours, minutes = divmod(abs(total_minutes), 60)
    return {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "weekday": WEEKDAY_NAMES[now.weekday()],
        "iso_week": f"{iso_year}-W{iso_week:02d}",
        "offset": f"{sign}{hours:02d}:{minutes:02d}",
        "beijing": beijing.strftime(MINUTE_FORMAT),
        "utc": utc.strftime(MINUTE_FORMAT),
    }


def render_markdown(
    data: dict[str, str],
    tz_name: str,
    snapshot_at: str,
    fallback_note: str = "",
) -> str:
    """生成有界的 Prompt 数据出口。

    快照只取分钟精度：全局感知刷新间隔只有几秒，把秒级当前时间写进出口会让内容
    每轮都变，收益有限却增加无谓写盘，也不利于 Prompt 前缀稳定。
    """

    lines = [
        "# 时间观测",
        "",
        "> 数据来源：本机系统时钟，按时区数据库换算，不依赖运行环境时区。",
        f"> 观测时区：{tz_name}（UTC{data['offset']}）",
        f"> 采集快照：{snapshot_at}（观测时区时间，分钟精度）",
        "> 刷新周期由框架全局感知调度决定；本出口的时间只保留到分钟。",
    ]
    if fallback_note:
        lines.append(f"> 配置回退：{fallback_note}")
    lines.extend(
        [
            "",
            "| 项目 | 值 |",
            "|------|-----|",
            f"| 当地日期 | {data['date']} |",
            f"| 当地时间 | {data['time']} |",
            f"| 星期 | {data['weekday']} |",
            f"| ISO 周 | {data['iso_week']} |",
            f"| 北京时间 | {data['beijing']} |",
            f"| UTC 时间 | {data['utc']} |",
            "",
            "以上为最近一次采集快照，时间精度为分钟。",
        ]
    )
    return "\n".join(lines) + "\n"


def checkpoint_time(now: datetime) -> str:
    second = int(now.timestamp())
    bucket = second - second % CHECKPOINT_SECONDS
    return datetime.fromtimestamp(bucket, tz=BEIJING_TZ).strftime(TIMESTAMP_FORMAT)


def monotonic_timestamp(previous: Any, candidate: str) -> str:
    previous_text = str(previous or "").strip()
    if not previous_text:
        return candidate
    try:
        previous_value = datetime.strptime(previous_text, TIMESTAMP_FORMAT)
        candidate_value = datetime.strptime(candidate, TIMESTAMP_FORMAT)
    except ValueError:
        return candidate
    return previous_text if previous_value >= candidate_value else candidate


def write_manifest_health(*, healthy: bool, update_time: str) -> None:
    manifest = yaml.safe_load(MANIFEST_PATH.read_text(encoding="utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("module.yaml 顶层必须是 YAML 映射")
    manifest["health"] = "正常" if healthy else "异常"
    if healthy:
        manifest["recent_update"] = monotonic_timestamp(
            manifest.get("recent_update"), update_time
        )
    atomic_write(
        MANIFEST_PATH,
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
    )


def write_module_status(
    *,
    healthy: bool,
    timezone_name: str,
    local: str,
    offset: str,
    run_at: str,
    summary: str,
) -> None:
    """为组件面板的 status 容器写只读状态；文件缺失时页面按暂无数据处理。"""

    payload = {
        "health": "正常" if healthy else "异常",
        "timezone": timezone_name,
        "local": local,
        "offset": f"UTC{offset}" if offset else "",
        "last": run_at,
        "summary": summary,
    }
    atomic_write(
        MODULE_STATUS_PATH,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def update() -> dict[str, Any]:
    """零参数更新入口：采集、写入并返回结构化执行状态。"""

    tzinfo, tz_name, fallback_note = resolve_timezone(load_timezone_name())
    current = datetime.now(tzinfo)
    snapshot = current.strftime(MINUTE_FORMAT)
    stamp_time = datetime.now(BEIJING_TZ)
    stamp = stamp_time.strftime(TIMESTAMP_FORMAT)
    checkpoint = checkpoint_time(stamp_time)
    try:
        data = collect(tzinfo)
        content = render_markdown(data, tz_name, snapshot, fallback_note)
        changed = atomic_write(SENSE_MD_PATH, content)
        write_manifest_health(healthy=True, update_time=stamp if changed else checkpoint)
        summary = (
            f"观测时区 {tz_name}（UTC{data['offset']}）；"
            f"最近快照 {snapshot}；时间精度分钟。"
        )
        if fallback_note:
            summary = f"{summary} {fallback_note}。"
        write_module_status(
            healthy=True,
            timezone_name=tz_name,
            local=snapshot,
            offset=data["offset"],
            run_at=stamp,
            summary=summary,
        )
        result: dict[str, Any] = {
            "ok": True,
            "status": "ok",
            "time": stamp,
            "timezone": tz_name,
            "changed": changed,
            "errors": [],
            "size": len(content),
        }
        if fallback_note:
            result["warning"] = fallback_note
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        try:
            write_manifest_health(healthy=False, update_time=stamp)
        except Exception as health_exc:
            error = f"{error}；写回异常健康状态失败：{health_exc}"
        try:
            write_module_status(
                healthy=False,
                timezone_name=tz_name,
                local="",
                offset="",
                run_at=stamp,
                summary=f"采集失败：{error}",
            )
        except Exception:
            pass
        result = {
            "ok": False,
            "status": "error",
            "time": stamp,
            "timezone": tz_name,
            "error": error,
        }

    atomic_write(
        STATUS_PATH,
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> dict[str, Any]:
    """兼容手动运行；调度器优先调用 ``update()``。"""

    return update()


if __name__ == "__main__":
    execution = main()
    sys.exit(0 if execution.get("ok") else 1)
