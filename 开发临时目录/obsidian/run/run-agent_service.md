---
type: component
project: kemo-agent
domain: run
module: run-agent_service
layer: L2
scope: project
status: active
summary: run/agent_service.py — 调度器注册表
source: "run/run-agent_service.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 子代理, 调度器, 注册表]
---
# run/agent_service.py — 调度器注册表

`E:\code\kemo-agent\run\agent_service.py`

## 概览

进程级 (root, user) → AgentScheduler 单例注册，跨请求复用同一调度器。

## 函数

### get_agent_scheduler

```python
def get_agent_scheduler(
    root: Path,
    user: str,
    *,
    config: dict,
    provider_factory=create_provider,
    event_callback=None,
) -> AgentScheduler
```

按 (root, user) 查找或创建 AgentScheduler。首次创建时初始化 AgentRunner + AgentScheduler.from_runner()。

### close_agent_schedulers

```python
def close_agent_schedulers(*, wait=True, cancel_pending=False) -> None
```

关闭所有注册的调度器，清空注册表。

---

## 内部

- `_lock: threading.RLock` — 注册表并发保护
- `_schedulers: dict[(root, user), AgentScheduler]` — 注册表
