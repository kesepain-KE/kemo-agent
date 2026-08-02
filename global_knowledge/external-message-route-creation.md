# 外部消息路由创建文档

外部消息模块把 Telegram 等平台适配到统一消息合同。框架只扫描 `message/out/` 的直接子目录，并且只加载 `message.json` 声明的 input、output、detect 三个 Python 模块。

## 最小框架合同与自由实现

`message/out/<module>/` 是一个完整的平台模块工作区，不是固定文件模板。框架只要求存在 `message.json`，并按清单加载 input、output、detect 三个适配入口，使用清单声明的消息缓冲与附件目录；日志和运行状态统一存入 `runtime/logs.sqlite3`。

这些入口和路径可以使用清单允许的模块内相对路径，不要求平铺在根目录。模块内部可以自由包含平台 SDK 封装、协议实现、数据库、资源、测试、构建文件、任意层级包或完整开源项目。入口可以直接实现极小平台，也可以只作为内部工程的薄适配器。框架不会扫描或自动执行未声明的内部 Python 文件，也不会因模板未列出额外文件而拒绝模块。

根目录 `template/message/` 只是一个面向 Telegram 的适配示例，用于展示合同和生命周期处理，不是所有平台的标准架构，更不是“复制后只改名称”即可适配任意平台。接入其他平台时可以完全替换示例源码，只保留最终合同语义。

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
  "files_dir": "files"
}
```

清单拒绝未知字段。`modules` 必须且只能包含 input/output/detect；所有路径必须是模块目录内的相对路径。`bound_user` 必须是现有内部用户。

`capabilities` 可选值只有 `receive_text`、`send_text`、`receive_file`、`send_file`，并且前两项必需。`allowed_tools=null` 表示不增加 Transport 级工具限制，仍受用户插件配置约束；空数组表示该入口不开放工具。

## 三入口合同

### input.py

```python
def start(config: dict, buffer_path: str, files_path: str) -> None:
    """启动轮询/Webhook，并把消息追加到 buffer_path。"""

def stop() -> None:
    """幂等停止，释放线程、事件循环和网络资源。"""

# 长期运行模块应同时实现以下可选生命周期接口：
def is_alive() -> bool:
    """接收循环仍能继续工作时返回 True。"""

def restart() -> None:
    """确认旧实例完全退出后，使用最近一次启动参数重新启动。"""

def last_error() -> str | None:
    """返回接收循环最近一次顶层异常。"""
```

`start()` 由框架线程调用。平台模块不能自行执行主智能体，也不能在这里实现 `/new`、`/clear`、`/compress` 等核心指令；斜杠消息应原样进入平台中立路由。

仅实现 `start()/stop()` 时，核心通过调用 `start()` 的框架线程判断存活。自行创建后台线程或事件循环的模块必须实现 `is_alive()`，否则 `start()` 返回后核心无法区分“模块在后台正常工作”和“接收循环已经退出”。

核心使用独立监督线程检查输入生命周期，监督不会阻塞 `message.md` 文件队列轮询。输入死亡后按指数退避自动重启；RuntimeHost 停止期间禁止拉起。实现 `restart()` 时，模块必须保证旧长轮询、Webhook、线程和事件循环已经退出，不能产生两个并行消费者。没有 `restart()` 时，核心退回到 `stop()` 后重新调用 `start()`。

自动重启不得清空平台积压消息。平台 SDK 中类似 `drop_pending_updates` 的选项必须关闭，除非用户明确要求丢弃历史积压。网络重试必须设置超时，不能无限阻塞 `stop()`。

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

当前文件队列适配器接受 `private` 和 `group`。正文和附件不能同时为空；`machine_id` 必须匹配清单。附件 `path` 不能为空，必须使用相对消息模块目录的路径（通常为 `files/<安全文件名>`），且解析后必须位于 `files_dir`；声明大小必须与磁盘一致，单文件最大 20 MiB。不要为了阻止清理而把 `path` 写成空字符串，清理由核心在本轮终态后按消息归属执行。

文本附件最多读取 100,000 字符并入请求；其他附件由核心登记为当前 Run 资产，不由平台模块自行转换 Base64 Content Block。资产登记会生成稳定 `asset_id` 并记录路径、类型、大小、校验和、来源和清理归属：主模型明确支持对应模态时才直传，否则把 `asset_id` 提供给 `multimodal` 专用工具。视频、音频、PDF 和普通文件因此与 Web 上传共用同一条能力路由。

平台输入必须从原始附件对象保留准确 MIME 和安全文件名，不能把所有文件降级为 `application/octet-stream`。输入模块只负责下载、原子保存到 `files_dir` 并写入非空相对路径；不能根据主模型名称猜测视觉能力，也不能把路径改写成 Markdown 图片来代替资产登记。

## 运行状态

```json
{
  "schema_version": 1,
  "health": "unknown",
  "last_check": null,
  "last_message_at": null,
  "error": null,
  "latency_ms": null,
  "messages_received_today": 0,
  "messages_sent_today": 0,
  "input_status": "unknown",
  "input_restart_count": 0,
  "input_last_restart_at": null,
  "input_error": null
}
```

计数必须是非负整数。框架把每日计数、检测时间、错误、延迟和输入监督状态写入 `runtime/logs.sqlite3` 的 `message_route_state`；模块私有检测字段进入 `extra_json`。`input_status` 取值为 `unknown`、`starting`、`running`、`restarting`、`stopped`。

状态对象由核心传给 `detect.check()`，检测返回的新字段随标准字段一起事务写入 `message_route_state`。输入模块不接收状态路径，也不得自行维护核心计数。

## 长存活与历史会话

外部历史会话由核心持久化，不依赖平台输入线程的内存状态。活跃绑定键由 `platform + chat_type + external_chat_id` 组成，事务写入 `users/<user>/history/history.sqlite3` 的活跃会话表；框架或输入模块重启后，同一个外部聊天会继续使用原来的 `conv_<uuid>` 会话。

- 私聊按外部聊天 ID 隔离。
- 群聊默认以整个群的聊天 ID 共享一个会话。
- `/new` 关闭当前会话并为同一外部聊天建立新会话。
- `/clear` 只清空当前外部聊天绑定的会话。
- 平台模块不得自行生成或缓存内部 `session_id`，也不得把输入线程重启解释为新对话。

入站幂等键 `<platform>:<message_id>` 也存入同一历史库的 `message_processed_messages`。群聊合批使用单事务领取；任一键已存在时整批拒绝。启动时遗留的 `processing` 只改为 `failed`，不会自动重放可能已经发生的副作用。

`message.md` 是接收与核心路由之间的持久缓冲。平台收到消息后必须先完整追加队列，再确认本地处理成功；文件写入应加锁并保证单条 YAML 块不会被并发写坏。消息计数和 `last_message_at` 由核心领取队列后统一维护，避免与健康检测发生覆盖竞争。

消息入站、出站、附件和失败状态只写入 `runtime/logs.sqlite3` 的 `message_route_logs` 表，网页也只查询该表。日志数据库只保存附件路径和元数据，不保存附件二进制；平台密钥不得写入正文日志。

## 创建流程

1. 确认平台、唯一 `machine_id`、绑定用户和收发能力。
2. 将平台 Token 放入环境变量或模块私有且被忽略的凭据文件，不放进 `message.json`。
3. 建立最小清单合同；按实际复杂度自由组织内部工程，并让三个声明入口适配框架协议。
4. 正确处理文本、命令、群聊、回复 ID、附件 MIME、重名附件和时区。
5. 测试附件非空相对路径、`files_dir` 越界拒绝、资产登记、文本主模型不接收图片数据、连接检测、入站去重、出站失败、输入线程自动拉起、积压消息保留、历史会话跨重启延续和优雅停止。
6. 重新发现模块，检查 Web 外部消息页中的健康状态和日志。

## 安全边界

- 所有外部输入都不可信，不能变成系统提示词或代码直接执行。
- 使用 `allowed_tools` 最小化外部入口权限。
- 不记录 Token、Cookie 或完整凭据；日志中的用户内容和附件也应按隐私数据管理。
- 输入线程、检测和输出都必须有超时，并能在 RuntimeHost 关闭时退出。
- 模块可以保留任意内部工程结构，但声明路径和运行时读写必须留在模块作用域；被入口导入的代码与入口具有相同信任级别，不能加载不可信项目。
