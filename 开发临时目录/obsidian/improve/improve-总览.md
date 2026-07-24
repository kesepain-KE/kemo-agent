---
type: domain_overview
project: kemo-agent
domain: improve
module: improve-总览
layer: L1
scope: project
status: active
summary: improve — 记忆系统（总览，v3 文件型架构 + 批量提取）
source: "improve/improve-总览.md"
updated: 2026-08-04
verified: true
tags: [kemo-agent, improve, 记忆系统, 档位, 生命周期, v3, batch_extraction]
# improve — 记忆系统（总览，v3 文件型 + 批量提取）
# improve — 记忆系统（总览，v2 文件型）

> 注：`improve/` 非源代码目录，对应 `users/<user>/improve/` 数据存储目录。
> 源码实现位于 `run/memory.py`、`run/memory_pipeline.py`、`run/memory_migrate.py`。
## 存储结构（schema v3）
## 存储结构（schema v2）
├── storage.json                              # v3 标记 {schema_version: 3}
```
users/<user>/improve/
├── storage.json                              # v2 标记 {schema_version: 2}
├── seven_days/
│   ├── data.json                             # 轻量索引（weight/updated_at/last_weight_date/expires_at）
│   ├── 文件名.md                              # 独立正文文件（文件名 ≤20 字符）
│   └── ...
├── one_month/  (同上)
├── half_year/   (同上)
└── permanent/
    ├── 永久记忆.md                            # 无 data.json，纯 .md 文件
    └── memory_temporary_important.md          # 热画像（独立单文件，受字符上限控制）
```

## 四档升级路径

```
seven_days(7d/3权重) → one_month(30d/10) → half_year(180d/60) → permanent(无到期)
## 提取模式（v3）

`extraction_mode` 四模式枚举：

| 模式 | 说明 |
|------|------|
| `compression_only`（默认） | 普通提交只登记 `deferred` 游标，保存会话或上下文压缩时才顺序提取 |
| `background` | Maintenance 领取普通 `pending` 轮次 |
| `on_commit` | 历史提交后同步提取 |
| `disabled` | 不进行自动提取，Maintenance 也不会领取 |

## 批量提取（v0.2.0 / 2026-08-04）

- 待提取轮次按 `extraction_batch_rounds`（默认 5）组成连续批次，一次交给 `self_improve` 分析
- 候选统一匹配、去重并通过带稳定 operation_id 的 `upsert_candidates` 批量落盘
- `recovery_max_rounds_per_scan`（默认 10）是单次后台扫描的总轮数预算
- `extraction_max_candidates_per_batch`（默认 10）是单批候选上限

## Prompt 注入顺序

```text
permanent_memory         → 全部注入，文件名自然排序
important_memory         → memory_temporary_important.md，受 important_memory_max_chars 控制
temporary_memory:half_year   → 最多 100 条（weight 降序）
temporary_memory:one_month   → 最多 200 条
temporary_memory:seven_days  → 最多 300 条
```
temporary_memory:one_month   → 最多 4 条
temporary_memory:seven_days  → 最多 3 条
```

参见记忆生命周期与权重笔记。

## 核心代码

| 职责 | 源码文件 |
|------|---------|
| 记忆引擎（v2 文件型） | `run/memory.py` |
| 异步提取管线 | `run/memory_pipeline.py` |
| v1→v2 迁移工具 | `run/memory_migrate.py` |
| 记忆集成入口 | `run/engine.py` |

## 相关笔记

- [[run-memory]]
- [[run-memory_pipeline]]
- [[run-memory_migrate]]
- [[原理-记忆升级权重]]
- `memory_lifecycle.md`（项目根）
