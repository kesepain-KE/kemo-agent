---
type: component
project: kemo-agent
domain: web
module: frontend-knowledge-page
layer: L2
scope: project
status: active
summary: web/frontend/src/pages/KnowledgePage.tsx — KnowledgePage
source: "web/frontend-knowledge-page.md"
updated: 2026-07-18
verified: partial
---
# web/frontend/src/pages/KnowledgePage.tsx — KnowledgePage

## 职责

- 展示知识文件索引
- 提供 scope 筛选与文本搜索
- 显示外接项目状态
- 只读呈现，不直接修改知识内容

## 调用与依赖

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| reads | web/service.py | web/frontend/src/pages/KnowledgePage.tsx | getKnowledge(user) | 用户进入知识页时 | 不打开知识页 | high | 2026-07-18 |
| reads | 用户/共享/全局索引 | web/frontend/src/pages/KnowledgePage.tsx | documents 渲染 | 服务返回文档列表 | 服务未返回知识数据 | high | 2026-07-18 |
| related_to | global_knowledge | web/frontend/src/pages/KnowledgePage.tsx | 外接项目卡片 | 仅展示外接项目占位 | 并未连接图谱扩展 | medium | 2026-07-18 |

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| reads | getKnowledge(user) | web/frontend/src/pages/KnowledgePage.tsx | useQuery | 进入知识页并读取服务数据 | 页面未打开 | high | 2026-07-18 |
| related_to | 外接项目 · kemo-graph | web/frontend/src/pages/KnowledgePage.tsx | extension-card | 仅用于占位提示 | 图谱未连接 | medium | 2026-07-18 |

## 相关笔记

- [[frontend-总览]]
- [[web-service]]
- [[global_knowledge-全局知识库]]
