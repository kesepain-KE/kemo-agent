---
type: component
project: kemo-agent
domain: plugins
module: plugins-subagent_dispatch
layer: L2
scope: project
status: active
summary: plugins/subagent_dispatch/ — 子代理发现与调度工具（含四步创建流程）
source: plugins/subagent_dispatch/tool.py
updated: 2026-07-26
verified: true
tags: [kemo-agent, plugins, tool, 子代理, 调度, 四步创建]
---
# plugins/subagent_dispatch/ — 子代理发现与调度工具

`E:\code\kemo-agent\plugins\subagent_dispatch\`

## 功能

发现和调度当前用户可用的公开子代理。通过 action 参数选择操作：

| action | 功能 |
|--------|------|
| list | 列出所有可用的公开子代理 |
| create | 创建数据型用户代理定义 |
| call | 同步调用指定子代理 |
| status | 查询后台任务状态 |
| cancel | 取消后台任务 |

## 工具定义

- name: `subagent_dispatch`
- 仅公开子代理（exposure=tool、allowed_callers 包含调用方）可被调度
- 禁止被子代理自身使用（子代理工具装配时自动排除）
- entrypoint: `tool.py:run`

## 四步创建流程（SKILL.md 新增）

SKILL.md 新增四步创建流程，强制要求创建子代理前依次完成：

1. **批判性判断** — 判断需求是否真的需要独立子代理（vs 插件/技能/直接执行）
2. **确认工具权限** — 开放哪些插件工具、共享技能、知识库访问、历史继承
3. **确认触发提示词** — 自然语言描述触发条件，精炼为 trigger_condition
4. **检查冲突与创建** — action=list 检查名称冲突、汇总确认、action=create

### 不适合创建子代理的场景

- 单次查询、一次性整理或纯文件增删改
- 主智能体可以直接回答或执行
- 现有插件、技能或已注册子代理已覆盖需求

### 权限确认要点

- 从主智能体当前 Tool Registry 列出真实可用名称供用户选择
- `subagent_dispatch action=list` 只列出公开子代理，不能获取工具列表
- 不替用户扩大权限；用户未确认时使用空工具、空技能白名单
- 创建失败报告原始校验错误，不偷偷改名或放宽权限后重试

## 调用已有子代理

- call 模式：agent（名称）+ input（结构化输入）+ wait（同步/后台）
- status 模式：使用后台任务 ID 查询状态
- cancel 模式：取消尚未完成的后台任务
- 调用前先读取目标子代理的 trigger.md，按其中约定构造结构化 input

## 子代理调用适配器（2026-07-26）

`subagent_dispatch action=call` 现在通过 `prepare_main_agent_invocation()` 和 `persist_main_agent_result()` 对内置子代理进行输入适配：

```python
invocation = prepare_main_agent_invocation(
    root, user, agent, input, config
)
payload = invocation.payload
```

- `task_plan`：注入 availale_tools、技能、知识库索引、max_steps、auto_accept；必须同步
- `time_plan`：强制注入 `current_time_beijing`（框架覆盖模型层提交值）
- `self_improve`：限制 `trigger=manual_review`
- `memory_temporary_important`：限制 `trigger=periodic_scan`/`daily_consolidate`

同步调用完成后（`wait=True`），调用 `persist_main_agent_result()`：
- `task_plan`：持久化计划结果到磁盘
- 其他：返回 None

异步调用（`wait=False`）遇到 `synchronous_only=True` 时抛出 ValueError。

依赖 `run/subagent_invocation.py`。

## 相关笔记

- [[plugins-manifest]]
- [[agents-总览]]
- [[agents-runtime]]
- [[run-agent_runner]]
- [[run-agent_queue]]
- [[run-subagent_invocation]]