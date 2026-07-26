# expand_call

调用当前用户可用的全局、共享或用户拓展。拓展操作在隔离 Python 子进程中执行；结构化参数通过 stdin 传递，不使用 Shell 拼接。采集状态仍由 `data_update.py` 写入 `input_data.md`，操控结果直接返回当前工具调用，不经过采集文件。

## 使用原则

1. 调用前从拓展注入说明或 `expand_control.md` 操作层确认准确的 `scope`、模块名、命令、参数、副作用和权限要求。
2. `scope` 必须明确填写，不能在三层同名模块之间猜测。
3. 只执行用户明确要求或完成任务必需的外部操作；不可逆操作仍须先确认。
4. 大型 DOM、日志、图片、音视频和二进制结果应由模块保存为 artifact，不得直接塞入 `data`。
5. 操控结果属于当前轮；长期状态由后续采集刷新到 `input_data.md`。不要手工把工具结果写进采集文件。
6. 同一操作失败后先分析错误；不能用 Shell 绕过白名单、路径校验或 `open_control`。
7. 隔离子进程不是权限沙箱；只能调用已获准的受信任拓展，不能把未知代码当作安全插件执行。

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|:---:|------|
| `scope` | string | 是 | `global`、`shared` 或 `user` |
| `module` | string | 是 | 拓展模块目录名 |
| `command` | string | 是 | 操作层声明的命令名 |
| `params` | object | 否 | 命令的结构化参数，默认空对象 |
| `timeout` | integer | 否 | 本次操作超时秒数；省略时使用全局工具超时 |

## Tool

```json
{
  "name": "expand_call",
  "description": "在隔离子进程中调用当前用户获准使用的拓展操作。参数通过 stdin 传递，支持结构化数据和文件产物；不可逆外部操作须先确认。",
  "input_schema": {
    "type": "object",
    "properties": {
      "scope": {
        "type": "string",
        "enum": ["global", "shared", "user"],
        "description": "拓展所在作用域"
      },
      "module": {"type": "string", "description": "拓展模块目录名"},
      "command": {"type": "string", "description": "操作层声明的命令名"},
      "params": {"type": "object", "description": "命令结构化参数"},
      "timeout": {
        "type": "integer",
        "minimum": 1,
        "maximum": 3600,
        "description": "可选超时秒数；默认使用 global_config.json → tools.timeout"
      }
    },
    "required": ["scope", "module", "command"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
