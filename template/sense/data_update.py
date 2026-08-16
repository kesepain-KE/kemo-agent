"""感知模块数据采集模板。

这是可替换的最小适配样例，不是内部架构要求。可以只实现一个极小采集，
也可以重写整个文件或从模块目录内的完整工程导入实现；框架只调用零参数
``update()``（兼容 ``main()``），不会限制目录中的其他文件。
RuntimeHost 会按配置周期在隔离子进程中调用零参数 ``update()``；Windows
后台调用由框架隐藏终端，模块不需要设置 ``CREATE_NO_WINDOW`` 或启动守护进程。

输出：
- ``sense.md``：进入 System Prompt 的最新感知数据；
- ``sense.json``：最近成功时间与健康状态；
- ``_last_run.json``：最近一次执行结果。
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
SENSE_MD_PATH = BASE_DIR / "sense.md"
STATUS_PATH = BASE_DIR / "_last_run.json"
MANIFEST_PATH = BASE_DIR / "sense.json"
HOST_TZ = timezone(timedelta(hours=8))
PERSISTENCE_CHECKPOINT_SECONDS = 300


def collect() -> dict[str, Any]:
    """可选样例钩子；也可以删除此函数并让 update() 调用其他内部实现。"""

    # TODO: 直接实现极小采集，或导入模块目录内的任意内部工程。
    return {}


def atomic_write(path: Path, content: str) -> bool:
    """在同一目录原子替换文本文件，避免 Prompt 读取到半份内容。"""

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


def render_markdown(data: dict[str, Any], update_time: str) -> str:
    """可选样例：产生有界 Prompt 数据出口；允许完全替换。"""

    rendered = (
        f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
        if data
        else "暂无数据"
    )
    del update_time
    return f"""# 感知模块名称

## 数据

{rendered}
"""


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
    manifest = json.loads(MANIFEST_PATH.read_text("utf-8-sig"))
    if not isinstance(manifest, dict):
        raise ValueError("sense.json 顶层必须是 JSON 对象")
    manifest["health"] = "正常" if healthy else "异常"
    if healthy:
        manifest["recent_update"] = monotonic_timestamp(
            manifest.get("recent_update"), update_time
        )
    atomic_write(
        MANIFEST_PATH,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )


def update() -> dict[str, Any]:
    """零参数更新入口：采集、写入并返回结构化执行状态。"""

    current = datetime.now(HOST_TZ)
    now = current.strftime("%Y-%m-%d %H:%M:%S")
    checkpoint = checkpoint_time(current)
    try:
        data = collect()
        content = render_markdown(data, now)
        changed = atomic_write(SENSE_MD_PATH, content)
        write_manifest_health(healthy=True, update_time=now if changed else checkpoint)
        result: dict[str, Any] = {
            "ok": True,
            "status": "ok",
            "time": now,
            "changed": changed,
            "errors": [],
            "size": len(content),
        }
    except Exception as exc:
        error = str(exc) or type(exc).__name__
        try:
            write_manifest_health(healthy=False, update_time=now)
        except Exception as health_exc:
            error = f"{error}；写回异常健康状态失败：{health_exc}"
        result = {"ok": False, "status": "error", "time": now, "error": error}

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
