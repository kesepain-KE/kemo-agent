# external_message

外部消息模块插件。可向已经配置并运行的平台主动发送文本或文件；创建新平台模块时，按下方四步流程参考 `template/message/`，不通过工具 action 创建。

### 发送消息

- `send_message`：向指定平台、会话类型和目标 ID 发送非空文本。
- `send_file`：向指定平台、会话类型和目标 ID 发送本地文件。
- 发送前必须确认平台已经在 `message/out/` 配置、绑定当前用户并处于运行状态。

### 创建外部消息平台模块：四步流程

1. **判断是否需要新平台**：先检查 `message/out/`。已有平台换账号应修改现有配置；临时发送消息应使用本工具。只有接入全新平台或现有模块无法满足需求时才新建。
2. **确认基本信息**：与用户确认目录名、显示名称、绑定用户、能力声明和工具白名单。目录名须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`；能力至少包含 `receive_text` 与 `send_text`；`machine_id` 使用唯一 UUID hex。
3. **确认框架合同和实现方式**：读取 `template/message/` 与全局知识中的消息入口合同。模板只是 Telegram 定向参考，不得把它当作其他平台的固定架构；根据 SDK 和真实复杂度决定极小实现、内部模块或完整工程，并确认三个声明入口如何适配。
4. **检查冲突并创建**：确认 `message/out/<platform>/` 不存在，汇总配置并获得用户最终确认后，先建立清单、声明入口和运行路径等最小合同，再使用正常文件或代码工具在模块目录内自由完善实现或迁入现有项目。

### 模块目录不是固定模板

每个 `message/out/<platform>/` 都是自由工作区，可以包含任意文件、任意层级包、SDK 封装、资源或完整开源项目。框架只加载 `message.json` 声明的 input/output/detect 入口；其他文件只有被这些入口导入时才执行。不得因为模板未列出而删除额外文件，也不得把复杂平台的所有代码强行塞进三个入口。

### 创建安全约束

- 目录不得越出 `message/out/`，不得包含符号链接或目录联接。
- `schema_version` 必须为 1；`machine_id` 与 `platform` 必须唯一。
- `bound_user` 必须是已有用户；不得把凭据硬编码进模板文件。
- 自由实现不能绕过声明路径、生命周期、队列、附件、权限和停止合同；入口及其导入代码运行在框架进程信任边界内，只能使用可信代码。
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
