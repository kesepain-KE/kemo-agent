---
type: component
project: kemo-agent
domain: plugins
module: plugins-skill_creater
layer: L2
scope: project
status: active
summary: plugins/skill_creater/ — 技能创建插件（新增 list/get/validate 和四步创建流程）
source: "plugins/skill_creater/"
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, skill_creater, 技能创建, 技能管理, 四步流程]
---
# plugins/skill_creater/ — 技能创建插件

`E:\code\kemo-agent\plugins\skill_creater/`

## 定位

安全创建、更新或删除当前用户技能及共享技能。self_improve 子代理只允许 agent_create 作用域的创建操作。

## 结构

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 工具注册描述（含四步创建流程指令） |
| `tool.py` | 工具入口，执行技能创建/更新/删除/list/get/validate |

## 工具定义

- name: `skill_creater`
- entrypoint: `tool.py:run`

### action 说明（新增 list/get/validate）

| action | 功能 |
|--------|------|
| `create` | 创建新技能目录及 SKILL.md（新增结构化参数：title+description+tool_schema/instruction） |
| `update` | 更新已有技能的 SKILL.md |
| `delete` | 删除技能目录及所有文件 |
| `list` | 列出指定 scope 下的技能（新增） |
| `get` | 获取指定技能的 SKILL.md 内容（新增） |
| `validate` | 校验技能结构完整性（新增） |

### scope 说明

| scope | 作用域 | 路径 |
|-------|--------|------|
| `agent_create` | 子代理专用技能 | `users/<user>/agents/<agent>/skills/` |
| `user_create` | 用户私有技能 | `users/<user>/user_skills/` |
| `shared` | 共享技能 | `shared_skills/` |

## 四步创建流程（SKILL.md 新增）

SKILL.md 新增四步创建流程，强制要求创建前依次完成：

1. **批判性判断** — 判断是否需要独立技能（vs 子代理/插件/直接执行）
2. **确认 scope 和名称** — 英文目录名、适用 scope
3. **确认技能内容** — title、description、tool_schema 或 instruction
4. **检查冲突与创建** — action=list 检查同名、汇总确认、action=create

## 权限限制

- self_improve 子代理只能执行 agent_create 作用域的 create 操作
- 其他 scope/action 组合仅主智能体可调用

## 相关笔记

- [[plugins-manifest]]
- [[agents-总览]]（self_improve 子代理的技能创建权限）