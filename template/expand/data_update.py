"""
拓展模块数据采集模板。

== 用途 ==
复制到 */expand/<name>/data_update.py，由 cron 定时调用或手动执行。
此模板不能直接运行——collect() 需要实现实际采集逻辑。

== 与 sense/data_update.py 的区别 ==
- 输出文件是 input_data.md（而非 sense.md）
- expand 有操控层（start_expand.py），sense 只有感知层

== 修 改 指 南 ==
1. 修改 collect()：实现实际的数据采集，返回 dict
2. 修改标题：main() 中的 "# 拓展模块名称" 改为模块实际名称
3. 不需要修改 main() 的格式化和写文件逻辑
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_MD = os.path.join(BASE_DIR, "input_data.md")
STATUS_PATH = os.path.join(BASE_DIR, "_last_run.json")
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


def main():
    """入口：采集 → 写入 → 打印状态（不需要修改）"""
    now = datetime.now(HOST_TZ).strftime("%Y-%m-%d %H:%M:%S")
    data = collect()

    # ── 修改此处标题为模块实际名称 ──
    content = f"""# 拓展模块名称

> 自动采集时间：{now}

## 数据

```json
{json.dumps(data, ensure_ascii=False, indent=2)}
```
"""

    with open(INPUT_MD, "w", encoding="utf-8") as f:
        f.write(content)

    result = {
        "ok": True,
        "time": now,
        "errors": [],
        "size": len(content),
    }
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    try:
        print(json.dumps(result))
        sys.stdout.flush()
    except Exception:
        pass

    return result


if __name__ == "__main__":
    result = main()
    sys.exit(0 if result.get("ok") else 1)
