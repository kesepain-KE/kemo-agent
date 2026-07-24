---
type: component
project: kemo-agent
domain: cron
module: cron-service
layer: L2
scope: project
status: active
summary: cron/service.py — 定时任务生成服务
source: "cron/cron-service.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, cron, 生成服务, time_plan]
---
# cron/service.py — 定时任务生成服务

`E:\code\kemo-agent\cron\service.py`

## 概览

通过 `time_plan` 子代理将自然语言转化为结构化定时任务草案，再由确定性 schedule 计算 `next_run_at`。

## 类

### CronGenerationError

`CronGenerationError(RuntimeError)`

### CronSkipped

`CronSkipped(CronGenerationError)` — 子代理判定不需要创建定时任务。

## 函数

### generate_cron_task

```python
def generate_cron_task(
    *, root, user, user_request, source="cli",
    config=None, provider_factory, tool_registry=None,
    existing_task=None, edit_request=None,
) -> dict
```

**流程**：
1. 通过 AgentRunner 调用 `time_plan` 子代理
2. 子代理返回 action/schedule/title/prompt
3. `compute_next_run(schedule)` 确定性计算
4. `normalize_task()` 校验并返回

### edit_cron_task

```python
def edit_cron_task(*, root, user, task, edit_request, ...) -> dict
```

编辑已有定时任务。

### _current_utc

```python
def _current_utc() -> str
```

当前 UTC ISO。
