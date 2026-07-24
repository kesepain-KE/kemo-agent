---
type: component
project: kemo-agent
domain: run
module: run-task_plan_store
layer: L2
scope: project
status: active
summary: run/task_plan_store.py — PlanStore
source: "run/run-task_plan_store.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, 任务计划, prompt, blocked_tools, step_recovery]
---
# run/task_plan_store.py — PlanStore

## 职责

- 维护计划文件
- 校验计划 schema
- 恢复中断中的计划
- 提供 prompt 注入内容

## 被阻止的工具（2026-07-23 更新）

```python
_BLOCKED_TOOL_PREFIXES = ("task_plan_",)
_BLOCKED_TOOL_NAMES = frozenset({"task_plan"})
```

`task_plan` 是运行态管理工具，不能作为计划中的执行步骤。验证逻辑同时检查前缀匹配和精确名称匹配。

## 恢复中断状态（2026-07-23 更新）

重启恢复时，步骤状态为 `running` 的计划：
- 计划状态转为 `paused`
- 步骤状态恢复为 `pending`（旧版恢复为 `paused`，现改为 `pending`）
- 不自动重放有副作用的步骤
- 等待用户决定恢复或取消

## 调用与依赖

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| reads | 计划文件 | run/task_plan_store.py | PlanStore.read / list_plans | 构建计划视图或 prompt | 目录不存在 | high | 2026-07-18 |
| writes | 计划文件 | run/task_plan_store.py | PlanStore.create / update / delete | 创建或更新计划 | 未修改计划 | high | 2026-07-18 |
| reads | 任务计划目录 | run/task_plan_store.py | _plan_dir | 用户计划路径构造 | 用户目录不存在 | high | 2026-07-18 |
| documented_by | [[原理-计划任务]] | run/task_plan_store.py | select_prompt_plans / PlanStore | 计划存储与注入说明 | 仅读源码不读原理笔记 | high | 2026-07-18 |

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| called_by | run/prompt.py | run/task_plan_store.py | select_prompt_plans | PromptBundle 注入 task_plan 时 | 没有计划文本 | high | 2026-07-18 |
| reads | plan_*.json | run/task_plan_store.py | list_plans / recover_interrupted | 读取计划文件 | 目录为空 | high | 2026-07-18 |

## 相关测试

- 计划创建与恢复测试
- prompt 注入测试

## 相关笔记

- [[task_plan-总览]]
- [[run-prompt]]
- [[原理-计划任务]]
