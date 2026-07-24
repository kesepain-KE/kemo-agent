---
type: domain_overview
project: kemo-agent
domain: project
module: global_knowledge-全局知识库
updated: 2026-08-04
status: active
summary: global_knowledge/ — 全局共享知识库（设计文档已迁移至开发文档目录）
source: "global_knowledge-全局知识库.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, global_knowledge, 知识库, 设计文档]
created: 2026-07-15
corrections: 2026-07-23 删除已实现的编程方案文档
---
# global_knowledge/ — 全局共享知识库

全局级别的知识文档和配置适配方案，所有用户共享，不可被更新脚本覆盖。

## 当前目录内容

| 文件 | 说明 |
|------|------|
| `data_structure.md` | 全局知识库目录索引 |
| `子代理配置规范.md` | 子代理清单和配置规范（已更新为 5 个子代理） |
| `用户目录结构.md` | 用户目录结构说明 |
| `全局配置文件.md` | 全局配置字段说明 |
| `环境变量.md` | 环境变量说明 |
| `web-README.md` | Web 前端 README |
| `user-config-reference.md` | 用户配置参考手册（重写） |
| `global-config-reference.md` | 全局配置参考手册（重写） |
| `version-and-update-modules.md` | 版本号与更新模块参考（新增/重写） |
| `cron-task-creation.md` | Cron 定时任务创建指南（新增） |
| `env-reference.md` | 环境变量参考（新增） |
| `expand-creation.md` | 拓展模块创建指南（新增） |
| `external-message-route-creation.md` | 外部消息路由创建指南（新增） |
| `knowledge-creation.md` | 知识库创建指南（新增） |
| `open-source-license.md` | 开源许可证说明（新增） |
| `sense-creation.md` | 感知模块创建指南（新增） |
| `skill-creation.md` | 技能创建指南（新增） |
| `subagent-creation.md` | 子代理创建指南（新增） |
| `task-plan-creation.md` | 任务计划创建指南（新增） |
| `user-directory-skeleton.md` | 用户目录骨架指南（新增） |

## 已删除的编程方案（2026-07-23）

以下已实现的编程方案文档已从 `global_knowledge/` 删除，不再需要：

- `cron-executor-missing-transport-registry.md` — Cron transport_registry 透传已实装
- `cron-executor-transport-registry-编程方案.md` — 同上
- `网页语音输入功能编程方案.md` — 语音输入已实装（useSpeechRecognition hook）
- `网页重启功能编程方案.md` — Web 重启已实装（restart.py + /api/system/restart）
- `task-plan-plugin-plan.md` — task_plan 插件已实装

## 设计文档迁移

- `token_condense废弃-编程规划.md`
- `方案实装缺陷清单.md`
- `子代理骨架适配-编程规划.md`
- `context_manage运行时适配-编程规划.md`
- `memory_temporary_important运行时适配-编程规划.md`
- `self_improve运行时适配-编程规划.md`
- `task_plan运行时适配-编程规划.md`
- `cron模块精简-编程规划.md`
- `cron模块精简-补丁.md`
- `感知模块标准化重构方案.md`
- `拓展模块标准化重构方案.md`
- `知识库重构方案（索引全量化+路径二删除）.md`
- `全局配置文件-编程适配方案.md`
- `用户配置文件-编程适配方案.md`
- `环境变量-编程适配方案.md`
- `Kemo网关-统一Provider协议适配要求.md`
- `子代理骨架适配-编程规划.md`

## 使用方式

- 通过 `config.json` 中 `knowledge.use_global` 控制启用
- 受 `source_policy.knowledge_scopes` 白名单约束
- 子代理通过 `agent-config.json` 中 `knowledge.scopes` 设置

## 优先级

用户级 > 共享级 > 全局级

## 相关笔记

- [[shared-用户共享模块]]
- [[run-knowledge]]
- [[run-source_policy]]
