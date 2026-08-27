# wait_for_condition

在最长两小时内等待明确条件。适合等待 Shell 受管理后台作业、外部进程退出、文件状态变化或服务端口就绪；条件满足会立即返回，不固定等待到上限。

## 使用原则

1. **框架后台作业优先使用 `job_exit`**：先用 `shell(action=run, background=true)` 启动作业，再用返回的 `job_id` 等待。不要在框架可管理作业时手动搬运裸 PID。
2. **必须显式给出上限**：`timeout` 为最长等待秒数，范围 1～7200。不要无依据地直接使用两小时，应按任务规模选择合理期限。
3. **外部进程需要可靠身份**：无法使用受管理作业、但确实取得外部进程 PID 时使用 `process_exit`。优先同时提供 `process_started_at`；`process_name` 只能作为较弱的辅助核验。任务会写完成文件时使用 `path_exists`，服务启动时使用 `tcp_open`。不要用固定时长代替可验证条件。
4. **可提前唤醒**：除 `duration` 外，条件在首次检查或轮询中满足便立即返回。`check_interval` 默认 5 秒，范围 0.1～60 秒。
5. **超时不是工具故障**：等待到上限仍未满足条件时返回 `status=timeout`。智能体应检查后台任务状态后决定继续等待、诊断或取消，不能宣称任务已经失败或完成。
6. **支持紧急停止**：框架取消信号会中断等待。不要通过无人管理的线程或子进程绕过取消机制。
7. **进程重启边界**：等待调用本身不会在 kemo-agent 重启后自动恢复；Shell 受管理后台作业记录仍可用 `action=status` 查询并继续按 `job_id` 等待。后台作业返回的日志路径以项目根目录为基准，使用 `file` 工具读取时先拼接项目根目录。

## 条件

| condition | 必要参数 | 提前返回条件 |
|---|---|---|
| `duration` | `timeout` | 指定时长结束 |
| `job_exit` | `job_id` | 受管理后台作业进入 completed / failed / cancelled / interrupted |
| `process_exit` | `pid`；建议附 `process_started_at` | PID 对应进程不存在，或 PID 已由另一个进程复用 |
| `path_exists` | `path` | 文件或目录出现 |
| `path_missing` | `path` | 文件或目录消失 |
| `path_changed` | `path` | 存在状态、类型、大小或修改时间相对调用开始发生变化 |
| `tcp_open` | `host`、`port` | TCP 连接成功 |
| `tcp_closed` | `host`、`port` | TCP 连接失败 |

相对 `path` 按 kemo-agent 项目根目录解析；外部程序在其他工作目录写文件时必须传绝对路径。返回的 observation 会同时给出请求路径、解析后路径和解析基准。`path_changed` 不计算完整文件哈希，以免轮询大型文件时造成额外负担。

`process_exit` 的诊断结果：首次检查目标就不存在时返回 `trigger=process_already_absent` 和 `ever_observed_alive=false`；已观察到存活后退出返回 `trigger=process_exit`；PID 存在但创建时间或名称不匹配时返回 `trigger=process_replaced`。若系统拒绝身份查询，`identity_match=null`，工具只按 PID 存在性兼容等待，不会谎称身份核验成功。

## 返回状态

- `triggered`：条件满足或 `duration` 正常结束；查看 `trigger`、`elapsed_seconds` 和 `observation`。
- `timeout`：达到最长等待时间，但目标条件尚未满足；这是正常结果。
- `cancelled`：插件直接观察到取消信号。主运行时也可能把用户紧急停止统一呈现为工具取消事件。

## 示例

等待 Shell 受管理后台作业结束：

```json
{"condition":"job_exit","job_id":"job_0123456789abcdef0123456789abcdef","timeout":7200,"check_interval":5}
```

等待外部进程退出（PID 与创建时间应来自可靠观测）：

```json
{"condition":"process_exit","pid":12345,"process_started_at":"134321965988268845","timeout":7200,"check_interval":5}
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
  "description": "在最长两小时内等待受管理后台作业、外部进程、路径或 TCP 条件。条件满足立即返回；超时返回正常状态；支持紧急停止。",
  "input_schema": {
    "type": "object",
    "properties": {
      "condition": {
        "type": "string",
        "enum": ["duration", "job_exit", "process_exit", "path_exists", "path_missing", "path_changed", "tcp_open", "tcp_closed"],
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
      "process_started_at": {
        "type": "string",
        "description": "process_exit 的强身份字段；应使用进程观测返回的创建时间原字符串"
      },
      "process_name": {
        "type": "string",
        "description": "process_exit 的可选弱身份辅助字段；不能代替创建时间"
      },
      "job_id": {
        "type": "string",
        "description": "job_exit 使用的 Shell 受管理后台作业 ID"
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
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "timeout_grace_seconds": 2
}
```
