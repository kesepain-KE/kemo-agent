# 编程方案：Cron 执行器透传 TransportRegistry

## 问题

`cron/executor.py` 的 `_execute_claimed_task()` 在构建 `handle_request()` 的 request 字典时，缺少 `_transport_registry` 字段。引擎（`run/engine.py:1596-1598`）从 request 中读取该字段注入工具上下文，缺失导致 `external_message` 工具报错：

```
当前运行上下文未提供外部消息 TransportRegistry
```

### 触发条件
- 任意 `exec_mode: "agent"` 的 cron 用户任务
- 主智能体被唤起后调用 `external_message` 发送消息
- `exec_mode: "subagent"` / `"function"` 不经过 `handle_request`，不受影响

### 已有参考

Web Chat（`web/service.py` stream_chat）正确注入了该字段：

```python
if self._router_ref is not None:
    transport_registry = getattr(self._router_ref, "transports", None)
    if transport_registry is not None:
        request["_transport_registry"] = transport_registry
```

消息路由（`message/router.py:255`）也注入了：

```python
"_transport_registry": self.transports,
```

## 方案

沿调用链逐级透传 `transport_registry`，路径为：

```
RuntimeHost.router.transports
  → CronScheduler.__init__（新增 transport_registry 参数）
    → CronScheduler._scan_user_tasks()（调用时传入）
      → execute_cron_task()（新增 transport_registry 参数）
        → _execute_claimed_task()（新增参数，注入到 request 字典）
```

### 影响范围
| 文件 | 改动 |
|------|------|
| `cron/executor.py` | `_execute_claimed_task()` 和 `execute_cron_task()` 各加一个可选参数 |
| `cron/scheduler.py` | `CronScheduler.__init__` 和 `_scan_user_tasks` 加参数并透传 |
| `run/runtime_host.py` | `build_host()` 中将 `host.router.transports` 传给 `CronScheduler` |
| `cli.py` | `execute_cron_task()` 调用处可传 `None`（已有默认值则无需改） |
| `tests/test_cron.py` | 受影响测试调用的签名可能需要更新 |

## 详细规划

### 步骤 1：`cron/executor.py` — `_execute_claimed_task()`

在函数签名中新增 `transport_registry` 可选参数，在 agent 分支构建 request 时注入。

```python
def _execute_claimed_task(
    *,
    root: Path,
    user: str,
    task: dict[str, Any],
    config: dict[str, Any],
    provider_factory: Callable[[dict[str, Any]], Any],
    tool_registry_factory: Callable[[Path, str], ToolRegistry],
    cancel_event: threading.Event | None,
    transport_registry: Any | None = None,   # ← 新增
) -> dict[str, Any]:
```

在 agent 分支（else 块，约第 161-176 行）修改 request 字典构建：

```python
else:
    background_session_id = str(task.get("session_id") or "").strip()
    if not background_session_id or background_session_id == "cron":
        background_session_id = new_conversation_id()
    request_payload = {
        "user": task["user"],
        "prompt": task["prompt"],
        "source": f"background:cron:{task_id}",
        "session_id": background_session_id,
    }
    if transport_registry is not None:
        request_payload["_transport_registry"] = transport_registry
    handle_request(
        request_payload,
        root=root,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
    )
```

### 步骤 2：`cron/executor.py` — `execute_cron_task()`

新增 `transport_registry` 参数并转发给 `_execute_claimed_task()`。

```python
def execute_cron_task(
    *,
    root: Path,
    user: str,
    task_id: str,
    config: dict[str, Any] | None = None,
    provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
    tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
    cancel_event: threading.Event | None = None,
    system_task: dict[str, Any] | None = None,
    transport_registry: Any | None = None,   # ← 新增
) -> dict[str, Any]:
```

在调用 `_execute_claimed_task` 处传入：

```python
    task = _claim_task(CronStore(root, user), task_id)
    return _execute_claimed_task(
        root=root,
        user=user,
        task=task,
        config=cfg,
        provider_factory=provider_factory,
        tool_registry_factory=tool_registry_factory,
        cancel_event=cancel_event,
        transport_registry=transport_registry,   # ← 新增
    )
```

### 步骤 3：`cron/scheduler.py` — `CronScheduler`

`__init__` 新增 `transport_registry` 可选参数并存储：

```python
class CronScheduler:
    def __init__(
        self,
        root: Path,
        *,
        poll_interval: float = 30.0,
        config: dict[str, Any] | None = None,
        provider_factory: Callable[[dict[str, Any]], Any] = create_provider,
        tool_registry_factory: Callable[[Path, str], ToolRegistry] = discover_tools,
        on_task_executed: Callable[[str, str, dict[str, Any]], None] | None = None,
        on_error: Callable[[str, Exception], None] | None = None,
        transport_registry: Any | None = None,   # ← 新增
    ) -> None:
        ...
        self._transport_registry = transport_registry
```

`_scan_user_tasks` 中调用 `execute_cron_task` 时传入：

```python
result = execute_cron_task(
    root=self.root,
    user=user,
    task_id=str(task.get("task_id") or ""),
    provider_factory=self.provider_factory,
    tool_registry_factory=self.tool_registry_factory,
    cancel_event=self._stop_event,
    transport_registry=self._transport_registry,   # ← 新增
)
```

### 步骤 4：`run/runtime_host.py` — `build_host()`

在创建 `CronScheduler` 时传入 `host.router.transports`。找到 `CronScheduler(...)` 的构造函数调用，添加参数：

```python
cron_scheduler = CronScheduler(
    root,
    poll_interval=30.0,
    config=config,
    provider_factory=provider_factory,
    tool_registry_factory=tool_registry_factory,
    transport_registry=host.router.transports,   # ← 新增
)
```

### 步骤 5：验证点

- `cli.py` 调用 `execute_cron_task` 处已有默认值 `None`，无需修改
- `tests/test_cron.py` 中的测试调用可能因新增参数而需要更新（取决于测试是否使用了 keyword args 还是直接按位置传参——若全用 keyword 传参则兼容）
- `execute_subagent_task` 和 `execute_function_task` 也调用 `_execute_claimed_task`，但它们的 `exec_mode` 不是 `agent`，不经过 `handle_request`，不受影响，无需传入 `transport_registry`

## 应达到的效果

1. 创建一个 `exec_mode: "agent"` 的 cron 定时任务，prompt 要求主智能体调用 `external_message` 向已绑定的 Telegram 用户发送消息
2. CronScheduler 到期触发执行
3. 主智能体调用 `external_message` 工具 → 工具从上下文中拿到 `TransportRegistry` → 找到对应平台的 transport → 成功发送消息
4. 不应引入回归：Web Chat 发消息正常、外部消息入站正常、subagent/function 模式 cron 任务正常
