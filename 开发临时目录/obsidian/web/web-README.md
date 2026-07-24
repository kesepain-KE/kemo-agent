---
type: component
project: kemo-agent
domain: web
module: web-README
layer: L3
scope: project
status: active
summary: web/README.md — Web 使用文档
source: "web/web-README.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, web, README, 文档]
---
# web/README.md — Web 使用文档

`E:\code\kemo-agent\web\README.md`

## 技术栈

React 19 + TypeScript + Vite / React Router / TanStack Query / Zustand / Zod / Vitest + MSW

## 安装

```powershell
cd E:\code\kemo-agent\web\frontend
npm.cmd install --registry=https://registry.npmmirror.com --no-audit --no-fund
```

## 开发

终端1: `python start_web.py`
终端2: `npm.cmd run dev` → `http://127.0.0.1:5173`

## 构建与测试

```powershell
npm.cmd test
npm.cmd run build
```

## 环境变量

`VITE_API_BASE_URL` — 留空同源，指定 URL 直连。禁止放入 API Key。

## 已接接口（全部实现）

| 端点 | 类型 |
|------|:--:|
| `/api/health` | 健康 |
| `/api/users` | 用户 |
| `/api/users/{user}/sessions` | 会话 |
| `/api/users/{user}/sessions/{id}/history` | 历史 |
| `POST /api/chat` | SSE 流 |
| `/api/users/{user}/overview` | Overview |
| `/api/users/{user}/tasks` | 任务 |
| `/api/users/{user}/knowledge` | 知识 |
| `/api/users/{user}/skills` | 技能 |
| `/api/users/{user}/sense` | 感知 |
| `/api/users/{user}/settings` | 配置 |

全部模块页面已接入真实 API，不再有占位页面。
