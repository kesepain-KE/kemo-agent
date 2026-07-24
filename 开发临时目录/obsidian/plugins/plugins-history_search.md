---
type: component
project: kemo-agent
domain: plugins
module: plugins-history_search
layer: L3
scope: project
status: active
summary: plugins/history_search — 历史搜索工具（新增 since/until 时间过滤、role 角色过滤、match_mode、snippet、context 上下文窗口）
source: "plugins/plugins-history_search.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, 工具, 历史搜索, since, until, role, context]
---
# plugins/history_search — 历史搜索工具

`E:\code\kemo-agent\plugins\history_search/`

## 功能

按关键词搜索对话历史窗口，返回匹配消息列表。

## 工具定义

- name: `history_search`
- entrypoint: `tool.py:run`

## 优化详情（2026-07-21）

- **since / until 时间过滤** — 新增按时间范围筛选历史消息
- **role 角色过滤** — 新增按消息角色（user/assistant/tool）过滤
- **match_mode 匹配精度** — 新增匹配模式选择（精确/模糊/正则）
- **snippet 片段截断** — 返回匹配片段摘要而非完整消息
- **context 上下文窗口** — 新增可配置上下文行数，返回匹配消息前后的上下文
- **指令型 SKILL.md** — 含层级和权限说明

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]