---
type: component
project: kemo-agent
domain: run
module: run-task_plan_service
layer: L2
scope: project
status: active
summary: run/task_plan_service.py — 计划生成服务
source: "run/run-task_plan_service.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 任务计划, 生成]
---
# run/task_plan_service.py — 计划生成服务

`E:\code\kemo-agent\run\task_plan_service.py`

## 概览

通过 task_plan 子代理（reasoning 档位）将用户目标转化为结构化计划草案，校验后返回可落盘的计划。

## 类

### PlanGenerationError

`PlanGenerationError(RuntimeError)` — 生成失败。

### PlanSkipped

`PlanSkipped(PlanGenerationError)` — 子代理判定不需要计划。

## 函数

### generate_plan

```python
def generate_plan(
    *,
    root, user, goal, source="cli", session_id="default",
    config=None, provider_factory=create_provider,
    tool_registry=None, existing_plan=None, edit_request=None,
) -> dict
```

**流程**：
1. 注入可用工具清单（name + description）和相关记忆（5条/1000字符）
2. AgentRunner.run("task_plan", input_data)
3. 处理 action=skip（→ PlanSkipped）
4. 校验步骤数量 ≤ max_steps
5. normalize_plan → 校验 → 返回

### edit_plan

```python
def edit_plan(*, root, user, plan, edit_request, ...) -> dict
```

传入现有计划 + 修改要求 → 子代理编辑 → 返回更新计划。

### _tool_summary

```python
def _tool_summary(registry, max_tools=50) -> list[dict]
```

提取工具清单（name + description）。

### _relevant_memory

```python
def _relevant_memory(root, user, config, goal) -> str
```

检索相关记忆（最多 5 条，1000 字符）。
