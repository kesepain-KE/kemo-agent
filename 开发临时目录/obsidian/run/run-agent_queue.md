---
type: component
project: kemo-agent
domain: run
module: run-agent_queue
layer: L2
scope: project
status: active
summary: run/agent_queue.py — 后台串行调度队列（实例级串行锁 + 有界队列）
source: "run/run-agent_queue.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, run, 子代理, 调度, 队列, 串行锁, 有界]
---
# run/agent_queue.py — 后台串行调度队列

`E:\code\kemo-agent\run\agent_queue.py`

## 概览

单工作线程严格串行队列。写入型子代理（background_serial）通过此队列提交，避免并发修改冲突。每个用户拥有独立 AgentScheduler 实例，使用实例级串行锁。

## 关键变更（2026-07-22）

### 1. 实例级串行锁替代进程级锁

旧版使用模块级 `_BACKGROUND_SERIAL_LOCK` 串行所有用户的子代理写入。新版使用 `self._serial_lock`（实例级），每个用户的 AgentScheduler 独立串行，不再跨用户互锁。

### 2. 有界队列默认值

`queue_maxsize` 从 0（无界）改为 50（有界）。通过 `agent_runtime.queue_maxsize` 配置。0 仍表示无界。

### 3. enqueue_inflight 跟踪

新增 `_enqueue_inflight` 计数器，跟踪正在入队的提交者。`close()` 后工作线程在 `_enqueue_inflight == 0` 时才退出。

### 4. submit 默认非阻塞

`submit()` 的 `block` 参数默认从 `True` 改为 `False`，队列满时立即抛 `AgentQueueError`。

### 5. close 改进

不再使用 `_SENTINEL` 对象投递退出信号。改为工作线程以 0.1s 超时轮询队列，检查 `_closed` 和 `_enqueue_inflight` 状态后退出。join 不再持有 `_lock`。

### 6. emit_queued 检查

入队成功后才 emit queued 事件，避免 put 阻塞期间已 cancel 的任务被 emit。

## 类

### 错误体系

```python
AgentQueueError
├── AgentQueueClosedError   # 队列已关闭
├── AgentTaskNotFoundError  # 任务不存在
└── AgentTaskWaitTimeout    # 等待超时
```

### AgentTask

```python
@dataclass
class AgentTask:
    id: str
    agent: str
    input_data: dict
    status: TaskStatus          # queued/running/completed/failed/cancelled
    created_at / started_at / finished_at: str
    timeout / model_override / max_tokens: optional
    result: AgentRunResult | None
    error: dict | None
    cancel_event / done_event: threading.Event
```

### AgentScheduler

```python
class AgentScheduler:
    def __init__(self, runner, *, maxsize=50, event_callback=None, thread_name)
    def from_runner(runner, *, event_callback) -> AgentScheduler
```

**方法**：

| 方法 | 说明 |
|------|------|
| `submit(agent, input_data, *, timeout, model_override, block=False, enqueue_timeout)` | 提交任务 → 返回 task_id |
| `get(task_id)` | 查询任务快照 |
| `wait(task_id, timeout)` | 阻塞等待 → AgentRunResult |
| `cancel(task_id)` | 取消（queued 立即，running 标记） |
| `close(wait, cancel_pending)` | 优雅关闭 |

**状态机**：`queued → running → completed/failed/cancelled`

**约束**：
- 只有 `execution="background_serial"` 的子代理可 submit
- 单线程严格串行（实例级）
- 队列满时拒绝新任务

### _work

```python
def _work(self) -> None
```

工作线程主循环：0.1s 超时轮询队列 → 检查退出条件 → 取任务 → `_serial_lock` 串行 → runner.run() → 设置状态/结果 → done_event.set()。

## 变更记录

| 旧版 | 新版 |
|------|------|
| 进程级 `_BACKGROUND_SERIAL_LOCK` | 实例级 `self._serial_lock` |
| `_SENTINEL` 退出信号 | 0.1s 轮询 + `_enqueue_inflight` 检查 |
| `queue_maxsize` 默认 0 | 默认 50 |
| `submit` block 默认 True | 默认 False |
| `_BACKGROUND_SERIAL_LOCK` 串行所有用户 | 每用户独立 `_serial_lock` |

## 相关笔记

- [[run-agent_runner]]
- [[run-总览]]
- [[provider-factory]]（provider_request_slot）
