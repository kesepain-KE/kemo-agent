---
type: domain_overview
project: kemo-agent
domain: run
module: run-总览
layer: L1
scope: project
updated: 2026-08-04
corrections: 2026-07-18 核验源码后修正；2026-07-21 补充新模块；2026-07-26 新增 process_utils + subagent_invocation；2026-08-04 v0.2.0 新增 multimodal + attachments + guidance + media_outputs 模块
source: "run/run-总览.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, run, 运行核心, 总览, PromptBundle, 感知, 拓展, 维护]
corrections: 2026-07-18 核验源码后修正；2026-07-21 补充新模块；2026-07-26 新增 process_utils + subagent_invocation
---
# run — 运行核心（总览）

## 定位

kemo-agent 的运行时编排核心，负责一次请求从配置、提示词、上下文、工具循环到历史提交的完整链路，以及后台维护与记忆生命周期管理。

## 职责

- 组装 PromptBundle
- 选择上下文与知识注入
- 运行工具循环
- 提交历史窗口
- 处理记忆提取与加权
- 协调任务计划与子代理
- 后台维护扫描（上下文压缩、重要记忆审阅、记忆生命周期）
- 资源准入策略控制

## 非职责

- 不负责 Provider 协议定义
- 不负责外部消息传输实现
- 不负责 Web UI 呈现
- 不负责插件具体业务能力实现
- 不负责子代理的 prompt 与工具装配（由 agents/_runtime/ 负责）

## 主要入口

- [[run-engine]] — 主引擎，事件驱动工具循环
- [[run-prompt]] — PromptBundle 编排
- [[run-context]] — 上下文选择与预算
- [[run-memory]] — 文件型记忆引擎（schema v2）
- [[run-tools]] — 工具注册与调用
- [[run-prompt_sources]] — 拓展/感知/技能注入选择器
- [[run-knowledge]] — 知识库索引与检索
- [[run-kemo_graph]] — 知识图谱替换边界
- [[run-maintenance]] — 后台维护调度器
- [[run-source_policy]] — 主智能体资源准入策略
- [[run-runtime_host]] — 后台宿主进程

## 主要模块

| 模块 | 源码 | 职责 |
|------|------|------|
| 引擎 | `run/engine.py` | 对话主循环、事件驱动、上下文压缩触发 |
| 提示词编排 | `run/prompt.py` | PromptBundle 七层/十七段拼接 |
| 上下文管理 | `run/context.py`, `run/context_summary.py` | 上下文窗口预算与整轮摘要 |
| 历史存储 | `run/history.py` | 对话历史 JSONL 窗口管理 |
| 记忆系统 | `run/memory.py`, `run/memory_pipeline.py` | 文件型四档记忆引擎与异步提取管线 |
| 记忆迁移 | `run/memory_migrate.py` | v1→v2 一次性迁移工具 |
| 知识检索 | `run/knowledge.py` | 知识库双层索引与检索 |
| 工具系统 | `run/tools.py`, `run/prompt_sources.py`, `plugins/manifest.py` | 工具注册、SKILL.md 解析、技能注入 |
| 拓展/感知 | `run/prompt_sources.py` | PromptSourceRegistry（拓展注入 + 感知选择） |
| 知识图谱边界 | `run/kemo_graph.py` | 外部 kemo-graph 提示词替换接口 |
| 后台维护 | `run/maintenance.py` | 重要记忆审阅、上下文压缩、记忆生命周期 |
| 资源准入 | `run/source_policy.py` | MainAgentSourcePolicy 配置驱动的资源白名单 |
| 子代理 | `run/agents.py`, `run/agent_queue.py`, `run/agent_runner.py`, `run/agent_service.py` | 子代理注册、调度、执行、服务 |
| 配置管理 | `run/config.py`, `run/users.py` | 用户配置加载、用户列表管理 |
| 任务计划 | `run/task_plan_store.py`, `run/task_plan_service.py`, `run/task_plan_executor.py` | 计划存储、生成、执行 |
| 后台宿主 | `run/runtime_host.py` | 后台线程宿主，管理 cron + 维护 |
| CLI 桥接 | `run/cli.py` | CLI 模式与引擎桥接 |
| Cron 存储 | `run/cron_store.py` | 定时任务持久 JSON 存储 |
| 子进程工具 | `run/process_utils.py` | 跨平台子进程控制（隐藏窗口、进程组、进程树终止） |
| 子代理调用适配 | `run/subagent_invocation.py` | 内置子代理输入适配（task_plan/time_plan/self_improve 的 runtime authority 注入） |
| 多模态路由 | `run/multimodal.py` | **v0.2.0 新增**：多模态输入/输出路由，主模型优先、插件兜底 |
| 附件处理 | `run/attachments.py` | **v0.2.0 新增**：上传文件附件解析与内联 |
| 运行中引导 | `run/guidance.py` | **v0.2.0 新增**：运行中引导队列与 guidance_applied 事件 |
| 媒体输出 | `run/media_outputs.py` | **v0.2.0 新增**：生成媒体下载校验与防重名保存 |

## 直接关联领域

- provider — API 协议适配
- message — 外部消息路由
- cron — 定时任务调度
- web — Web UI 后端
- task_plan — 任务计划
- improve — 记忆系统
- agents — 子代理系统

## 使用该领域的开发场景

- 修改对话主链路
- 调整上下文预算
- 修改记忆注入逻辑
- 修改工具调用循环
- 修改任务计划执行链路
- 调整后台维护策略
- 修改资源准入白名单

## 不需要进入该领域的情况

- 仅修改单个前端样式
- 仅调整一个插件自身能力
- 仅改共享知识文档内容
- 仅修改子代理的 AGENT.md 指令

## 源码范围

- run/engine.py
- run/prompt.py
- run/context.py, run/context_summary.py
- run/history.py
- run/memory.py（含 schema v3 文件型记忆引擎）
- run/memory_migrate.py（v1→v2 一次性迁移工具）
- run/memory_pipeline.py（异步记忆提取管线）
- run/knowledge.py
- run/kemo_graph.py（知识图谱替换边界）
- run/maintenance.py（后台维护调度器）
- run/tools.py
- run/prompt_sources.py（拓展/感知/技能选择器）
- run/source_policy.py（资源准入策略）
- run/agents.py, run/agent_queue.py, run/agent_runner.py, run/agent_service.py
- run/task_plan_store.py, run/task_plan_service.py, run/task_plan_executor.py
- run/runtime_host.py
- run/config.py, run/users.py, run/cli.py, run/cron_store.py
- run/process_utils.py（跨平台子进程工具函数）
- run/process_utils.py（跨平台子进程工具函数）
- run/subagent_invocation.py（内置子代理输入适配）
- run/multimodal.py（**v0.2.0 新增**：多模态路由）
- run/attachments.py（**v0.2.0 新增**：附件处理）
- run/guidance.py（**v0.2.0 新增**：运行中引导）
- run/media_outputs.py（**v0.2.0 新增**：媒体输出）

## 检索建议

优先从 [[run-engine]] 与 [[run-prompt]] 进入，再根据任务词扩展到上下文、记忆、知识、计划与工具。新模块建议从 [[run-maintenance]] 和 [[run-source_policy]] 入手了解后台架构变动。

## 相关笔记

- [[run-engine]]
- [[run-prompt]]
- [[run-maintenance]]
- [[run-kemo_graph]]
- [[run-source_policy]]
- [[run-memory_migrate]]
- 原理-运行原理
- 原理-PromptBundle编排
- agents-总览
