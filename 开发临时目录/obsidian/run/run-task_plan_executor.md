---
type: component
project: kemo-agent
domain: run
module: run-task_plan_executor
layer: L2
scope: project
status: active
summary: run/task_plan_executor.py — 计划执行核心
source: "run/run-task_plan_executor.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, run, 任务计划, 执行, tool_timeout, executor_managed, agent_managed]
---
# run/task_plan_executor.py — 计划执行核心

`E:\code\kemo-agent\run\task_plan_executor.py`

## tool_timeout 上下文注入

`execute_plan` 在构造任务计划工具上下文时注入 `tool_timeout` 字段，确保任务计划的工具执行使用一致的超时策略而非默认值。

## 概览

按依赖和顺序执行计划步骤，每步从磁盘重读最新计划。每步持久化状态。关键失败暂停，非关键失败继续。支持崩溃恢复。

## 类

### PlanExecutionError

`PlanExecutionError(RuntimeError)`

## 函数

### execute_plan (生成器)

```python
def execute_plan(
    *,
    root, user, plan_id,
    config=None, provider_factory=create_provider,
    tool_registry=None, cancel_event=None, event_callback=None,
) -> Iterator[RunEvent]
```

**每步流程**：
1. 从磁盘重读最新计划
2. `_next_step()` — 选 pending + deps 全 completed
3. 原子持久化 running → 执行工具 → 原子持久化结果
4. yield tool_call_start / tool_call_result / done / error
5. 关键步骤失败 → 暂停
6. 非关键 → 继续

**终止条件**：全部完成 / 关键失败 / 无可用步骤 / 取消。

### approve_plan

```python
def approve_plan(root, user, plan_id) -> dict
```

`pending → approved`

### pause_plan

```python
def pause_plan(root, user, plan_id) -> dict
```

`running/approved → paused`

### resume_plan

```python
def resume_plan(root, user, plan_id) -> dict
```

`paused → running`

### cancel_plan

```python
def cancel_plan(root, user, plan_id) -> dict
```

任意状态 → cancelled（pending/running 步骤也取消）。

### get_plan / list_plans

```python
def get_plan(root, user, plan_id) -> dict
def list_plans(root, user) -> list[dict]
```

### _next_step

```python
def _next_step(plan) -> dict | None
```

选择第一个 pending 且所有依赖已完成的步骤。

## executor-managed 模式（2026-07-23 新增）

`execute_plan` 现在支持两种执行模式：

- **executor-managed**（默认）：框架执行器驱动，注入 `_task_plan_mode=executor_managed`，prompt 明确声明步骤状态由框架维护，禁止主智能体调用 `step_done`/`step_fail`。状态由 executor `PlanExecutionError` 安全捕获并持久化。
- **agent-managed**（由 `stream_plan` 启用）：主智能体通过 `stream_chat` 注入 `_task_plan_mode=agent_managed`，在单轮连续执行中自主调用 `step_done`/`step_fail`。

`execute_plan` 的 prompt 末尾追加「本次为 executor-managed 模式，步骤状态由框架维护，禁止调用 task_plan.step_done 或 task_plan.step_fail」。
