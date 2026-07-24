---
type: component
project: kemo-agent
domain: plugins
module: plugins-web_search
layer: L2
scope: project
status: active
summary: plugins/web_search/ — Tavily 网络搜索工具（SKILL.md 优化为决策表+参数速查+5示例）
source: plugins/web_search/tool.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, tool, 搜索, Tavily, 决策表]
---
# plugins/web_search/ — Tavily 网络搜索工具

`E:\code\kemo-agent\plugins\web_search\`

## 功能

通过 action 参数选择 5 种 Tavily 搜索操作：

| action | 功能 | 适用场景 |
|--------|------|---------|
| search | 网络搜索 | 查找最新信息、实时数据、事实核查 |
| extract | 正文提取 | 读取网页/文档全文 |
| crawl | 深度爬取 | 抓取文档站、知识库、博客整站内容 |
| map | 网站地图 | 发现站点 URL，侦察网站结构 |
| research | 深度研究 | 多源交叉验证、竞品分析、行业调研 |

## 工具定义

- name: `web_search`
- 需要 `TAVILY_API_KEY` 环境变量 + `tavily-python` 包
- entrypoint: `tool.py:run`

## SKILL.md 优化详情（2026-07-21）

SKILL.md 从简单参数列表改写为指令型引导：

- **使用决策表** — 根据任务需求快速选择 action（search vs extract vs crawl vs map vs research）
- **参数速查** — 每种 action 的关键参数一览
- **5 个典型示例** — 常见搜索场景的完整参数示例
- **返回字段解读** — `content_truncated` / `truncated` 等字段含义说明
- **指令型引导** — 从参数型 SKILL.md 改为指令型结构

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]