---
type: component
project: kemo-agent
domain: plugins
module: plugins-network
layer: L2
scope: project
status: active
summary: plugins/network/ — HTTP 网络请求与网页读取（新增 put/delete/patch、truncated 统一、max_bytes 可配）
source: plugins/network/tool.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, tool, 网络, HTTP, put, delete, patch]
---
# plugins/network/ — HTTP 网络请求与网页读取

`E:\code\kemo-agent\plugins\network\`

## 功能

通过 action 参数选择 6 种操作：

| action | 功能 |
|--------|------|
| get | HTTP GET 请求 |
| post | HTTP POST 请求 |
| **put** | HTTP PUT 请求（新增） |
| **delete** | HTTP DELETE 请求（新增） |
| **patch** | HTTP PATCH 请求（新增） |
| read | 网页正文读取（支持 auto/direct/reader 策略） |

## 工具定义

- name: `network`
- 无网络范围限制
- entrypoint: `tool.py:run`

## 优化详情（2026-07-21）

- **新增 put/delete/patch**：从 get/post 扩展到完整 REST 方法集
- **统一 truncated**：所有请求方法返回体均携带 truncated 标志
- **失败返 error**：请求失败时返回结构化 error 字段而非异常
- **max_bytes 可配**：响应体最大字节数可配置，防止超长响应

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]