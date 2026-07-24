---
type: component
project: kemo-agent
domain: provider
module: provider-schema
layer: L3
scope: project
status: active
summary: provider/schema.py — Provider 契约
source: "provider/provider-schema.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, provider, 数据契约, 请求, 响应]
---
# provider/schema.py — Provider 契约

## 模块定位

定义 provider 层的中立数据合同。

## 所属领域

provider

## 职责

- 定义 ChatRequest / ChatResponse / Usage / ToolCall
- 定义 ChatProvider Protocol
- 定义 provider 错误类型
- 定义多模态能力集合

## 非职责

- 不负责网络请求实现
- 不负责模型路由
- 不负责运行时编排

## 主要符号

- ProviderError
- ProviderAuthError
- ProviderTimeoutError
- Usage
- ToolCall
- ChatRequest
- ChatResponse
- ChatProvider
- MULTIMODAL_CAPABILITIES

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| defines | ChatProvider | provider/schema.py | ChatProvider | provider 实现需要协议 | 仅文档阅读 | high | 2026-07-18 |
| defines | ChatRequest/ChatResponse | provider/schema.py | ChatRequest / ChatResponse | provider 数据交换 | 没有请求或响应建模 | high | 2026-07-18 |
| defines | MULTIMODAL_CAPABILITIES | provider/schema.py | MULTIMODAL_CAPABILITIES | 能力判断与展示 | 不涉及多模态能力 | high | 2026-07-18 |

## 相关测试

- provider schema 序列化测试
- 协议兼容测试

## 相关笔记

- [[provider-总览]]
- [[provider-factory]]
- [[原理-工具调用]]
