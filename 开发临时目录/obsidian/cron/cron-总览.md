---
type: domain_overview
project: kemo-agent
domain: cron
module: cron-总览
layer: L1
status: active
summary: cron — 定时任务调度核心（总览）
source: "cron/cron-总览.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, cron, 定时任务, 调度, 总览, review_due]
---updated: 2026-07-18
verified: partial
tags: [kemo-agent, cron, 定时任务, 调度, 总览]
---
# cron — 定时任务调度核心（总览）

## 四层架构

| 层 | 文件 | 职责 |
|------|------|------|
| 存储 | `run/cron_store.py` | CronStore 原子读写，校验，崩溃恢复 |
| 时间 | `cron/schedule.py` | 确定性 next_run_at 计算（无 LLM） |
| 执行 | `cron/executor.py` | 原子领取 → handle_request → 持久化结果（重构：合并执行状态机） |
| 调度 | `cron/scheduler.py` | CronScheduler 守护线程，扫描到期任务（重构：统一 recover_all 入口） |

## 新增模块

| 文件 | 职责 |
|------|------|
| `cron/review_due.py` | **新增** — 重要临时记忆定期审阅。后台维护线程的 review_due 循环，同步触发 memory_temporary_important 子代理执行记忆审阅 |

## 生成

| 文件 | 职责 |
|------|------|
| `cron/service.py` | 通过 time_plan 子代理生成/编辑定时任务草案（重构：精简接口） |

## 源文件导航

- [[cron-schedule|schedule.py]] — compute_next_run / is_due
- [[cron-executor|executor.py]] — execute_cron_task（原子领取+执行+持久化）
- [[cron-scheduler|scheduler.py]] — CronScheduler / recover_all
- [[cron-service|service.py]] — generate_cron_task / edit_cron_task
- **cron-review_due**（新增） — review_due 循环

## 存储

- [[run-cron_store|cron_store.py]] — CronStore（per-user 原子存储，重构：简化状态机）

## 子代理

- time_plan v2.0.0（严格 Schema，4 模式，新增 trigger.md 注册系统）

## 调用链

```
CronScheduler._scan_user()
  → cron/schedule.is_due(next_run_at) → True
  → cron/executor.execute_cron_task()
    → CronStore.update(claim: enabled→running)
    → run/engine.handle_request(prompt)
    → CronStore.update(persist result + next_run_at)
  → on_task_executed 回调

不经过 cli.py
```

## 原理引用

- 运行原理
- 计划任务
- 工具调用
