---
type: component
project: kemo-agent
domain: cron
module: cron-schedule
layer: L2
scope: project
status: active
summary: cron/schedule.py — 确定性时间计算
source: "cron/cron-schedule.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, cron, 时间计算, 时区, 确定性]
---
# cron/schedule.py — 确定性时间计算

`E:\code\kemo-agent\cron\schedule.py`

## 概览

纯 Python 计算，**无 LLM 参与**。使用 `zoneinfo` 进行时区转换，全部时间以 UTC ISO 存储。

## 函数

### compute_next_run

```python
def compute_next_run(schedule: dict, *, after: datetime | None = None) -> str
```

根据 schedule 类型计算下一次执行时间（UTC ISO）：

| 类型 | 逻辑 |
|------|------|
| `once` | 直接返回 `start_at`（UTC ISO） |
| `daily` | 将 after 转到目标时区 → 取当天 HH:MM → 若已过则 +1 天 → 转 UTC |
| `recurring` | `after + interval_seconds`（≥60） |

### is_due

```python
def is_due(next_run_at: str, *, now: datetime | None = None) -> bool
```

`next_run_at <= now` 判断。

### _parse_utc / _to_utc_iso / _get_timezone

内部辅助函数。
