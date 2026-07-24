---
type: component
project: kemo-agent
domain: archive
module: web-Web运行目录
layer: L2
scope: project
status: archived
summary: web — Web 运行目录
source: "archive/web-Web运行目录.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, web, Flask, React, FastAPI, 前端]
created: 2026-07-15
---
# web — Web 运行目录

**文件**：`web运行目录.txt`

## 架构

- **后端**：FastAPI
- **前端**：React + TypeScript + Vite

## 启动

```bash
python start_web.py --host=0.0.0.0 --port=1478
```

## 功能

- Web 聊天界面
- 任务计划可视化管理（审批、暂停、继续、中止）
- 定时任务管理
- 用户配置
- Provider 仪表
- 多会话支持

## 更新

`update.py` 覆盖 `web/` 目录。
