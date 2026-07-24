---
type: component
project: kemo-agent
domain: provider
module: provider-factory
layer: L2
scope: project
status: active
summary: provider/factory.py — Provider 工厂 + 进程级并发总闸（信号量/槽位/反压）
source: "provider/provider-factory.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, provider, 工厂, chat, kemo, ProviderAdapter, 并发, 信号量, 反压]
---
# provider/factory.py — Provider 工厂与并发总闸

## 模块定位

按 provider.type 创建具体 ProviderAdapter 实现。支持 `chat` 和 `kemo` 两种类型。同时管理进程级 Provider 并发控制——所有来源（Web、消息路由、Cron、维护任务、子代理）共享一个信号量。

## 职责

- 读取 provider.type，返回 ProviderAdapter
- 进程级 Provider 并发信号量管理
- Provider 请求槽位申请与释放
- 并发状态查询（active/available/waiting）

## 非职责

- 不负责请求协议细节
- 不负责模型选择策略
- 不负责运行时编排

## 主要入口

### create_provider

按 `provider.type` 创建 ProviderAdapter。

### ProviderCongestionError

```python
class ProviderCongestionError(RuntimeError):
    """等待 Provider 全局并发槽位超时。"""
```

### get_provider_semaphore

```python
def get_provider_semaphore(max_concurrent: int = 10) -> threading.BoundedSemaphore
```

进程级单例信号量，空闲时可重配置。所有来源共享。

### provider_request_slot (上下文管理器)

```python
@contextmanager
def provider_request_slot(config, *, cancel_event=None) -> Iterator[None]
```

限制一次真实 LLM API 请求。工具执行期间释放槽位（避免主智能体调用子代理时嵌套自锁）。等待超时抛 `ProviderCongestionError`。支持取消事件。

### provider_semaphore_status

```python
def provider_semaphore_status(config=None) -> dict[str, int]
```

返回 `active_requests`、`max_requests`、`available_requests`、`waiting_estimate`。供运行时状态 API 的 `congestion.provider` 使用。

## 并发控制工作原理

1. `provider_runtime.max_concurrent_requests`（默认 10）配置信号量大小
2. `provider_runtime.request_semaphore_timeout`（默认 300s）配置等待超时
3. 每次 LLM API 请求前 `provider_request_slot` 尝试获取槽位
4. 工具执行期间不占用槽位（槽位在 yield 内释放）
5. 队列满时等待，超时抛 `ProviderCongestionError`
6. 信号量空闲时可动态重配置（无活跃请求且无等待者时）

## 类型变更

| 版本 | 类型 |
|------|------|
| 旧版 | "openai" / "kemo"，返回 ChatProvider |
| **新版** | **"chat" / "kemo"，返回 ProviderAdapter** |

## 调用者

- run/engine.py（主智能体，通过 provider_request_slot）
- run/agent_runner.py（子代理，通过 provider_request_slot）
- message/router.py
- run/runtime_host.py
- cron/scheduler.py（通过 _should_backoff 读取状态）

## 被调用对象

- provider/adapters/chat_bridge.py
- provider/kemo_gateway.py

## 配置

- `provider.type`：chat / kemo
- `provider.model`
- `provider.base_url`
- `provider.api_key_env`
- `provider_runtime.max_concurrent_requests`（默认 10）
- `provider_runtime.request_semaphore_timeout`（默认 300.0）

## 代码证据

| 关系 | 目标 | 条件 |
|------|------|------|
| calls | [[provider-openai_chat]]（ChatBridgeProvider） | provider.type=chat |
| calls | [[provider-kemo_gateway]] | provider.type=kemo |
| used_by | [[run-engine]] | provider_request_slot 包裹 LLM 调用 |
| used_by | [[run-agent_runner]] | provider_request_slot 包裹子代理 LLM 调用 |
| queried_by | [[cron-scheduler]] | _should_backoff 调用 provider_semaphore_status |
| queried_by | [[web-service]] | runtime_status 调用 provider_semaphore_status |

## 相关笔记

- [[provider-总览]]
- [[原理-运行原理]]
