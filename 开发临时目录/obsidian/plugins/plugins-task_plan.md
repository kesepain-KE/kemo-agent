---
type: component
project: kemo-agent
domain: plugins
module: plugins-task_plan
layer: L3
scope: project
status: active
summary: plugins/task_plan — 运行时计划管理工具（executor-managed 模式 + step_done 进度返回 + executor_managed 检测）
source: "plugins/task_plan/SKILL.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, plugins, task_plan, 计划管理, 运行时, step_done, step_fail, executor_managed, progress_payload]
---
# plugins/task_plan — 运行时计划管理工具

`E:\code\kemo-agent\plugins\task_plan\`

## 概览

运行态计划管理插件，提供查看、列出、标记步骤完成/失败以及批准、暂停、恢复和中止计划的工具接口。计划文件由 `PlanStore` 管理，位于 `users/<user>/task_plan/`。

## 核心原则

- **主智能体在 agent-managed 模式下执行完一个步骤后，必须立即调用 `step_done` 并写入结果摘要**；根据返回的 `next_step` 继续执行，直到计划完成或暂停。
- **executor-managed 模式下禁止调用 `step_done`/`step_fail`**：状态由框架执行器维护，工具检查 `context.task_plan_mode` 字段，若为 `executor_managed` 则拒绝 `step_done`/`step_fail` 调用。
- 步骤执行失败时必须调用 `step_fail`；失败步骤会被记录，计划自动暂停。
- **`create` 和 `edit` 不属于本工具**：创建和修改计划必须走 `task_plan` 子代理。
- **`task_plan` 是管理工具，不能被写成计划中的执行步骤**：`run/task_plan_store.py` 的 `_BLOCKED_TOOL_NAMES` 和 `_BLOCKED_TOOL_PREFIXES` 拦截。

## step_done 返回进度（2026-07-23 新增）

`step_done` 和 `step_fail` 现在额外返回 `_progress_payload`：

```python
{
    "completed_step": step,           # 刚完成的步骤
    "progress": {"completed", "total", "remaining"},
    "next_step": {...} | None,        # 下一个可运行步骤（仅计划 running 时有值）
    "remaining_steps": [...],         # 尚未终结的全部步骤
    "plan_status": "running",
}
```

- `_next_runnable_step` 按依赖顺序选择第一个 pending 的步骤
- `allow_paused=True` 参数允许在已暂停的计划上标记步骤完成（agent-managed 模式使用）
- 返回的 `next_step` 供主智能体判断是否继续执行

## 支持的 action

| action | 说明 |
|--------|------|
| `view` | 查看当前计划或某个计划的完整信息 |
| `list` | 列出当前用户全部计划 |
| `step_done` | 标记步骤已完成，写入结果摘要 |
| `step_fail` | 标记步骤失败，计划自动暂停 |
| `approve` | 用户批准计划（pending → approved → running） |
| `pause` | 用户暂停计划 |
| `resume` | 用户继续计划 |
| `abort` | 用户取消计划 |

## 计划状态机

```
pending → approved → running → completed
                     ↘ paused → running（恢复）
                     ↘ failed（依赖断裂）
                     ↘ cancelled
```

- `failed`：仍有未完成步骤但依赖断裂，已无可运行步骤
- `cancelled`：已取消
- 重启恢复：步骤状态为 `running` 的计划恢复为 `pending`（非 `paused`），计划状态转为 `paused`

## reminder 硬编码

无论 LLM 输出什么 reminder 文本，executor 都会根据 `auto_accept` 和 action 类型强制覆盖为规范文本。LLM 不需要自行生成 reminder，输出空字符串即可。

## 注入量说明

注入内容由调用方（引擎）在调用前组装，子代理只接收注入结果，不控制注入过程。当技能或知识库数量较大时，调用方应自行评估 Token 预算并决定是否截断。建议截断优先级：工具列表 > 用户技能 > 用户知识库索引 > 共享技能/知识库 > 插件全文。

## 代码证据

| 关系 | 目标 | 源码路径 | 条件 | 置信度 |
|---|---|---|---|---|
| depends_on | [[run-task_plan_store]] | plugins/task_plan/tool.py | PlanStore 验证和管理 | high |
| blocked_by | run/task_plan_store.py | _BLOCKED_TOOL_NAMES | task_plan 不能作为步骤 | high |

## 相关笔记

- [[run-task_plan_store]]
- [[run-task_plan_executor]]
- [[run-task_plan_service]]
- [[task_plan-总览]]
- [[agents-runtime]]
