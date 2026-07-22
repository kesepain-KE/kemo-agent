"""
感知模块数据采集模板。

== 用途 ==
复制到 global_sense/<name>/data_update.py，由 cron 定时调用或手动执行。
此模板不能直接运行——collect() 需要实现实际采集逻辑。

== 修 改 指 南 ==
1. 修改 collect()：实现实际的数据采集，返回 dict
2. 修改标题：update() 中的 "# 系统状态感知" 改为模块实际名称
3. 不需要修改 main() 和 update()：格式化和写文件逻辑已通用

== 输出文件 ==
- sense.md：Markdown 格式感知数据，注入系统提示词
- _last_run.json：运行状态（ok/time/errors/size）
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SENSE_MD_PATH = os.path.join(BASE_DIR, "sense.md")
STATUS_PATH = os.path.join(BASE_DIR, "_last_run.json")
HOST_TZ = timezone(timedelta(hours=8))  # 北京时间，按需修改


def collect() -> dict:
    """
    【必须修改】采集感知数据，返回 dict。

    示例返回值:
        {"cpu_usage": 45.2, "memory_free_gb": 12.3}
    """
    data: dict = {}
    # TODO: 在此实现实际采集逻辑
    return data


def update(data: dict) -> None:
    """将采集到的数据写入 sense.md（不需要修改）"""
    now = datetime.now(HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # ── 修改此处标题为模块实际名称 ──
    content = f"""# 感知模块名称

> 自动采集时间：{now}

## 数据

```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```
"""

    with open(SENSE_MD_PATH, "w", encoding="utf-8") as f:
        f.write(content)

    status = {
        "ok": True,
        "time": now,
        "errors": [],
        "sense_md_size": len(content),
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)


def main():
    """入口：采集 → 写入 → 打印状态（不需要修改）"""
    try:
        data = collect()
        update(data)
        print(json.dumps({"ok": True}))
        sys.stdout.flush()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        sys.stdout.flush()
        raise


if __name__ == "__main__":
    main()
