---
type: component
project: kemo-agent
domain: run
module: run-cron_store
layer: L2
scope: project
status: active
summary: run/cron_store.py — CronStore
source: "run/run-cron_store.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 定时任务, 存储, CronStore]
---
# run/cron_store.py — CronStore

`E:\code\kemo-agent\run\cron_store.py`

## 职责

- 维护每个用户的 cron 任务文件
- 校验任务 schema
- 原子写入与更新
- 恢复中断执行中的任务

## 调用与依赖

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| writes | 用户任务文件 | run/cron_store.py | create / update / delete | 创建、更新、删除 cron 任务 | 未访问任务存储 | high | 2026-07-18 |
| reads | 用户任务文件 | run/cron_store.py | read / list_tasks / recover_interrupted | 查看或恢复任务 | 目录不存在或无任务 | high | 2026-07-18 |
| configured_by | run/config.py | run/cron_store.py | CronStore 路径归属 | 用户目录来自运行配置与用户目录结构 | 未加载用户配置 | medium | 2026-07-18 |

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| writes | cron_*.json | run/cron_store.py | _atomic_write / create / update | 写入任务文件时 | 文件不存在或删除时 | high | 2026-07-18 |
| reads | cron_*.json | run/cron_store.py | list_tasks / read | 扫描或读取任务时 | 目录为空 | high | 2026-07-18 |
| reads | task_cron 目录 | run/cron_store.py | _task_dir | 构造用户任务目录 | 用户目录不存在 | high | 2026-07-18 |

## 相关测试

- cron 任务存储与恢复测试

## 相关笔记

- [[cron-总览]]
- [[cron-scheduler]]
- [[原理-定时任务]]
