---
type: domain_overview
project: kemo-agent
domain: task_plan
module: task_plan-总览
layer: L1
scope: project
status: active
summary: task_plan — 任务计划系统（总览）
source: "task_plan/task_plan-总览.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, task_plan, 状态机, 生命周期]
---
# task_plan — 任务计划系统（总览）

## 定位

任务计划的存储、生成与执行领域总览。

## 职责

- 原子存储计划
- 生成计划草案
- 按依赖执行计划
- 提供 prompt 注入

## 非职责

- 不负责 run 主对话链路
- 不负责 provider 协议
- 不负责 Web UI 呈现

## 主要入口

- [[run-task_plan_store]]
- [[run-task_plan_service]]
- [[run-task_plan_executor]]

## 主要模块

- 存储
- 生成
- 执行

## 直接关联领域

- run
- agents
- improve

## 使用该领域的开发场景

- 调整计划状态机
- 修改计划创建/审批/执行
- 修改计划 prompt 注入

## 不需要进入该领域的情况

- 仅修改知识页或插件文档
- 仅修改 provider 协议

## 源码范围

- run/task_plan_store.py
- run/task_plan_service.py
- run/task_plan_executor.py

## 检索建议

先读存储，再读生成或执行。修改执行链路时同时看 run-engine。

## 相关笔记

- [[run-task_plan_store]]
- [[run-task_plan_service]]
- [[run-task_plan_executor]]
- 原理-计划任务
