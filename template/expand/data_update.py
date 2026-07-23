"""
拓展模块数据采集模板。

== 用途 ==
复制到 */expand/<name>/data_update.py，由 cron 定时调用或手动执行。
RuntimeHost 会在隔离子进程中调用零参数 update()；Windows 后台调用由框架
隐藏终端，模块不需要设置 CREATE_NO_WINDOW，也不要自行启动常驻进程。

== 与 sense/data_update.py 的区别 ==
- 输出文件是 input_data.md（而非 sense.md）
- expand 有操控层（start_expand.py），sense 只有感知层

== 修 改 指 南 ==
1. 修改 collect()：实现实际的数据采集，返回 dict
2. 修改标题：update() 中的 "# 拓展模块名称" 改为模块实际名称
3. 不需要修改 update() 的格式化和写文件逻辑
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
INPUT_MD = BASE_DIR / "input_data.md"
STATUS_PATH = BASE_DIR / "_last_run.json"
MANIFEST_PATH = BASE_DIR / "expand.json"
HOST_TZ = timezone(timedelta(hours=8))  # 北京时间，按需修改


def collect() -> dict:
    """
    【必须修改】采集拓展数据，返回 dict。

    示例返回值:
        {"volume": 54, "muted": false}
    """
    data: dict = {}
    # TODO: 在此实现实际采集逻辑
    return data


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest_health(*, healthy: bool, update_time: str) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text("utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("expand.json 顶层必须是 JSON 对象")
    manifest["input_health"] = "正常" if healthy else "异常"
    if healthy:
        manifest["recent_update"] = update_time
    atomic_write(
        MANIFEST_PATH,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )


def update():
    """入口：采集 → 写入 → 更新健康状态 → 打印结果。"""
    now = datetime.now(HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        data = collect()

        # ── 修改此处标题为模块实际名称 ──
        content = f"""# 拓展模块名称

> 自动采集时间：{now}

## 数据

```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```
"""
        atomic_write(INPUT_MD, content)
        write_manifest_health(healthy=True, update_time=now)
        result = {
            "ok": True,
            "time": now,
            "errors": [],
            "size": len(content),
        }
    except Exception as exc:
        error = str(exc)
        try:
            write_manifest_health(healthy=False, update_time=now)
        except Exception as health_exc:
            error = f"{error}；写回异常健康状态失败：{health_exc}"
        result = {"ok": False, "time": now, "error": error}
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
