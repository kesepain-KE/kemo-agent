---
type: component
project: kemo-agent
domain: run
module: run-maintenance
layer: L2
scope: project
status: active
summary: run/maintenance.py — 后台维护调度器（分析/持久化分离 + 陈旧结果保护）
source: run/maintenance.py
updated: 2026-07-26
verified: true
tags: [kemo-agent, run, maintenance, 维护, 记忆审阅, 上下文压缩, extraction_mode, history_summary]
---
# run/maintenance.py — 后台维护调度器

`E:\code\kemo-agent\run\maintenance.py` → `MaintenanceScheduler`

## 概览

独立于 cron 运行的系统级后台维护，负责定期扫描所有用户的重要记忆审阅、上下文压缩和记忆生命周期管理。

## 核心类

### MaintenanceScheduler

```python
class MaintenanceScheduler:
    def __init__(self, root, *, poll_interval=30.0,
                 provider_factory, tool_registry_factory, on_error)
```

后台线程驱动，独立于主对话引擎和 cron 定时任务运行。

## 维护任务

### 1. 重要记忆审阅（important_memory_review）

- 调度间隔由 `config["agents"]["important_memory_review_hours"]` 控制（默认 3 小时）
- 扫描所有用户的 temporary 记忆
- 调用 AgentRunner 让 `memory_temporary_important` 子代理生成热画像
- 结果写入 `users/<name>/memory_temporary_important.md`

### 2. 每日记忆审阅（daily_memory_review）

- 执行时间由 `config["agents"]["daily_memory_review_time"]` 控制（默认 02:00 北京时间）
- 调用 `MemoryStore.review_due()` 处理过期记忆档位升级/降级

### 3. 上下文压缩（context_review）

- 每 60 分钟扫描一次所有活跃会话
- 检查 round_limit_triggered / token_limit_triggered
- 触发时自动调用 `engine.compress_context()` 进行摘要压缩

## 重要变更（2026-07-26）

### 1. 记忆提取：分析/持久化分离

旧版：`_extract_round_memory` 在单次 locked 上下文中完成分析+持久化
新版：分离为两步

```python
# 第一步：在 locked 外执行分析（不修改任何数据）
analysis = _analyze_round_memory(round_number, agent_runner, ...)
# → 返回 {status, candidates, source, agent, usage}

# 第二步：在 locked 内持久化并校验游标
extraction = _persist_round_memory_analysis(root, user, config, analysis)
# → 调用 MemoryStore.upsert_candidates 实际写入
```

**好处**：分析阶段不持有 session lock，减少锁争用。持久化阶段锁时间短。

### 2. 陈旧结果保护（stale result）

持久化前重新加载 session index，校验：
- `memory_claim_id` 是否匹配当前 claim
- `memory_claim_round` 是否与提取轮次一致
- 游标是否仍为 `round_number - 1`

如果 claim 已失效（被新 claim 覆盖），标记为 `stale=True`，跳过持久化。

### 3. history_index 同步扩展

`finish_memory_claim` 返回的数据新增字段：

| 字段 | 说明 |
|------|------|
| `memory_processed_round` | 实际处理到的轮次 |
| `memory_status` | 完成后状态（`completed`/`deferred`/`queued`） |
| `memory_queue_reason` | 队列原因（`session_closed`/`manual_compression`） |
| `memory_target_round` | 目标处理轮次（大于 processed_round 时表示有剩余任务） |
| `memory_queued_at` | 队列登记时间 |
| `memory_last_error` | 最近一次错误记录（含 retry_count） |

### 4. target_round 队列调度

`_recover_pending_memory` 新增 `target_round` 支持：

- `claim_pending_memory` 时携带 `memory_target_round`
- 每次处理完一个轮次后更新 `memory_processed_round`
- 当 `processed_round >= target_round` 时结束该会话的提取
- 清理 `memory_queue_reason`/`memory_target_round`/`memory_queued_at` 字段

### 5. retry_count 指数退避

`finish_memory_claim` 在失败时记录 `memory_last_error.retry_count`：

- 检查 `memory_last_error` 中的 `retry_count`
- 每次重试 +1
- 多轮重试之间使用指数退避间隔（避免频繁重试同一失败轮次）

## 记忆恢复流程（_recover_pending_memory）

```
scan_once()
  → _recover_pending_memory(user)
    → 读取 extraction_mode
    → claim_pending_memory (原子领取)
      → 如果已 claim 失效: discard (stale)
      → 否则:
        → _session_lock 内加载归档窗口
        → _analyze_round_memory (无锁)
        → _session_lock 内校验游标
          → 游标已变: discard (stale)
          → 正常: _persist_round_memory_analysis → finish_memory_claim
```

## 历史摘要处理（history_summary）

### _recover_pending_summary

1. `claim_pending_summary` 原子租赁
2. 在 `_session_lock` 内加载归档
3. 按 `HISTORY_SUMMARY_CHUNK_CHARS=48000` 分块
4. 逐块调用 `AgentRunner.run("history_summary", ...)`
5. 使用 `cheap` 模型档位，`max_tokens=512`
6. `finish_summary_claim` 写入 title/summary/completed_round
7. 失败时支持指数退避重试（30s/120s/600s）

## 错误处理

- 单个用户的维护失败不影响其他用户
- 用户级错误通过 `on_error` 回调报告
- 循环级错误自动吞入 `on_error`，不中断线程

## 相关笔记

- [[run-总览]]
- [[run-engine]]
- [[run-memory]]
- [[run-agent_runner]]
- [[run-runtime_host]]
- [[run-history_index]]
- [[run-memory_pipeline]]
- [[原理-记忆升级权重]]
