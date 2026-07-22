# external_message

外部消息模块插件。可向已经配置并运行的平台主动发送文本或文件；创建新平台模块时，按下方四步流程使用 `template/message_platform/`，不通过工具 action 创建。

### 发送消息

- `send_message`：向指定平台、会话类型和目标 ID 发送非空文本。
- `send_file`：向指定平台、会话类型和目标 ID 发送本地文件。
- 发送前必须确认平台已经在 `message/out/` 配置、绑定当前用户并处于运行状态。

### 创建外部消息平台模块：四步流程

1. **判断是否需要新平台**：先检查 `message/out/`。已有平台换账号应修改现有配置；临时发送消息应使用本工具。只有接入全新平台或现有模块无法满足需求时才新建。
2. **确认基本信息**：与用户确认目录名、显示名称、绑定用户、能力声明和工具白名单。目录名须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`；能力至少包含 `receive_text` 与 `send_text`；`machine_id` 使用唯一 UUID hex。
3. **确认适配器代码**：读取 `template/message_platform/` 的 `message.json`、`input.py`、`output.py`、`detect.py`，展示替换占位符后的配置。若用户未提供平台 SDK 文档或示例，三个 Python 适配器保留待实现状态并明确告知。
4. **检查冲突并创建**：确认 `message/out/<platform>/` 不存在，汇总配置并获得用户最终确认后，才创建目录并写入四个文件。

### 创建安全约束

- 目录不得越出 `message/out/`，不得包含符号链接或目录联接。
- `schema_version` 必须为 1；`machine_id` 与 `platform` 必须唯一。
- `bound_user` 必须是已有用户；不得把凭据硬编码进模板文件。
- `create_platform` 是指令流程，不属于本工具的 action。

## Tool

```json
{
  "name": "external_message",
  "description": "向已配置且绑定当前用户的外部消息平台主动发送文本或文件。创建平台模块时请遵循 SKILL.md 的四步指令流程。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["send_message", "send_file"],
        "description": "send_message=发送文本，send_file=发送文件"
      },
      "platform": {
        "type": "string",
        "description": "目标平台名"
      },
      "target": {
        "type": "string",
        "description": "目标用户、群聊或会话 ID"
      },
      "chat_type": {
        "type": "string",
        "enum": ["private", "group"],
        "description": "private=私聊，group=群聊"
      },
      "message": {
        "type": "string",
        "description": "send_message 要发送的非空文本"
      },
      "file_path": {
        "type": "string",
        "description": "send_file 要发送的本地文件绝对路径"
      }
    },
    "required": ["action", "platform", "target", "chat_type"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
