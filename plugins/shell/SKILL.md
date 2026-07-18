# shell

系统命令执行工具。通过 `run_command` action 运行本地命令，支持会话模式、命令链和跨平台输出解码。
无沙箱限制。

## Tool

```json
{
  "name": "shell",
  "description": "执行系统命令。通过 action=run_command 调用，支持 working_dir、stdin、env、timeout 和 session_id。会话模式下 cwd/env/history 按用户隔离并跨调用保留，可用 cd/pwd/export/history，并支持 &&/||/; 命令链。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["run_command"], "description": "固定为 run_command"},
      "command": {"type": "string", "description": "要执行的命令"},
      "working_dir": {"type": "string", "description": "工作目录，默认项目根"},
      "timeout": {"type": "integer", "minimum": 0, "maximum": 3600, "description": "超时秒数，0=使用 context.tool_timeout 或默认 120 秒"},
      "stdin": {"type": "string", "description": "标准输入文本"},
      "env": {"type": "object", "description": "附加环境变量"},
      "session_id": {"type": "string", "description": "会话标识；相同 session_id 共享 cwd/env/history"},
      "reset_session": {"type": "boolean", "description": "是否重置指定 session_id 的状态"}
    },
    "required": ["action", "command"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
