---
type: component
project: kemo-agent
domain: run
module: run-subagent_invocation
layer: L2
scope: project
status: active
summary: run/subagent_invocation.py — 内置子代理输入适配（runtime authority 注入）
source: "run/subagent_invocation.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, run, subagent, 子代理, task_plan, time_plan, self_improve]
---
# run/subagent_invocation.py — 内置子代理输入适配

`E:\code\kemo-agent\run\subagent_invocation.py`（新增）

## 概览

为主智能体调用内置子代理（task_plan、time_plan、self_improve、memory_temporary_important）提供权威的输入适配。保证运行时权限（时间、配置、触发模式）不由模型擅自决定。

## 核心函数

### prepare_main_agent_invocation

```python
def prepare_main_agent_invocation(
    *, root, user, agent, input_data, config=None
) -> PreparedSubagentInvocation
```

- 通用子代理：直接透传 input_data
- 内置子代理（task_plan/time_plan/self_improve/memory_temporary_important）：调用对应 `_prepare_*` 函数

### persist_main_agent_result

```python
def persist_main_agent_result(
    *, root, user, agent, payload, result_data, source, session_id, config
) -> dict | None
```

- `agent == "task_plan"`：调用 `persist_agent_result()` 持久化计划结果
- 其他子代理：返回 None

## PreparedSubagentInvocation

```python
@dataclass(frozen=True, slots=True)
class PreparedSubagentInvocation:
    payload: dict[str, Any]
    synchronous_only: bool = False
```

- `synchronous_only=True` 时，插件 `subagent_dispatch` 会拒绝异步 `wait=False` 调用

## 内置子代理适配器

### _prepare_task_plan

- 调用 `prepare_task_plan_input()` 构建权威输入
- 注入 available_tools、plugin_skills、三层知识库索引、max_steps、auto_accept、relevant_memory
- `synchronous_only=True`（必须同步，完成校验和持久化）

### _prepare_time_plan

- 校验 `action` 为 `create`/`edit`/`delete`
- `create`/`edit` 时强制注入 `current_time_beijing`（框架层覆盖，模型提交的同名值无效）
- `delete` 时移除 `current_time_beijing`

### _prepare_self_improve

- 限制 `trigger` 只能为 `manual_review`（引擎/调度器内部使用的 `context_compression`/`memory_promotion` 不可从外部调用）
- 校验 `request` 非空

### _prepare_important_memory

- 限制 `trigger` 为 `periodic_scan` 或 `daily_consolidate`

## 适配器注册表

```python
_PREPARERS = {
    "task_plan": _prepare_task_plan,
    "time_plan": _prepare_time_plan,
    "self_improve": _prepare_self_improve,
    "memory_temporary_important": _prepare_important_memory,
}
```

不在注册表中的子代理按通用子代理处理（input 直接透传）。
