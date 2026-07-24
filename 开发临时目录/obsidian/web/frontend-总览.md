---
type: domain_overview
project: kemo-agent
domain: web
module: frontend-总览
layer: L1
scope: project
status: active
summary: frontend — 前端总览
source: "web/frontend-总览.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, web, frontend, React, Vite, 毛玻璃, 总览]
---
# frontend — 前端总览

## 定位

React + TypeScript + Vite 前端总览。

## 职责

- 提供可视化入口
- 调用后端只读 API
- 展示聊天、知识、任务、技能、感知等页面

## 非职责

- 不负责后端业务执行
- 不负责 Run 核心逻辑
- 不负责数据持久化

## 主要入口

- [[frontend-shell]]
- [[frontend-chat]]
- [[frontend-modules]]
- [[frontend-client]]
- [[frontend-knowledge-page]]

## 主要模块

- 壳层
- 聊天页
- 知识页
- 模块页
- API 客户端

## 直接关联领域

- web-service
- run

## 使用该领域的开发场景

- 修改前端布局、路由、样式
- 调整知识页展示
- 调整聊天流式消息展示

## 不需要进入该领域的情况

- 仅修改运行时内部逻辑
- 仅修改 cron 调度或 provider 协议

## 源码范围

- web/frontend/src/pages/*
- web/frontend/src/components/*
- web/frontend/src/api/*

## 检索建议

先从壳层或目标页面进入，再读取对应服务与 API 契约。

## 相关笔记

- [[frontend-shell]]
- [[frontend-chat]]
- [[frontend-modules]]
- [[frontend-client]]
- [[frontend-knowledge-page]]
