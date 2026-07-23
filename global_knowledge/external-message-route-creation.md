# 外部消息路由创建文档

外部消息模块把 Telegram 等平台适配到统一消息合同。框架只扫描 `message/out/` 的直接子目录，并且只加载 `message.json` 声明的 input、output、detect 三个 Python 模块。

## 标准结构

```text
message/out/<module>/
├── message.json       # 严格静态清单，不放密钥
├── input.py           # start()/stop()，把平台消息写入文件队列
├── output.py          # send(payload) -> True
├── detect.py          # check(config, state) -> state
├── message.md         # 可恢复入站队列
├── state.json         # 健康和计数
├── files/             # 入站附件
├── log/               # 终态日志
└── ...                # 平台自有 helper/config；核心不会自动执行
```

新增模块后需要让 RuntimeHost 重新发现消息插件，通常通过消息模块刷新或重启 RuntimeHost 完成。一个进程内 `machine_id` 和 `platform` 都必须唯一。

## message.json

```json
{
  "schema_version": 1,
  "machine_id": "tg-main-001",
  "platform": "telegram",
  "display_name": "Main Telegram Bot",
  "bound_user": "alice",
  "modules": {
    "input": "input.py",
    "output": "output.py",
    "detect": "detect.py"
  },
  "capabilities": [
    "receive_text",
    "send_text",
    "receive_file",
    "send_file"
  ],
  "allowed_tools": null,
  "message_buffer": "message.md",
  "files_dir": "files",
  "log_dir": "log"
}
```

清单拒绝未知字段。`modules` 必须且只能包含 input/output/detect；所有路径必须是模块目录内的相对路径。`bound_user` 必须是现有内部用户。

`capabilities` 可选值只有 `receive_text`、`send_text`、`receive_file`、`send_file`，并且前两项必需。`allowed_tools=null` 表示不增加 Transport 级工具限制，仍受用户插件配置约束；空数组表示该入口不开放工具。

## 三模块合同

### input.py

```python
def start(config: dict, buffer_path: str, files_path: str, state_path: str) -> None:
    """启动轮询/Webhook，并把消息追加到 buffer_path。"""

def stop() -> None:
    """幂等停止，释放线程、事件循环和网络资源。"""
```

`start()` 由框架线程调用。平台模块不能自行执行主智能体，也不能在这里实现 `/new`、`/clear`、`/compress` 等核心指令；斜杠消息应原样进入平台中立路由。

### output.py

```python
def send(payload: dict) -> bool:
    """发送文本或文件；只有真实发送成功才返回 True。"""
```

`payload` 包含 `chat_type`、`external_chat_id`、`text`、`file_path`、`reply_to`。失败应抛出带平台原因的异常，不能返回伪成功。

### detect.py

```python
def check(config: dict, state: dict) -> dict:
    """执行轻量连接检查并返回更新后的 state。"""
```

健康值只能是 `unknown`、`healthy`、`degraded`、`dead`。检测不能消费用户消息或产生昂贵副作用。

## message.md 队列格式

每条消息使用一组 YAML front matter，正文紧随其后：

```markdown
---
machine_id: tg-main-001
message_id: "12345"
chat_type: private
external_user_id: "123456789"
external_chat_id: "123456789"
timestamp: "2026-07-23T13:00:00+08:00"
attachments:
  - path: files/document.pdf
    name: document.pdf
    mime: application/pdf
    size: 1024
---
请读取这个文件并总结。
```

当前文件队列适配器接受 `private` 和 `group`。正文和附件不能同时为空；`machine_id` 必须匹配清单。附件必须位于 `files_dir`，声明大小必须与磁盘一致，单文件最大 20 MiB。

MIME 决定输入方式：文本附件最多读取 100,000 字符；图片、音频和 PDF 转成内容块；视频与未知普通文件当前只提供说明。平台输入必须从原始附件对象保留准确 MIME，不能把所有文件降级为 `application/octet-stream`。

## state.json

```json
{
  "schema_version": 1,
  "health": "unknown",
  "last_check": null,
  "last_message_at": null,
  "error": null,
  "latency_ms": null,
  "messages_received_today": 0,
  "messages_sent_today": 0
}
```

计数必须是非负整数。框架会维护每日计数、检测时间、错误和延迟；模块可增加私有字段，但不能破坏标准字段类型。

## 创建流程

1. 确认平台、唯一 `machine_id`、绑定用户和收发能力。
2. 将平台 Token 放入环境变量或模块私有且被忽略的凭据文件，不放进 `message.json`。
3. 实现三模块合同和可恢复 `message.md` 写入。
4. 正确处理文本、命令、群聊、回复 ID、附件 MIME、重名附件和时区。
5. 测试连接检测、入站去重、出站失败、重启恢复和优雅停止。
6. 重新发现模块，检查 Web 外部消息页中的健康状态和日志。

## 安全边界

- 所有外部输入都不可信，不能变成系统提示词或代码直接执行。
- 使用 `allowed_tools` 最小化外部入口权限。
- 不记录 Token、Cookie 或完整凭据；日志中的用户内容和附件也应按隐私数据管理。
- 输入线程、检测和输出都必须有超时，并能在 RuntimeHost 关闭时退出。
