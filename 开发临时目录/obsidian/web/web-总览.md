---
type: domain_overview
project: kemo-agent
domain: web
module: web-总览
layer: L1
scope: project
status: active
summary: web — Web 模块总览（含管理端点：agent删除/消息模块/拓展CRUD/技能CRUD/感知CRUD）
source: "web/web-总览.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, web, FastAPI, React, 总览, 管理端点]
---
# web — Web 模块（总览）

## 定位

Web 领域负责把运行时能力通过 HTTP 与前端页面暴露出来。

## 规模

| 维度 | 变化 |
|------|------|
| 业务 API | 基础 API + 新增 19 条管理端点（tmp 批删/agent 删除/消息检查删除/拓展刷新/技能文档 CRUD/感知刷新） |
| 前端页面 | 13 个（新增 AgentsPage/ExpandPage/MessagesPage/SkillsPage 样式） |
| 新增 CRUD | 子代理/消息/拓展/技能/感知全管理闭环 |

## 前端页面

| 路由 | 页面 | 状态 |
|------|------|------|
| `/chat` | ChatPage | 更新 |
| `/tasks` | TasksPage | 重写（CRUD） |
| `/knowledge` | KnowledgePage | 更新 |
| `/memory` | MemoryPage | 新增 |
| `/agents` | AgentsPage | 新增 |
| `/skills` | SkillsPage | 重写（分类管理 CRUD） |
| `/sense` | SensePage | 重写（CRUD + 刷新） |
| `/expand` | ExpandPage | 新增 |
| `/files` | FilesPage | 更新 |
| `/messages` | MessagesPage | 新增（含日志解析） |
| `/status` | RuntimeStatusPage | 新增 |
| `/runtime` | RuntimeModulesPage | 保留 |
| `/profile` | ProfilePage | — |
| `/settings` | SettingsPage | 更新 |

## 主要入口

- [[web-app]] — API 端点
- [[web-service]] — WebRunService 适配层
- [[frontend-client]] — API 客户端
- [[frontend-modules]] — 页面与组件

## 源码范围

- web/app.py / web/service.py / web/auth.py
- web/frontend/src/ — React 前端