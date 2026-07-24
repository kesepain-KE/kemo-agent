# Cron 执行器缺少 TransportRegistry 透传

## 问题描述

定时任务（cron）中通过 `external_message` 工具向外部消息平台（Telegram）发送消息时，工具报错：
```
当前运行上下文未提供外部消息 TransportRegistry
```

**业务背景**：用户创建了一个 once 类型定时任务，由 CronScheduler 到期触发主智能体执行，主智能体调用 `external_message` 的 `send_message` 向 Telegram 用户发送一条文本消息。

## 根因

`external_message` 工具（`plugins/external_message/tool.py`）中的 `_registered_transport()` 函数从工具上下文 `context` 中读取 `transport_registry`：

```python
registry = context.get("transport_registry")
if not isinstance(registry, TransportRegistry):
    raise RuntimeError("当前运行上下文未提供外部消息 TransportRegistry")
```

这个 `transport_registry` 由引擎（`run/engine.py`）从 `request` payload 的 `_transport_registry` 字段注入到工具上下文中：

```python
# run/engine.py 第 1596-1599 行
context={
    ...
    "transport_registry": request.get(
        "_transport_registry"
    ),
    ...
}
```

**谁正确注入了 `_transport_registry`：**

Web Chat 入口 `web/service.py` 的 `stream_chat()` 方法（第 4788 行附近）在构建 request 时主动从 MessageRouter 获取 TransportRegistry 并注入：

```python
if self._router_ref is not None:
    transport_registry = getattr(self._router_ref, "transports", None)
    if transport_registry is not None:
        request["_transport_registry"] = transport_registry
```

**谁漏掉了 `_transport_registry`：**

Cron 执行器 `cron/executor.py` 的 `_execute_claimed_task()` 函数（第 170-178 行附近）在构建 request 调用 `handle_request` 时，**没有传入 `_transport_registry`**：

```python
handle_request(
    {
        "user": task["user"],
        "prompt": task["prompt"],
        "source": f"background:cron:{task_id}",
        "session_id": background_session_id,
        # ⚠️ 缺少 _transport_registry
    },
    ...
)
```

CronScheduler 运行在 RuntimeHost 内部（`run/runtime_host.py`），而 RuntimeHost 的 `router` 属性持有 `MessageRouter`，其 `transports` 属性就是 `TransportRegistry`。因此执行器**有途径**拿到 TransportRegistry，只是没有传递。

## 影响范围

- 任何通过 `exec_mode: "agent"` 的 cron 任务，如果在 prompt 中要求主智能体调用 `external_message` 工具发送消息，都会失败。
- `exec_mode: "subagent"` 和 `exec_mode: "function"` 不通过 `handle_request`，不受影响。
- Web Chat 发起的对话中调用 `external_message` 正常（因为已经正确注入）。
- 外部消息入站（通过 MessageRouter 路由）也正常（因为 router 自身的 route 流程也会注入）。

## 修复方案

在 `cron/executor.py` 的 `_execute_claimed_task()` 函数中，构建 request 字典时加入 `_transport_registry` 字段。

### 具体修改点

**文件**：`cron/executor.py`

**函数**：`_execute_claimed_task()`

**改动**：在构造传给 `handle_request` 的 request 字典时，从 RuntimeHost 的 router 获取 TransportRegistry 并注入。

```python
# 当前代码（约第 170-178 行）：
handle_request(
    {
        "user": task["user"],
        "prompt": task["prompt"],
        "source": f"background:cron:{task_id}",
        "session_id": background_session_id,
    },
    ...
)
```

需要改为类似 Web Chat 的做法，从现有的 RuntimeHost 组件中获取 `_transport_registry`。

**注意**：
- CronScheduler 运行在 RuntimeHost 内部（`run/runtime_host.py`），`RuntimeHost.router.transports` 就是 `TransportRegistry`。
- `execute_cron_task` 的调用方是 RuntimeHost 的 CronScheduler，而 CronScheduler 本身没有直接持有 router 引用。需要追溯调用链确定最佳注入点。
- 可参考 `web/service.py` 中 `WebRunService` 通过 `router_ref` 获取 transports 的模式。

## 可选变量注入点

1. **`cron/executor.py` 的 `execute_cron_task()`** — 接收额外的 `transport_registry` 参数
2. **`cron/scheduler.py` 的 `CronScheduler`** — 在 `_scan_user_tasks` 调用 `execute_cron_task` 时传入
3. **`run/runtime_host.py` 的 `RuntimeHost`** — 启动时把 router.transports 传给 CronScheduler

三个方案都需要修改 `cron/executor.py` 的 `_execute_claimed_task()` 函数中的 request 构建逻辑。
