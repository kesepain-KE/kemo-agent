---
type: component
project: kemo-agent
domain: plugins
module: plugins-task_time
layer: L2
scope: project
status: active
summary: plugins/task_time/ — 定时任务管理工具（含 get action 和 time_plan 管道规则）
source: plugins/task_time/tool.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, tool, 定时任务, cron, time_plan管道]
---
# plugins/task_time/ — 定时任务管理工具

`E:\code\kemo-agent\plugins\task_time\`

## 功能

管理 cron 定时任务，支持创建、查看、修改、删除。

| action | 功能 |
|--------|------|
| create | 创建定时任务（daily / once / recurring） |
| list | 列出所有任务（新增 query 过滤） |
| get | 获取单个任务详情（新增） |
| update | 修改任务时间/命令/类型 |
| delete | 删除任务 |

## 工具定义

- name: `task_time`
- 委托给 cron 调度器执行
- entrypoint: `tool.py:run`

## time_plan 管道硬性调用规则（SKILL.md 新增）

SKILL.md 新增主智能体硬性调用规则：

- **time_plan 的职责**：接收自然语言，输出 title、自包含 prompt、type 及调度参数组成的结构化草案
- **task_time 的职责**：接收由 time_plan 解析或程序确定的结构化参数，持久化到 CronStore
- **主智能体的职责**：用户自然语言创建/修改任务时，必须先调用 time_plan，再调用 task_time；只有程序已确定完整参数时才可直接调用 task_time

### 编辑已有任务

必须先用 `task_time get` 读取现有任务，再把现有任务和修改要求交给 time_plan。主智能体不得绕过 time_plan 自行猜测 type/time/interval_seconds/next_run_at。

## 相关笔记

- [[plugins-manifest]]
- [[cron-总览]]
- [[run-cron_store]]
- [[agents-总览]]（time_plan 子代理）