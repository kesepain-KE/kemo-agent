---
type: component
project: kemo-agent
domain: archive
module: improve-记忆系统
layer: L2
scope: project
status: archived
summary: improve — 记忆系统（已落地）
source: "archive/improve-记忆系统.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, improve, 记忆, 档位, 权重, 生命周期]
created: 2026-07-15
---
# improve — 记忆系统（已落地）

**状态**：✅ 第六轮：四档记忆生命周期闭环

## 存储结构

```
users/<user>/improve/
├── seven_days/data.json
├── one_month/data.json
├── half_year/data.json
└── permanent/data.json
```

所有档位使用统一条目契约，原子替换写入。

## 档位与审核规则

| 当前档位 | 持续时间 | 升级阈值 | 下一档位 |
|---|---:|---:|---|
| `seven_days` | 7 天 | 3 | `one_month` |
| `one_month` | 30 天 | 10 | `half_year` |
| `half_year` | 180 天 | 60 | `permanent` |
| `permanent` | 无到期 | 无 | 无 |

到达 `review_at` 后：
- **达标**：升级 → 权重归零 → 重新计时
- **不达标**：直接删除
- `permanent` 不自动到期，但允许用户纠正或删除

## 权重机制

- **不进行每日权重衰减**，不每天遍历全库减权
- 只有记忆被实际注入模型上下文或被任务使用才加权
- 仅检索命中**不加权**
- 同一记忆同一本地自然日最多加权一次
- 权重无上限
- 进入新档位后权重归零

## 记忆条目契约

```json
{
  "schema_version": 1,
  "id": "hex",
  "content": "内容",
  "type": "fact",
  "keywords": ["关键词"],
  "entities": ["实体"],
  "source": {},
  "confidence": 0.5,
  "importance": 0.5,
  "status": "active",
  "tier": "seven_days",
  "tier_weight": 0,
  "tier_entered_at": "ISO8601",
  "review_at": "ISO8601|null",
  "last_weight_date": "2026-07-17",
  "created_at": "ISO8601",
  "updated_at": "ISO8601",
  "explicit": false,
  "version": 1
}
```

## run/memory.py — MemoryStore

| 方法 | 说明 |
|------|------|
| `load_tier(tier)` | 加载单档位（含旧格式迁移） |
| `load_all()` | 加载全部（高层覆盖低层去重） |
| `upsert_candidates(candidates)` | 提交候选：upsert/forget，自动去重合并 |
| `forget(query)` | 按 ID/关键词删除 |
| `review_due()` | 到期审核：升级/删除 |
| `search(query)` | 混合检索：bigram + 关键词 + 子串 |
| `select_for_injection(query)` | 选取注入记忆（受 char/item 预算约束） |
| `mark_used(ids)` | 每日加权（同一自然日最多一次） |
| `list_items()` | 列出全部（按档位+权重排序） |

### 检索算法

- 中文：字符 bigram
- 英文/数字：词级匹配
- 子串匹配权重高于 token 重叠
- 档位 rank + importance + weight 综合排序

### 安全过滤

```python
_SECRET_RE — 拦截: api_key, token, password, secret, cookie, private_key, 密钥, 密码, sk-*, -----BEGIN PRIVATE KEY-----
```

敏感凭据禁止入库。

---

## run/memory_pipeline.py — 记忆管线

```python
submit_memory_extraction(root, user, config, user_text, assistant_text, tool_results, source)
```

流程：
1. 成功提交的对话轮次结束后调用
2. 检索已有记忆候选 → 传给 self_improve 子代理
3. self_improve 提取新候选 → 回调 upsert + review_due
4. 通过 AgentScheduler 后台串行提交

**只有成功提交的对话才能产生记忆。** Provider 失败、取消、未提交轮次不触发。

---

## run/agent_service.py — 调度器注册表

进程级单例 `AgentScheduler` 注册：

```python
get_agent_scheduler(root, user, config) → AgentScheduler
close_agent_schedulers()
```

同 (root, user) 返回同一调度器，跨请求复用。

---

## System Prompt 注入

`prompt.py` 新增 `memory_text` 参数。注入位序：

```
1. global_soul
2. user_soul
3. agents.md
4. memory_temporary_important.md
5. [relevant_memory]  ← 记忆注入（新增）
```

配置控制：
- `memory.injection_enabled`: 启用/禁用
- `memory.injection_max_chars`: 默认 2000
- `memory.injection_max_items`: 默认 8
