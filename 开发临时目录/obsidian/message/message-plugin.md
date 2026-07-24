---
type: component
project: kemo-agent
domain: message
module: message-plugin
layer: L2
scope: project
status: active
summary: message/plugin.py — 文件夹级外部消息插件运行时（FileMessageTransport / Markdown 队列 / 出站附件日志）
source: "message/plugin.py"
updated: 2026-07-21
verified: true
tags: [kemo-agent, message, 插件, FileMessageTransport, Markdown 队列, 附件, 出站附件日志]
---
# message/plugin.py — 文件夹级外部消息插件运行时

`E:\code\kemo-agent\message\plugin.py`

## 概览

实现基于文件夹的 `message/out/<platform>/` 外部消息插件系统。每个插件目录包含 `message.json` 配置、`message.md` 消息队列、`input/output/detect` 三个 Python 模块、`files/` 附件目录和 `log/` 日志目录。RuntimeHost 启动时自动发现并注册这些插件。

## 核心类

### MessagePluginConfig (frozen)

```python
@dataclass(frozen=True, slots=True)
class MessagePluginConfig:
    root: Path
    directory: Path
    machine_id: str
    platform: str
    display_name: str
    bound_user: str          # 绑定到的内部用户
    modules: dict[str, str]  # input/output/detect 模块路径
    capabilities: frozenset[str]
    allowed_tools: frozenset[str] | None
    message_buffer: str     # message.md 相对路径
    files_dir: str
    log_dir: str
    raw: dict
```

**加载**：`MessagePluginConfig.load(root, directory)` — 读取并校验 `message.json`。

**关键属性**：
- `buffer_path` / `files_path` / `log_path` / `state_path`
- `module_path(name)` — 解析 input/output/detect 模块
- `policy()` — 生成 TransportPolicy（含 bound_user）

### FileMessageTransport

```python
class FileMessageTransport:
    # 实现 Transport 协议
    config: MessagePluginConfig
    name: str               # = config.platform
    capabilities: frozenset[str]
    policy: TransportPolicy
```

**核心方法**：

| 方法 | 说明 |
|------|------|
| `start(on_message, on_error)` | 启动 input 线程 + poll 线程 |
| `stop()` | 停止 input/poll 线程 |
| `send(message)` | 调用 output.py.send() 发送出站消息 |
| `request_payload(envelope)` | 从附件构建 Engine prompt/content（图片→base64、文本→直接读取、视频→仅说明） |
| `finalize(result)` | 写日志、清理附件、释放领取文件 |
| `poll_once()` | 扫描领取文件并提交消息 |
| `check_health()` | 调用 detect.py.check() 更新 state.json |

### finalize 日志新增出站附件记录（2026-07-21）

`finalize()` 方法在写入日志 Markdown 时，新增出站附件显示：

```python
if result.outbound is not None and result.outbound.file_path:
    outbound_path = Path(result.outbound.file_path)
    display_path = outbound_path.resolve().relative_to(self.config.root).as_posix()
    pieces.append(f"  - 出站附件：{outbound_path.name} ({display_path})\n")
```

日志格式从仅记录出站消息文本，扩展为同时记录发送的附件文件名和相对路径。

### BufferedMessage / BufferedAttachment (frozen)

队列中的单条消息和附件描述。

### MessagePluginIssue (frozen)

```python
@dataclass(frozen=True, slots=True)
class MessagePluginIssue:
    name: str
    path: Path
    error: str
```

发现阶段的错误记录。

## 关键函数

### parse_message_buffer

```python
def parse_message_buffer(text: str) -> tuple[BufferedMessage, ...]
```

解析 `message.md` 中的 YAML front matter + Markdown 正文消息队列。多条消息通过 `---` 分隔符识别。

### discover_message_plugins

```python
def discover_message_plugins(root: Path) -> tuple[list[FileMessageTransport], list[MessagePluginIssue]]
```

扫描 `message/out/` 直接子目录，返回有效插件列表和失败问题列表。

## 消息合并规则

- **群聊**：同一 `external_chat_id` 的多条消息合并为一个 MessageEnvelope
- **私聊**：逐条处理
- **重复消息**：通过 ProcessedMessageStore 幂等跳过
- 合批消息的 `message_id` 为 `batch_{sha256前24位}`

## 插件目录结构

```text
message/out/<platform>/
├── message.json      # 配置
├── state.json        # 健康状态
├── message.md        # 消息队列
├── input.py          # start(config, buffer_path, files_dir, state_path) / stop()
├── output.py         # send(payload) -> bool
├── detect.py         # check(config, state) -> dict
├── files/            # 附件落地
└── log/              # 按日 Markdown 日志
```

## 依赖

- `yaml` (PyYAML) — 解析 front matter
- `message.schema` — MessageEnvelope / OutboundMessage
- `message.state` — ProcessedMessageStore
- `message.transport` — TransportPolicy / TransportError / TransportRegistrationError
- `run.users` — user_dir 校验

## 代码证据

| 关系 | 目标 | 条件 |
|------|------|------|
| calls | [[message-schema]] | 构造 MessageEnvelope |
| calls | [[message-state]] | 幂等去重 |
| calls | [[message-transport]] | 实现 Transport 协议 |
| called_by | [[run-runtime_host]] | 启动时 discover_message_plugins |
| called_by | [[message-router]] | request_payload / finalize 通过 Transport 接口 |
| called_by | [[web-service]] | MessagePluginConfig.load / check_health（Web 消息模块管理） |

## 相关笔记

- [[message-总览]]
- [[message-router]]
- [[message-transport]]
- [[run-runtime_host]]
- [[原理-消息路由]]