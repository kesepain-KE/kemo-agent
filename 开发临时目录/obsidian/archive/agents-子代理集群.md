---
type: component
project: kemo-agent
domain: archive
module: agents-子代理集群
layer: L2
scope: project
status: archived
summary: agents — 子代理集群
source: "archive/agents-子代理集群.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, agents, task_plan, 子代理, v2]
created: 2026-07-15
---
# agents — 子代理集群

**状态**：✅ 第七轮：task_plan 子代理 v2.0.0

## 统一清单契约 (agent.json)

### task_plan v2.0.0（升级）

| 字段 | v1.0.0 | v2.0.0 |
|------|--------|--------|
| `description` | 根据显式任务数据创建或编辑结构化任务计划 | +「不直接执行工具或写文件」 |
| `input_schema` | `additionalProperties: true` | 严格 Schema：action(create/edit)、goal、tools、max_steps、existing_plan、edit_request、memory |
| `output_schema` | `additionalProperties: true` | 严格 Schema：action(create/edit/skip)、title、description、steps[]、message |

三模式支持：
- `create` — 新建计划，返回 steps
- `edit` — 编辑已有计划，返回更新 steps
- `skip` — 判断不需要计划，返回 message

### 五个子代理总览

| 代理 | 版本 | 模型档位 | 执行模式 | 写入策略 |
|------|------|----------|----------|----------|
| `context_manage` | 1.0.0 | cheap | sync | derived_cache |
| `self_improve` | 1.0.0 | reasoning | background_serial | user_memory |
| `memory_temporary_important` | 1.0.0 | cheap | background_serial | user_memory |
| `task_plan` | **2.0.0** | reasoning | background_serial | user_task |
| `time_plan` | 1.0.0 | default | background_serial | user_task |

## 上下文摘要迁移

- 所有上下文摘要统一由 `context_manage`（sync）处理
- `token_condense` 已于 2026-07-21 废弃删除

`get_or_create_summary()` 通过 `AgentRunner` 委托子代理执行。

## 独立上下文

所有子代理不继承主对话历史，只接收专用指令 + 显式输入。
