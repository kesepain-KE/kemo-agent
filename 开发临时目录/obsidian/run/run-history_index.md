---
type: component
project: kemo-agent
domain: run
module: run-history_index
layer: L2
scope: project
status: active
summary: run/history_index.py — 持久化逻辑会话注册表（conv_id/active绑定/记忆游标/恢复扫描）
source: "run/run-history_index.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, history, 索引, 会话, conv_id, active, 记忆游标, 恢复, history_summary, summary_claim, summary_status]
---
# run/history_index.py — 持久化逻辑会话注册表

`E:\code\kemo-agent\run\history_index.py`

## 概览

用户级可重建会话索引，存储在 `users/<user>/history/data.json`。映射逻辑会话到归档/运行时窗口和每条链路的活跃入口点。归档目录仍是完整对话的唯一真相来源；此注册表是小而可重建的。

## 核心概念

### conversation_id

`new_conversation_id()` 返回 `conv_<uuid_hex>` 格式的不透明机器标识符。新归档目录使用此 ID 命名，兼容旧时间戳目录。

### chain

`chain_for_source(source)` 将来源映射到链路类型：
- `web` / `cli` / `interactive` / `direct_api` → `interactive`
- `message:*` / `telegram` / `onebot` → `message`
- 其他 → `background`

### active 绑定

每个用户有 `active: { <active_key>: {source, session_id} }` 映射。`get_or_reserve_active()` 原子性地解析活跃绑定或预约新会话。

## 数据结构

```json
{
  "schema_version": 2,
  "revision": 42,
  "sessions": {
    "web\x1fconv_abc": {
      "conversation_id": "conv_abc",
      "session_id": "conv_abc",
      "source": "web",
      "chain": "interactive",
      "lifecycle": "open",       // open / closed / deleted
      "run_state": "idle",       // idle / running / failed
      "archive_window": "conv_abc",
      "runtime_window": "temp/conv_abc",
      "rounds": 3,
      "memory_processed_round": 3,
      "memory_status": "completed",
      "title": "...",
      "summary": "...",
      ...
    }
  },
  "active": {
    "interactive:alice": {"source": "web", "session_id": "conv_abc"},
    "message:telegram:private:12345": {"source": "message:telegram", "session_id": "conv_xyz"}
  }
}
```

## 关键函数

| 函数 | 说明 |
|------|------|
| `new_conversation_id()` | 生成 `conv_<uuid>` 标识符 |
| `upsert_window(root, user, source, session_id, directory, data)` | 插入/更新一个已提交归档窗口 |
| `reserve_session(root, user, source, session_id, *, active_key)` | 预约逻辑会话但不创建空归档 |
| `get_or_reserve_active(root, user, source, active_key, *, preferred_session_id, reuse_latest)` | 原子解析活跃绑定或预约下一个会话 |
| `find_record(root, user, source, session_id)` | 查找单个会话记录 |
| `list_records(root, user, *, source, query)` | 列出会话记录 |
| `update_run_state(root, user, source, session_id, *, run_state, run_id, directory)` | 更新运行状态 |
| `update_memory_state(root, user, source, session_id, *, processed_round, status, error)` | 更新记忆游标和状态 |
| `claim_pending_memory(root, user, *, worker_id)` | 原子租赁下一个未处理已提交轮次 |
| `finish_memory_claim(root, user, source, session_id, *, claim_id, processed_round, error)` | 完成记忆租赁 |
| `close_session(root, user, source, session_id)` | 关闭会话（lifecycle=closed） |
| `update_title(root, user, source, session_id, title)` | 更新标题 |
| `remove_session(root, user, source, session_id)` | 删除会话记录 |
| `remove_all_sessions(root, user, source)` | 删除指定来源全部会话 |
| `set_active(root, user, active_key, session_id, *, source)` | 设置活跃绑定 |
| `get_active(root, user, active_key)` | 获取活跃绑定记录 |

## 记忆恢复机制

### memory_processed_round 游标

每个会话记录维护 `memory_processed_round` 游标，跟踪已提取记忆的轮次。

### claim_pending_memory（2026-07-23 更新）

```python
def claim_pending_memory(root, user, *, worker_id=None, stale_after_seconds=900, statuses=None) -> dict | None
```

`MaintenanceScheduler` 调用此函数领取待处理的已提交轮次：
1. 扫描所有会话，找到 `memory_processed_round < last_committed_round` 的记录
2. 根据 `statuses` 参数过滤可领取状态（默认 `{"pending", "failed", "processing"}`）
3. 跳过正在处理（非过期）或正在运行的会话
4. 原子设置 `memory_claim_id`、`memory_claim_round`
5. 返回领取记录供 maintenance 执行提取

**statuses 参数**：允许 Maintenance 根据 `extraction_mode` 设置不同的可领取状态集。

### finish_memory_claim（2026-07-23 更新）

```python
def finish_memory_claim(root, user, source, session_id, *, claim_id, processed_round=None, error=None, remaining_status="pending") -> dict | None
```

- 新增 `remaining_status` 参数：提取完成后设置的下一个 memory_status
- `compression_only` 模式：`remaining_status="deferred"`
- `background`/`on_commit` 模式：`remaining_status="pending"`

### 过期清理

`_reconcile_unlocked` 中清理过期的 memory claim：
- 超过 `MEMORY_CLAIM_STALE_SECONDS`（15分钟）的 processing 状态被重置
- 超过 `MEMORY_RETRY_DELAY_SECONDS`（30秒）的 failed 状态可被重新领取

## 并发控制

- 进程内 `threading.RLock`（每用户一个）
- 跨进程文件锁（`msvcrt` on Windows, `fcntl` on POSIX）
- 原子写入（临时文件 + `os.replace`）

## 旧归档兼容

- 旧时间戳目录保留，不移动
- `_legacy_index_id` 为旧目录生成确定性 ID
- 旧归档（无 `memory_processed_round`）被视为已迁移（cursor = rounds）

## 调用者

- `run/history.py`（commit_window 时 upsert，list_sessions/delete/rename 时更新索引）
- `run/engine.py`（update_run_state, update_memory_state, set_active）
- `run/maintenance.py`（claim_pending_memory, finish_memory_claim）
- `run/cli.py`（get_or_reserve_active 解析共享会话）
- `message/router.py`（get_or_reserve_active 绑定外部消息会话）
- `web/service.py`（active_session, create_session, close_session, extract_session_memory, queue_history_summary）

## 历史摘要生命周期（2026-07-23 新增）

### 数据字段

每个会话记录新增摘要相关字段：

```json
{
  "summary_status": "none/queued/processing/completed/failed",
  "summary_target_round": 42,
  "summary_completed_round": 42,
  "summary_retry_count": 2,
  "summary_retry_at": "2026-07-23T12:00:00+00:00",
  "summary_error": {"message": "...", "exception_type": "..."},
  "summary_claim_id": "summary_xxx",
  "summary_claimed_at": "...",
  "summary_state_updated_at": "...",
  "title_source": "manual/auto"
}
```

### queue_summary

```python
def queue_summary(root, user, source, session_id) -> dict
```

关闭会话后由 `web/service.py` 调用，将备用状态转为 `queued`：
- 仅处理 `lifecycle=closed` 且 `target_round >= 1` 且可归档的会话
- 已完成的会话跳过（`completed_round >= target_round`）
- 返回 `{status, reason, rounds}`

### claim_pending_summary

```python
def claim_pending_summary(root, user, *, worker_id=None, stale_after_seconds=900) -> dict | None
```

`MaintenanceScheduler._recover_pending_summary` 在每次 `scan_once()` 开始时调用：
- 扫描所有 `lifecycle=closed` 的会话
- 按 `summary_status` 过滤（queued / processing过期 / failed到期）
- 返回单个最旧候选会话的完整记录

### finish_summary_claim

```python
def finish_summary_claim(root, user, source, session_id, *, claim_id, title=None, summary=None, completed_round=None, error=None) -> dict | None
```

- 成功：写入 title（`title_source=auto`）、summary、completed_round，状态设为 `completed`
- 失败：累加 `summary_retry_count`，按 30s/120s/600s 指数退避设置 `summary_retry_at`
- claim_id 不匹配时返回 None（防过期覆盖）

## 相关笔记

- [[run-history]]
- [[run-engine]]
- [[run-maintenance]]
- [[message-router]]
- [[web-service]]
- [[run-cli]]
