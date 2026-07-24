---
type: component
project: kemo-agent
domain: run
module: run-runtime_host
layer: L2
scope: project
status: active
summary: run/runtime_host.py — 后台运行宿主（含文件夹插件发现 / finalize 钩子）
source: "run/run-runtime_host.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, RuntimeHost, 后台宿主, 插件发现, finalize]
---
# run/runtime_host.py — 后台运行宿主

`E:\code\kemo-agent\run\runtime_host.py`

## 概览

统一托管 CronScheduler、MessageRouter 和 Transport 生命周期。宿主不实现平台协议，也不经过 CLI 调用 Run。

## RuntimeHost

状态：

```text
stopped → starting → running → stopping → stopped
                      ↓
                    failed
```

核心方法：

- `register_transport(transport, policy)`：停止状态注册 Transport；
- `start()`：恢复中断状态、启动 Router/Transport/Cron；
- `stop()`：停止 Transport/Router/Cron，重复调用幂等；
- `wait(timeout)`：等待停止信号；
- `status()`：返回宿主与组件状态。

## 关键变更（2026-07-21）

### 6. 消息模块健康检查与注销

新增两个方法，用于 Web 消息模块管理：

```python
def check_message_transport(self, platform: str, bound_user: str) -> dict[str, Any]:
    """Run an explicit health check for one user-bound folder transport."""
    item = self.registry.get(platform)
    transport = item.transport
    if not isinstance(transport, FileMessageTransport):
        raise RuntimeError(...)
    if transport.config.bound_user != bound_user:
        raise PermissionError(...)
    return transport.check_health()

def remove_message_transport(self, platform: str, bound_user: str) -> None:
    """Stop and unregister one folder transport before its files are deleted."""
    item = self.registry.get(platform)
    transport = item.transport
    if not isinstance(transport, FileMessageTransport):
        raise RuntimeError(...)
    if transport.config.bound_user != bound_user:
        raise PermissionError(...)
    transport.stop()
    item.state = "stopped"
    self.registry.unregister(platform)
    with self._lock:
        self._components.pop(f"transport:{platform}", None)
```

这两个方法通过 `WebRunService` 的 `message_health_checker` 和 `message_transport_remover` 回调注入，供 Web API 调用。

## 关键变更（2026-07-22）

### 1. 文件夹插件自动发现

```python
plugin_transports, discovery_issues = discover_message_plugins(self.root)
self._message_plugin_issues.extend(discovery_issues)
for transport in plugin_transports:
    self.registry.register(transport, transport.policy)
```

RuntimeHost 构造时自动扫描 `message/out/` 目录，注册有效插件。失败的插件以 `MessagePluginIssue` 记录。

### 2. 组件状态区分

`FileMessageTransport` 类型的注册项在组件状态中标记为 `message_plugin` 而非 `transport`：

```python
self._components[f"transport:{item.transport.name}"] = ComponentStatus(
    item.transport.name,
    "message_plugin" if isinstance(item.transport, FileMessageTransport) else "transport",
)
```

失败的插件也记录在组件状态中（state="failed"）。

### 3. _accept_message 返回 future

```python
def _accept_message(self, envelope):
    if self.state not in {"starting", "running"}:
        return None
    try:
        future = self.router.submit(envelope)
        future.add_done_callback(...)
        return future
    except Exception as exc:
        self._handle_error(envelope.platform, exc)
        return None
```

旧版仅 `if not self.running: return`，新版支持 `starting` 状态并返回 future。

### 4. finalize 钩子

```python
def _handle_result(self, result: RouteResult) -> None:
    try:
        registered = self.registry.get(result.envelope.platform)
        finalize = getattr(registered.transport, "finalize", None)
        if callable(finalize):
            finalize(result)
    except Exception as exc:
        self._handle_error(result.envelope.platform, exc)
```

RouteResult 完成后，如果 Transport 有 `finalize()` 方法（FileMessageTransport 实现），调用它来清理领取文件、写日志、删除附件。

### 5. 外部 Router 支持

```python
if router is None:
    self.router = MessageRouter(...)
else:
    if self.on_result is None:
        self.on_result = router.on_result
    router.on_result = self._handle_result
    router.on_error = self._handle_error
    self.router = router
```

允许外部传入已构造的 Router，自动绑定宿主的 result/error 回调。

## 组件故障隔离

每个 Transport 有独立状态与 last_error。单 Transport 启动失败不阻止其他 Transport 和 Cron 启动。宿主核心组件启动失败会进入 failed 并清理已启动组件。

## 关键变更（2026-07-22 追加）

### 6. 感知与拓展系统任务注册

`start()` 中在 `ensure_memory_maintenance_tasks` 和 `ensure_memory_promotion_task` 之后，新增：

```python
ensure_perception_task(self.root, self.config)
ensure_expand_task(self.root, self.config)
```

### 7. Cron 轮询间隔自动取最小值

新增 `_cron_poll_interval(config)` 函数，取 `cron.poll_interval` 与 `sense_update_rate`、`expand_update_rate` 的最小值，保证短周期感知/拓展任务按时被扫描。

### 8. 消息路由有界队列参数

构造 MessageRouter 时传入 `max_queued_messages`：

```python
max_queued_messages=int(runtime_message_config.get("max_queued_messages", 20))
```

### 9. CronScheduler 接收 config

构造 CronScheduler 时传入 `config=self.config`，供 `_should_backoff()` 使用。

### 10. Maintenance 轮询间隔

`MaintenanceScheduler` 使用 `_positive_seconds` 安全解析 `cron.poll_interval`。

### 11. transport_registry 透传至 Cron（2026-07-23 新增）

```python
CronScheduler(
    ...,
    transport_registry=self.router.transports,
)
```

`RuntimeHost` 将 `self.router.transports` 传递给 `CronScheduler`，使 Cron 执行器能向工具上下文注入 `transport_registry`，解决定时任务中 `external_message` 工具无法访问 Transport 注册表的问题。

## 依赖

- `cron.scheduler.CronScheduler / recover_all / ensure_perception_task / ensure_expand_task`
- `message.router.MessageRouter`
- `message.transport.TransportRegistry`
- `message.identity.IdentityResolver`
- `message.plugin.discover_message_plugins / FileMessageTransport / MessagePluginIssue`
- `provider.factory.create_provider / provider_semaphore_status`