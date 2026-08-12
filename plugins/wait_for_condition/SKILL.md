# wait_for_condition

在最长两小时内等待明确条件。适合在其他工具已启动后台任务后，等待进程退出、文件状态变化或服务端口就绪；条件满足会立即返回，不固定等待到上限。

## 使用原则

1. **先启动后台任务，再等待**：本工具不负责启动、持久化或恢复后台任务。应先取得可靠的 PID、完成标志路径或服务端口，再调用本工具。
2. **必须显式给出上限**：`timeout` 为最长等待秒数，范围 1～7200。不要无依据地直接使用两小时，应按任务规模选择合理期限。
3. **优先使用明确完成信号**：能获得 PID 时使用 `process_exit`；任务会写完成文件时使用 `path_exists`；服务启动时使用 `tcp_open`。不要用固定时长代替可验证的完成条件。
4. **可提前唤醒**：除 `duration` 外，条件在首次检查或轮询中满足便立即返回。`check_interval` 默认 5 秒，范围 0.1～60 秒。
5. **超时不是工具故障**：等待到上限仍未满足条件时返回 `status=timeout`。智能体应检查后台任务状态后决定继续等待、诊断或取消，不能宣称任务已经失败或完成。
6. **支持紧急停止**：框架取消信号会中断等待。不要通过无人管理的线程或子进程绕过取消机制。
7. **进程重启边界**：等待状态仅存在于当前工具调用。kemo-agent 重启后不会自动恢复等待；后台任务是否继续由其自身运行方式决定。

## 条件

| condition | 必要参数 | 提前返回条件 |
|---|---|---|
| `duration` | `timeout` | 指定时长结束 |
| `process_exit` | `pid` | PID 对应进程不存在 |
| `path_exists` | `path` | 文件或目录出现 |
| `path_missing` | `path` | 文件或目录消失 |
| `path_changed` | `path` | 存在状态、类型、大小或修改时间相对调用开始发生变化 |
| `tcp_open` | `host`、`port` | TCP 连接成功 |
| `tcp_closed` | `host`、`port` | TCP 连接失败 |

相对 `path` 按 kemo-agent 项目根目录解析；也支持用户明确提供的绝对路径。`path_changed` 不计算完整文件哈希，以免轮询大型文件时造成额外负担。

## 返回状态

- `triggered`：条件满足或 `duration` 正常结束；查看 `trigger`、`elapsed_seconds` 和 `observation`。
- `timeout`：达到最长等待时间，但目标条件尚未满足；这是正常结果。
- `cancelled`：插件直接观察到取消信号。主运行时也可能把用户紧急停止统一呈现为工具取消事件。

## 示例

等待后台进程退出：

```json
{"condition":"process_exit","pid":12345,"timeout":7200,"check_interval":5}
```

等待结果文件出现：

```json
{"condition":"path_exists","path":"tmp/jobs/result.json","timeout":1800,"check_interval":3}
```

等待本地服务启动：

```json
{"condition":"tcp_open","host":"127.0.0.1","port":8000,"timeout":600,"check_interval":2}
```

固定等待十分钟：

```json
{"condition":"duration","timeout":600}
```

## Tool

```json
{
  "name": "wait_for_condition",
  "description": "在最长两小时内等待后台进程退出、路径出现/消失/变化、TCP 端口打开/关闭或固定时长结束。条件满足立即返回；超时返回正常状态；支持紧急停止。",
  "input_schema": {
    "type": "object",
    "properties": {
      "condition": {
        "type": "string",
        "enum": ["duration", "process_exit", "path_exists", "path_missing", "path_changed", "tcp_open", "tcp_closed"],
        "description": "等待条件类型"
      },
      "timeout": {
        "type": "number",
        "minimum": 1,
        "maximum": 7200,
        "description": "最长等待秒数；显式值同时用于插件期限和框架外层看门狗"
      },
      "check_interval": {
        "type": "number",
        "minimum": 0.1,
        "maximum": 60,
        "default": 5,
        "description": "轮询间隔秒数"
      },
      "pid": {
        "type": "integer",
        "minimum": 1,
        "description": "process_exit 使用的进程 PID"
      },
      "path": {
        "type": "string",
        "description": "path_exists/path_missing/path_changed 使用的相对项目根目录或绝对路径"
      },
      "host": {
        "type": "string",
        "description": "tcp_open/tcp_closed 使用的主机名或 IP"
      },
      "port": {
        "type": "integer",
        "minimum": 1,
        "maximum": 65535,
        "description": "tcp_open/tcp_closed 使用的端口"
      }
    },
    "required": ["condition", "timeout"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "timeout_grace_seconds": 2
}
```
