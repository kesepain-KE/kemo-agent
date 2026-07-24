---
type: component
project: kemo-agent
domain: cron
module: cron-review_due
layer: L2
scope: project
status: active
summary: cron/review_due.py — 临时记忆过期扫描与晋升调度
source: "cron/cron-review_due.md"
updated: 2026-07-21
verified: false
tags: [kemo-agent, cron, review_due, 记忆晋升, memory_promotion, 临时记忆]
---
# cron/review_due.py — 临时记忆过期扫描与晋升调度

`E:\code\kemo-agent\cron\review_due.py` → `scan_and_promote()`

## 定位

Cron 模块持有的独立函数，定期扫描所有用户的临时记忆层（seven_days / one_month / half_year），删除低权重过期条目，将高权重条目提交给 `self_improve` 子代理审核晋升。

## 函数签名

```python
def scan_and_promote(
    *,
    root: Path,
    user: str,
    config: dict[str, Any],
    provider_factory=create_provider,
    cancel_event=None,
    now=None,
) -> dict[str, Any]
```

## 执行流程

1. **删除过期低权重条目**：
   - 遍历每层临时记忆的索引文件
   - 检查 `expires_at` 是否已过当前时间
   - 如果 `weight < upgrade_threshold` 或文件已不存在 → 直接删除
   - 如果 `weight >= upgrade_threshold` → 加入晋升候选列表

2. **晋升候选提交**：
   - 调用 `AgentRunner` 以 `self_improve` 子代理身份运行
   - trigger = `"memory_promotion"`，传入 `promotions` 数组
   - 子代理返回决策（promotions 数组），每条含 from_tier / to_tier / filename / merged_with

3. **应用决策**：
   - 匹配晋升决策与候选条目
   - 如 `merged_with` 存在，执行合并晋升（内容合并到目标文件）
   - 否则直接晋升到下一层（规则表配置的 `next` 层）
   - 晋升时更新 `expires_at` 为新层的保留期限

## 调度关系

- 由 `cron/schedule.py` 定期调用（周期由配置 `cron.review_due_hours` 控制）
- 依赖 `MemoryStore`（`run/memory.py`）读取/写入临时记忆
- 依赖 `AgentRunner`（`run/agent_runner.py`）调用 `self_improve` 子代理
- 依赖 `self_improve` 子代理的 AGENT.md 中的 memory_promotion 触发描述

## 返回值

```python
{
    "status": "completed" | "cancelled",
    "requested": int,      # 提交审查的条目数
    "deleted": [str],      # 已删除的临时记忆文件名
    "promotions": [dict],  # 子代理返回的晋升摘要
    "applied": [str],      # 实际晋升的文件名列表
    "model": str,          # 执行模型
    "usage": dict,         # token 用量
}
```

## 进阶

### MemoryPromotionError

当临时记忆层配置了 `upgrade_threshold` 但无 `next` 目标层时，或 `self_improve` 输出不符合预期时抛出，不会中断整轮扫描。

### 取消机制

支持 `cancel_event` 信号，可在扫描完成后、子代理执行前中断。

## 相关笔记

- [[cron-总览]]
- [[cron-schedule]]（调用方）
- [[run-memory]]（MemoryStore 依赖）
- [[run-agent_runner]]（AgentRunner 依赖）
- [[agents-总览]]（self_improve 子代理）
- [[improve-总览]]（记忆系统）
