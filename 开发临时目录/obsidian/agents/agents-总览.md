---
type: domain_overview
project: kemo-agent
domain: agents
module: agents-总览
layer: L1
scope: project
status: active
summary: agents — 子代理体系总览（self_improve 三模式、memory_temporary_important 三路触发、time_plan↔task_time 管道规则）
source: agents/
updated: 2026-07-26
verified: true
tags: [kemo-agent, agents, 子代理, 清单, schema-v2, manual_review, time_plan管道, history_summary, agent_models]
---
# agents — 子代理体系总览

## 定位

kemo-agent 的子代理（sub-agent）系统，支持 schema v2 的数据型用户代理发现、装配和执行。

## 结构

```
agents/
├── _runtime/          # 子代理运行时基础（schema、资源、prompt装配）
├── context_manage/    # 上下文管理子代理
├── memory_temporary_important/  # 重要记忆审阅子代理
├── self_improve/      # 自我改进子代理
├── task_plan/         # 任务计划子代理
├── time_plan/         # 时间计划子代理
├── __init__.py
└── agent.json         # （每个子代理目录下有自己的 agent.json）
```

## 关键文件

| 文件 | 用途 |
|------|------|
| `agent.json` | **精简清单（schema-less）**，仅含 `name/version/description/trigger` 四字段；或旧版 schema v2 完整清单 |
| `agent-config.json` | 能力配置（schema v1），控制 exposure、tools、prompt_sources、knowledge、context 权限 |
| `AGENT.md` | 子代理指令（instruction），由 `agent.json` 同目录引用 |
| `executor.py` | 内置子代理 Python 执行器；用户子代理仅允许 `builtin:llm` |
| `trigger.md` | 触发元数据，含 `# 注册信息` 段，定义子代理的触发条件、职责、模型和编排规则 |
| `schema.json` | **可选（新增）** 子代理输入输出 JSON Schema，用于结构化校验 |

## 精简清单（agent.json）v2 关键字段

```json
{
  "name": "context_manage",
  "version": "2.0.0",
  "description": "按完整对话轮执行上下文压缩与记忆提取编排",
  "trigger": "trigger.md"
}
```

- `name`: 必须与目录名一致
- `trigger`: 指向同目录 `trigger.md`
- executor / execution / write_policy / model_profile **由内建默认值按 name 自动注入**

## Trigger 注册系统

每个子代理目录下的 `trigger.md` 包含 `# 注册信息` 段（必填），定义代理名称、触发场景、职责描述、模型要求和编排规则。

## 内置子代理一览

| 名称 | 职责 | 调用方 | 执行器 | 触发方式 |
|------|------|--------|--------|-----------------|
| `context_manage` | 上下文压缩与记忆提取 | engine / main_agent | `executor.py:execute` | 引擎自动触发 / 主智能体主动触发 |
| `memory_temporary_important` | 巡检临时记忆并维护热画像 | scheduler / main_agent | `executor.py:execute` | cron 定时 + 主智能体主动唤起 |
| `self_improve` | 提取新微记忆碎片、增量权重、层间晋升 | scheduler / context_manage / main_agent | `executor.py:execute` | 三模式：context_compression / memory_promotion / **manual_review** |
| `task_plan` | 创建/编辑结构化任务计划草案 | main_agent | `executor.py:execute` | 主智能体工具调用时触发 |
| `time_plan` | 解析自然语言定时要求→结构化定时任务 | main_agent | `executor.py:execute` | 主智能体工具调用时触发 |
| `history_summary` | 为已关闭历史对话生成卡片标题与摘要 | scheduler | `executor.py:execute` | Maintenance 扫描时触发（background_serial） |

## self_improve 三模式（2026-07-21 变更）

| trigger | 调用方 | 说明 |
|---------|--------|------|
| `context_compression` | context_manage（内部直调） | 上下文压缩前传入即将裁剪的批量轮次 |
| `memory_promotion` | cron review_due 任务 | 发现到期且权重达标的碎片后唤起 |
| **`manual_review`** | 主智能体（通过 subagent_dispatch） | **新增**：用户主动要求审阅/整理/搜索记忆 |

### manual_review 模式流动

1. 主智能体通过 `subagent_dispatch` 传入 `{ trigger: "manual_review", request: "..." }`
2. 执行器使用 memory_manage 搜索相关记忆，返回 candidates
3. 执行器自动将 candidates 写入 MemoryStore（`upsert_candidates`）
4. 纯搜索允许返回空 candidates 并在其他输出字段说明结果
5. 手动模式不执行层间晋升；晋升仍只走 memory_promotion

## memory_temporary_important 三路触发（2026-07-21 变更）

| 调用方 | 路径 | 说明 |
|--------|------|------|
| cron `CronScheduler` | `AgentRunner.run()` 直调 | 按 schedule 自动触发 periodic_scan / daily_consolidate |
| 主智能体 | `subagent_dispatch` 工具 | 用户手动要求触发巡检或每日整理时主动调用 |

## time_plan ↔ task_time 管道规则（2026-07-21 新增）

agents.md 和 trigger.md 新增硬性调用规则：

- 用户自然语言创建定时任务时，主智能体必须先调用 `time_plan` 生成自包含提示词和结构化调度参数，再调用 `task_time create`
- 用户自然语言编辑任务时，必须先用 `task_time get` 读取现有任务，调用 `time_plan` 解析修改要求，最后调用 `task_time update`
- 删除前也必须先核对任务
- 只有内部程序或 API 已经确定完整调度参数时，才能直接调用 `task_time create/update`

## 运行时基础（_runtime/）

`agents/_runtime/` 提供子代理的装配和执行能力：

- **schema.py**: `AgentDefinition` / `AgentRegistry` / `AgentCapabilities` 定义，`discover_agents()` 发现内置+用户代理
- **resources.py**: `build_agent_prompt_bundle()` 拼接子代理 prompt，`build_agent_tool_registry()` 按白名单装配工具
- **user_packages.py**: 用户数据型代理包管理
- **user_resources.py**: 用户资源管理

## 注册流程

1. `discover_agents(root, user)` 扫描 `agents/` 和 `users/<user>/agents/`
2. 优先尝试 **精简清单模式**（schema-less，仅 `name/version/description/trigger` 四字段）
3. 精简模式不匹配时回退旧版 **schema v2 完整清单**
4. 用户代理不能重名内置代理，不能含 Python 文件（仅允许 `builtin:llm` 执行器）
5. 返回 `AgentRegistry` 供调度使用

## 子代理调用适配器（2026-07-26）

`run/subagent_invocation.py` 为内置子代理提供输入适配，保证运行时权限不由模型擅自决定：

| 子代理 | 适配 | 说明 |
|--------|------|------|
| `task_plan` | `prepare_task_plan_input` | 注入 tools/skills/knowledge/max_steps/auto_accept；必须同步 |
| `time_plan` | 强制注入 `current_time_beijing` | 框架覆盖模型提交值 |
| `self_improve` | 限制 `trigger=manual_review` | context_compression/memory_promotion 仅供引擎/调度器 |
| `memory_temporary_important` | 限制 `trigger=periodic_scan/daily_consolidate` | 禁止从外部使用 |

适配器在 `plugins/subagent_dispatch` 中调用，`synchronous_only` 子代理拒绝异步 `wait=False`。

## 相关笔记

- [[run-agent_queue]]（子代理调度队列）
- [[run-agent_runner]]（子代理执行器）
- [[run-agent_service]]（子代理注册与服务）
- [[run-agents]]（子代理清单加载）
- [[run-总览]]
- [[agents-runtime]]（运行时基础 + 执行器变更）
- [[agents-registry]]（注册表与发现）
- [[run-subagent_invocation]]（子代理输入适配）