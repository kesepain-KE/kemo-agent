---
type: component
project: kemo-agent
domain: message
module: message-transport
layer: L3
scope: project
status: active
summary: message/transport.py — Transport 协议与注册（含 bound_user 字段、unregister 方法）
source: "message/transport.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, message, Transport, 协议, 注册, bound_user, unregister]
---
# message/transport.py — Transport 协议与注册

`E:\code\kemo-agent\message\transport.py`

## 协议

### Transport (Protocol)

```python
@runtime_checkable
class Transport(Protocol):
    name: str
    capabilities: frozenset[str]

    def start(self, on_message, on_error) -> None: ...
    def send(self, message: OutboundMessage) -> None: ...
    def stop(self) -> None: ...

    @property
    def running(self) -> bool: ...
```

**InboundCallback** 类型变更：`Callable[[MessageEnvelope], None]` → `Callable[[MessageEnvelope], Any]`（允许返回 future 等对象）。

## 类

### TransportPolicy (frozen)

```python
@dataclass(frozen=True, slots=True)
class TransportPolicy:
    allowed_tools: frozenset[str] | None = None  # null=不限制，空=禁用工具
    capabilities: frozenset[str] = {"receive_text", "send_text"}
    bound_user: str | None = None                 # 新增：直接绑定内部用户
```

`from_dict()` 读取 `bound_user` 字段。文件夹插件使用此字段跳过 IdentityResolver。

### TransportRegistry

```python
class TransportRegistry:
    def register(transport, policy=None) -> RegisteredTransport
    def get(name) -> RegisteredTransport
    def unregister(name) -> RegisteredTransport  # 新增
    def items() -> list[RegisteredTransport]
    def names() -> list[str]
```

同名冲突拒绝，能力声明校验。

### unregister 方法（2026-07-21 新增）

```python
def unregister(self, name: str) -> RegisteredTransport:
    key = name.strip().lower()
    with self._lock:
        item = self._items.pop(key, None)
        if item is None:
            raise TransportRegistrationError(f"未知 Transport：{key}")
        return item
```

用于在消息模块删除前停止并注销对应 Transport。RuntimeHost 的 `remove_message_transport` 方法调用此功能。

### RegisteredTransport

```python
@dataclass(slots=True)
class RegisteredTransport:
    transport: Transport
    policy: TransportPolicy
    state: str = "registered"
    last_error: dict | None = None
```

### MockTransport

```python
class MockTransport:
    name = "mock"
    capabilities = {"receive_text", "send_text"}
    # fail_start / fail_send / emit(envelope) / sent[]
```

内存传输，用于测试和诊断。

## 错误

- `TransportError` / `TransportRegistrationError`

## 变更记录

| 旧版 | 新版 |
|------|------|
| `InboundCallback = Callable[..., None]` | `Callable[..., Any]` |
| `TransportPolicy` 无 `bound_user` | 新增 `bound_user: str \| None = None` |
| `from_dict()` 不解析 bound_user | 解析 `raw.get("bound_user")` |
| 无 `unregister` 方法 | 新增 `unregister(name)` 方法，用于消息模块删除 |