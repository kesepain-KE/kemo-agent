---
type: component
project: kemo-agent
domain: message
module: message-state
layer: L3
scope: project
status: active
summary: message/state.py — 消息幂等存储（单键 + 多键 claim_many/complete_many）
source: "message/message-state.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, message, 幂等, 去重, 恢复, claim_many]
---
# message/state.py — 消息幂等存储

`E:\code\kemo-agent\message\state.py`

## 概览

每个用户在 `users/<user>/message_state/processed.json` 记录已处理消息，按 `<platform>:<message_id>` 原子领取。

## 类

### ProcessedMessageStore

```python
class ProcessedMessageStore:
    def __init__(self, root, user, *, max_entries=2000)
```

| 方法 | 说明 |
|------|------|
| `claim(key)` | 单键原子领取 → True/False（委托给 claim_many） |
| `claim_many(keys)` | 多键原子领取，全部成功才返回 True |
| `complete(key, status, error)` | 单键终态（委托给 complete_many） |
| `complete_many(keys, status, error)` | 多键终态，缺少任一键时报错 |
| `get(key)` | 查询记录 |
| `recover_interrupted()` | 重启恢复：processing→failed |

**状态**：`processing → completed / failed`

### _trim

```python
def _trim(messages, *, protected=None) -> None
```

按 updated_at 排序淘汰最旧条目。`protected` 集合中的键不会被淘汰，确保新领取的消息不会立即被裁剪。

## 多键幂等（2026-07-22 新增）

群聊合批时，`claim_many` 一次性领取批次内所有原始消息 ID 的幂等键：

```python
def claim_many(self, keys: tuple[str, ...]) -> bool:
    normalized = tuple(dict.fromkeys(...))
    with self._lock:
        if any(key in messages for key in normalized):
            return False  # 任一已存在则拒绝整批
        for key in normalized:
            messages[key] = {"status": "processing", ...}
        self._trim(messages, protected=set(normalized))
```

`complete_many` 批量写终态，缺少任一键时报 `MessageStateError`。

## 重启恢复

`processing` 状态转为 `failed`，不自动重放（避免副作用重复）。

## 错误

`MessageStateError(RuntimeError)`

## 变更记录

| 旧版 | 新版 |
|------|------|
| 只有 `claim(key)` / `complete(key)` | 新增 `claim_many(keys)` / `complete_many(keys)`，单键版委托给多键版 |
| `_trim(messages)` 无保护 | `_trim(messages, *, protected=None)`，新领取的键不被裁剪 |