# 会话级长任务运行合同

## 定位

长任务模式解决的是“一个用户任务需要的工具调用超过单个 Run 的安全上限”这一问题。它不会取消
`tools.max_iterations`，也不会把一次运行无限拉长；每个 Run 仍然独立遵守工具、上下文、Provider
和取消边界。只有用户为当前对话空间显式授权后，Web 编排层才可以在命中单 Run 工具上限时提交
当前 Run，并在同一会话锁内开启下一个 Run。

长任务状态严格绑定 `(user, source, session_id)`，保存在该用户现有历史库的
`history_sessions.record_json.long_task` 中，不写入 `global_config.json` 或 `user_config.json`。
因此，同一用户的两个对话空间互不影响，`source=web` 与 `source=app` 也互不共享开关或运行状态。
新会话默认关闭。

## 续跑边界

只有同时满足以下条件时才允许自动续跑：

1. 当前会话的 `long_task.enabled=true`；
2. 当前请求不是任务计划执行请求；
3. 当前 Run 的终态为 `status=limited`；
4. `stop_reason=max_tool_iterations`；
5. 整个逻辑任务没有收到取消请求，并且没有达到 128 个 Run 的内部硬上限。

上下文保护终态、Provider 失败、工具上下文耗尽、任务计划等待批准、普通取消及其他错误均不会自动
开启下一 Run。每个受限 Run 会先完整提交历史；内部续跑提示使用 synthetic metadata，历史界面只
显示“长任务自动续跑”边界，不把它冒充成新的用户消息。记忆提取和历史摘要使用最初的用户请求
替代内部控制提示，避免控制文本污染长期语义。

## 状态与控制语义

公开状态包括：

| 状态 | 含义 |
|---|---|
| `disabled` | 当前会话未授权长任务模式 |
| `enabled` | 已授权，但当前没有跨 Run 长任务正在执行 |
| `running` | 逻辑长任务正在运行 |
| `pausing` | 用户已关闭开关；当前 Run 可自然收束，但不得再开启下一 Run |
| `paused` | 关闭开关后当前 Run 再次触及工具上限，逻辑任务暂停 |
| `cancelling` | 已请求取消，正在终止当前 Run |
| `cancelled` | 整个逻辑长任务已取消 |
| `completed` | 最终 Run 正常完成 |
| `failed` | Run 或编排器失败 |
| `interrupted` | 运行被进程退出等外部边界中断 |

关闭开关不会强行中断正在执行的工具或 Provider 请求。当前 Run 正常完成时状态为 `completed`；
当前 Run 再次触及工具上限时状态为 `paused`。取消操作则面向整个逻辑长任务，会设置当前 Run 的
取消事件并关闭运行中引导邮箱。

## HTTP API

三个接口都必须携带真实来源。省略 `source` 时只为 Web 兼容而默认为 `web`；APP 客户端必须显式
使用 `source=app`。

### 查询状态

```http
GET /api/users/{user}/sessions/{session_id}/long-task?source=web
```

### 开启或关闭

```http
PUT /api/users/{user}/sessions/{session_id}/long-task?source=web
Content-Type: application/json

{"enabled": true}
```

### 取消整个逻辑任务

```http
POST /api/users/{user}/sessions/{session_id}/long-task/cancel?source=web
```

统一响应结构：

```json
{
  "user": "kesepain",
  "source": "web",
  "session_id": "conv_xxx",
  "long_task": {
    "enabled": true,
    "status": "running",
    "task_id": "long_task_xxx",
    "original_prompt": "用户最初的任务",
    "started_at": "2026-08-13T12:00:00+00:00",
    "updated_at": "2026-08-13T12:10:00+00:00",
    "finished_at": "",
    "run_count": 2,
    "continuation_count": 1,
    "total_tool_calls": 96,
    "total_provider_requests": 43,
    "active_elapsed_ms": 600000,
    "usage": {},
    "current_run_id": "run_xxx",
    "last_stop_reason": "max_tool_iterations",
    "cancel_requested": false,
    "last_error": null
  }
}
```

接口只接受已经存在的会话；不能为了打开开关而隐式创建一个来源不明的会话。

## SSE 事件合同

底层 Run 达到工具上限但准备继续时，服务端发送非终态事件：

```json
{
  "type": "long_task_update",
  "metadata": {
    "terminal": false,
    "long_task": true,
    "next_run_id": "run_next",
    "run_id": "run_next",
    "long_task_state": {}
  }
}
```

客户端收到事件后必须：

1. 不把该事件视为本轮对话的最终 `done`；
2. 用 `next_run_id` 更新当前活动 Run ID，后续运行中引导和当前 Run 紧急取消才能指向新 Run；
3. 将上一段流式正文、思考和工具调用收束为非流式状态；
4. 插入一条窄的续跑边界提示，再接收下一 Run 的助手输出；
5. 用 `long_task_state` 刷新原始任务、累计耗时、Run 数、工具调用、Provider 请求和 Token 统计。

中间 Run 不发送终态 `done`。整个逻辑任务最终只发送一次 `done` 或 `error`；最终事件的
`metadata.long_task_state` 是权威终态。客户端重新连接或切换会话时，应通过查询接口恢复状态，
不能只依赖内存中的 SSE 事件。

## 与上下文压缩的关系

跨 Run 续跑不会绕过既有上下文选择和压缩。每个新 Run 都按照正常会话历史重新选择上下文；达到
轮次或 Token 阈值时仍由 `context_manage` 生成摘要。运行时通过非终态 `context_compression` 事件
报告 `started`、`ready` 或 `failed`，Web 在发送框上方显示压缩状态。

`ready` 只表示摘要已经可以用于当前请求。默认队列策略下，裁剪轮次的记忆分析在本轮提交后由后台
继续处理；只有 `memory_processed_round` 追平 `memory_target_round` 才表示记忆分析完成。分析完成
可以合法地产生零个新碎片，不能仅凭“没有新增文件”判断链路失败。

## 客户端实现边界

- Web 已提供会话操作菜单开关和输入框上方的长任务状态气泡。
- APP 通过 `global_expand/kemo_app` 的认证桥接接入上述核心 API 与 SSE 合同。桥接层可以把手机
  SSE 降级为可断开的订阅者，并提供运行快照/游标恢复，但不得改变本节的会话状态机、重复提交
  原始请求或把手机断线映射成 Run 取消。
- 普通 Run ID 取消只针对当前内部 Run；长任务气泡上的“停止整个任务”应调用会话级 cancel API。
- 开关是用户授权，不是模型判断结果。模型不得在用户关闭时替用户打开长任务模式。
