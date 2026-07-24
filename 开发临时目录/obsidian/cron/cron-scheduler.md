---
type: component
project: kemo-agent
domain: cron
module: cron-scheduler
layer: L2
scope: project
status: active
summary: cron/scheduler.py — 后台调度器（含系统任务独立目录 + 两层扫描）
source: "cron/cron-scheduler.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, cron, 调度器, 后台线程, 系统任务, 两层扫描, 执行日志]
---
# cron/scheduler.py — 后台调度器

`E:\code\kemo-agent\cron\scheduler.py`

## 职责

- 周期扫描系统 cron 任务（`cron/task_cron_system/`）
- 周期扫描用户 cron 任务（`users/<user>/task_cron/`）
- 检查到期条件
- 调用执行器
- 支持启动、停止与恢复
- 记录系统任务执行日志到 `cron/task_cron_system/log/`

## 关键变更（2026-07-22）

### 系统任务独立目录

旧版：记忆维护/晋升任务散落在每个用户 `users/<user>/task_cron/` 中，用 `system_key` 标识。

新版：系统任务统一存储在 `cron/task_cron_system/`，由 `CronStore(root, "__system__", system=True)` 管理。不再按用户隔离系统任务。

### system_key 退役

- `normalize_task()` 的 `system_key` 参数改为 `action: str | None`
- 系统任务用 `exec_mode="system"` + `action` 字段标识类型
- `cleanup_old_system_tasks(root, user)` 清理旧版散落在用户目录的 system_key 任务

### 系统任务规格

| task_id | action | type | 间隔 | 说明 |
|---------|--------|------|------|------|
| memory_periodic_scan | periodic_scan | recurring | config | 临时重要记忆定时巡检 |
| memory_daily_consolidate | daily_consolidate | daily | config | 临时重要记忆每日整理 |
| memory_promotion | memory_promotion | recurring | 30s | 记忆碎片到期晋升检查 |

### 两层扫描

```python
def scan_once(self):
    executed = 0
    # 1. 系统任务（对每个用户执行一次）
    executed += self._scan_system_tasks(now)
    # 2. 用户任务
    for user in list_users(self.root):
        executed += self._scan_user_tasks(user, now)
```

### ensure_memory_maintenance_tasks / ensure_memory_promotion_task

签名变更：不再需要 `user` 参数，任务创建在 `__system__` store 中。

## 系统任务执行日志（2026-07-21 新增）

系统任务执行后自动写入 JSONL 日志：

```python
def _append_system_execution(root, *, user, task_id, executed_at, duration_ms, result=None, error=None):
    """写一条有界、用户隔离的系统 cron 执行记录到 cron/task_cron_system/log/YYYY-MM-DD.jsonl"""
```

- 记录字段：schema_version, executed_at, user, task_id, status, duration_ms, result, error
- `_system_result_summary(result)` 从执行结果中提取 status/action/model/requested 等元数据和 promotions 列表
- 日志写入失败不影响调度器运行（catch OSError 后静默返回）
- 成功和失败均写入日志
- 日志路径：`cron/task_cron_system/log/YYYY-MM-DD.jsonl`（北京时间）
- Web API `GET /api/users/{user}/runtime/status` 读取这些日志展示系统任务执行记录

## 关键变更（2026-07-22 追加）

### 感知与拓展系统任务

新增两个全局系统任务，由 `ensure_perception_task` 和 `ensure_expand_task` 注册：

| task_id | action | type | 间隔 | 说明 |
|---------|--------|------|------|------|
| perception_update | perception_update | recurring | `task_cron_system.sense_update_rate`（默认 5s） | 全局感知模块数据采集 |
| expand_update | expand_update | recurring | `task_cron_system.expand_update_rate`（默认 5s） | 全局拓展模块数据采集 |

- `_GLOBAL_SYSTEM_ACTIONS = {"perception_update", "expand_update"}` 标识全局任务
- 全局任务每个到期周期只以 `__system__` 身份执行一次，不按用户遍历
- `_configured_update_rate(config, key)` 读取配置，非法或缺失时回退到 5

### Cron 反压退避

新增 `_should_backoff()` 方法，当 Provider 高负载时跳过本轮普通任务：

- `cron.avoid_congestion=true`（默认）时启用
- `cron.congestion_threshold_ratio`（默认 0.2）控制触发阈值
- 通过 `provider_semaphore_status()` 检查可用槽位比例
- 全局感知/拓展采集不退避，仍按计划执行
- 被跳过的任务保持到期状态，后续扫描重试

### CronScheduler 构造变更

- 新增 `config: dict | None` 参数，存储配置供 `_should_backoff()` 使用
- 新增 `transport_registry: Any | None` 参数，透传给 `execute_cron_task` 以注入工具上下文
- `scan_once()` 中先检查 action 是否属于全局系统任务，跳过退避检查

### _system_result_summary 变更

- 新增 `category`、`reason` 字段提取
- 新增 `failed` 列表提取
- 新增 `errors` 列表提取（含 module/reason/exception_type）

## 调用与依赖

| 关系 | 目标 | 条件 |
|------|------|------|
| calls | [[run-cron_store]] | 扫描任务和恢复中断 |
| calls | [[cron-executor]] | 任务到期时执行 |
| reads | run/users.py | 枚举用户目录 |
| queries | [[provider-factory]] | `_should_backoff` 调用 `provider_semaphore_status` |

## 相关笔记

- [[cron-总览]]
- [[cron-executor]]
- [[run-cron_store]]
- [[run-runtime_host]]
- [[provider-factory]]
- [[原理-定时任务]]