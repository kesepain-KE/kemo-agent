---
type: component
project: kemo-agent
domain: run
module: run-tools
layer: L2
scope: project
status: active
summary: run/tools.py — 工具发现与执行（支持取消感知 + 连续相同调用检测）
source: "run/run-tools.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, run, 工具, 插件, SKILL.md, manifest]
---
# run/tools.py — 工具发现与执行

`E:\code\kemo-agent\run\tools.py`

## 模块定位

围绕插件工具发现、注册、校验与执行的专题模块。

## 职责

- 扫描插件清单
- 构造 ToolRegistry
- 暴露 provider 工具 schema
- 执行 tool call
- 跟踪连续失败和连续相同调用

## 非职责

- 不定义插件业务本身
- 不负责 provider 协议实现
- 不负责 Web 展示

## 主要入口

- `discover_tools` 扫描注册工具
- `execute_tool` 执行工具调用（含取消/超时）
- `validate_arguments` 参数校验

## 主要数据类型

### ToolCancelledError

```python
class ToolCancelledError(ToolError):
    """The user explicitly cancelled the run while a tool was executing."""
```

当用户在工具执行期间紧急停止时抛出。与普通 `ToolError` 和 `ToolTimeoutError` 区分。

### tool_call_signature

```python
def tool_call_signature(name: str, arguments: dict[str, Any]) -> str
```

返回稳定的工具调用标识：`"{name}:{json.dumps(arguments, sort_keys=True)}"`。用于相同调用检测和去重。

### ConsecutiveIdenticalToolCallTracker

```python
@dataclass(slots=True)
class ConsecutiveIdenticalToolCallTracker:
    limit: int   # 连续相同调用上限，默认 8
    signature: str = ""
    count: int = 0
    
    def record(self, name, arguments) -> int    # 记录调用，返回当前连续次数
    def is_blocked(self, count) -> bool          # 是否超过上限
```

- 以 `工具名称 + 完整参数` 作为调用签名
- 同一签名连续请求超过上限后阻止继续执行
- 工具或参数变化会将连续计数重置为 1

### ConsecutiveToolFailureTracker

```python
@dataclass(slots=True)
class ConsecutiveToolFailureTracker:
    limit: int   # 连续失败上限，默认 5
```

- 同一工具连续失败达到上限后，从 Provider 工具 schema 中临时移除
- 其他工具穿插执行会重置连续失败计数

## execute_tool 取消感知

```python
def execute_tool(tool, arguments, context, timeout, cancel_event=None) -> Any
```

新版支持在工具执行过程中实时响应取消事件：

1. 启动时检查 `cancel_event`
2. 将 `cancel_event` 注入到工具上下文中
3. 在 `ThreadPoolExecutor` 中轮询取消事件，而不是阻塞等待
4. 取消时 `future.cancel()` + 抛出 `ToolCancelledError`
5. 超时使用逐段检查（`min(0.1, remaining)`），而非一次性 wait

## 配置项

| 配置路径 | 默认值 | 说明 |
|---------|--------|------|
| `tools.enabled` | true | 工具功能总开关 |
| `tools.timeout` | 240 | 单次工具调用超时（秒） |
| `tools.max_iterations` | 80 | Provider 工具循环最大迭代次数 |
| `tools.consecutive_identical_call_limit` | 8 | 相同签名连续调用上限（新增） |
| `history.consecutive_tool_fail_limit` | 5 | 同一工具连续失败上限 |

## 依赖

- `plugins/manifest.py` — 插件清单解析
- `provider runtime` — Provider 协议
- 线程池执行环境

## 代码证据

| 关系 | 目标 | 源码路径 | 置信度 |
|------|------|---------|--------|
| calls | plugins/manifest | run/tools.py → discover_tools | high |
| calls | 插件入口 | run/tools.py → execute_tool | high |

## 相关笔记

- [[run-总览]]
- [[run-engine]]
- [[plugins-manifest]]
- [[原理-工具调用]]
