# shell

系统命令执行工具。以 subprocess 安全模式运行本地命令，支持会话模式、命令链、跨平台。
无沙箱限制。

## Tool

```json
{
  "name": "shell",
  "description": "执行系统命令。subprocess 安全模式。支持 working_dir、stdin、env、timeout 和 session_id。会话模式下 cwd/env/history 跨调用保留，可用 cd/pwd/export/history，并支持 &&/||/; 命令链。跨平台兼容 Windows/Linux/macOS。",
  "input_schema": {
    "type": "object",
    "properties": {
      "command": {"type": "string", "description": "要执行的命令"},
      "working_dir": {"type": "string", "description": "工作目录，默认项目根"},
      "timeout": {"type": "integer", "description": "超时秒数，0=默认"},
      "stdin": {"type": "string", "description": "标准输入文本"},
      "env": {"type": "object", "description": "附加环境变量"},
      "session_id": {"type": "string", "description": "会话标识；相同 session_id 共享 cwd/env/history"},
      "reset_session": {"type": "boolean", "description": "是否重置指定 session_id 的状态"}
    },
    "required": ["command"]
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
