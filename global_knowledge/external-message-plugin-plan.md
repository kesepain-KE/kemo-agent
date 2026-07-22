# external_message 插件 · 编程方案

## 问题

kemo-agent 的 `message/` 已有完整的路由+传输层，但缺少 agent 可调用的插件。智能体无法主动发消息、发文件，也无法创建新的外部消息平台模块。

## 方案

创建 `plugins/external_message/` 插件，混合工具型 + 指令型：

- **工具型**：`send_message` / `send_file` — 有 `tool.py` 实现，可 function call
- **指令型**：`create_platform` — SKILL.md 引导智能体读 `template/` 模板后手动创建，不提供创建工具

---

## 详细规划

### 第一步：创建消息平台模板 `template/message_platform/`

```
template/message_platform/
├── message.json        # 平台配置骨架
├── input.py            # 消息接收适配器骨架
├── output.py           # 消息发送适配器骨架
└── detect.py           # 健康检测骨架
```

#### `message.json` 骨架

```json
{
  "schema_version": 1,
  "machine_id": "{{MACHINE_ID}}",
  "platform": "{{PLATFORM}}",
  "display_name": "{{DISPLAY_NAME}}",
  "bound_user": "{{BOUND_USER}}",
  "modules": {
    "input": "input.py",
    "output": "output.py",
    "detect": "detect.py"
  },
  "capabilities": ["receive_text", "send_text"],
  "allowed_tools": null,
  "message_buffer": "message.md",
  "files_dir": "files",
  "log_dir": "logs"
}
```

占位符：`{{MACHINE_ID}}` `{{PLATFORM}}` `{{DISPLAY_NAME}}` `{{BOUND_USER}}`，由智能体按 SKILL.md 指令替换。

#### `input.py` 骨架

```python
"""{{PLATFORM}} 消息接收适配器。

start() 在传输启动时调用，负责持续接收消息并写入 buffer 文件。
"""

from __future__ import annotations

from typing import Any


def start(raw_config: dict[str, Any], buffer_path: str, files_dir: str, state_path: str) -> None:
    """启动消息接收循环。

    Args:
        raw_config: message.json 的完整内容
        buffer_path: message.md 缓冲文件的绝对路径
        files_dir: 附件目录的绝对路径
        state_path: state.json 状态文件的绝对路径
    """
    # TODO: 对接实际平台 SDK，接收消息后写入 buffer_path
    # 消息格式参考 message/plugin.py 的 parse_message_buffer() 期望的 YAML front matter 格式
    raise NotImplementedError("input.start() 需要实现")


def stop() -> None:
    """停止消息接收。"""
    pass
```

#### `output.py` 骨架

```python
"""{{PLATFORM}} 消息发送适配器。"""

from __future__ import annotations

from typing import Any


def send(payload: dict[str, Any]) -> bool:
    """发送消息到外部平台。

    Args:
        payload: 包含 chat_type、external_chat_id、text、file_path、reply_to

    Returns:
        True 表示发送成功，False 表示失败
    """
    # TODO: 对接实际平台 SDK，发送消息
    raise NotImplementedError("output.send() 需要实现")
```

#### `detect.py` 骨架

```python
"""{{PLATFORM}} 健康检测适配器。"""

from __future__ import annotations

from typing import Any


def check(raw_config: dict[str, Any], current_state: dict[str, Any]) -> dict[str, Any]:
    """检测平台连接健康状态。

    Args:
        raw_config: message.json 的完整内容
        current_state: 当前 state.json 内容

    Returns:
        更新后的状态字典，至少包含 health 字段
        health 值: unknown / healthy / degraded / dead
    """
    return {**current_state, "health": "unknown"}
```

---

### 第二步：创建 `plugins/external_message/`

```
plugins/external_message/
├── SKILL.md
└── tool.py
```

---

### 第三步：实现 `tool.py`（工具型部分）

两个 tool：

#### `send_message`

```python
def send_message(
    platform: str,
    target: str,
    chat_type: str,
    message: str,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
```

**逻辑**：
1. 从 `context` 获取 `transport_registry`（或 `message_router`）
2. 查 `transport_registry.get(platform)` 拿到 transport
3. 构造 `OutboundMessage`：
   - `platform` = platform
   - `chat_type` = chat_type（private/group）
   - `external_chat_id` = target
   - `text` = message
   - `reply_to` = None（主动消息）
4. 调用 `transport.send(outbound)`
5. 返回 `{"ok": true, "platform": platform, "target": target}`

#### `send_file`

```python
def send_file(
    platform: str,
    target: str,
    chat_type: str,
    file_path: str,
    *,
    context: dict[str, Any],
) -> dict[str, Any]:
```

**逻辑**：
1. 同 `send_message` 获取 transport
2. 校验 `file_path` 存在且为文件
3. 构造 `OutboundMessage`，设置 `file_path` 字段
4. 调用 `transport.send(outbound)`
5. 返回 `{"ok": true, "platform": platform, "target": target, "file": file_path}`

#### context 传递 transport_registry

需要修改 tool 调用链，在 `context` 中加入 `transport_registry`。改动点：

- `run/engine.py` 或 `run/tool.py`：在构造 tool context 时，从 `MessageRouter` 获取 `TransportRegistry` 并注入

如果不想改调用链，备选方案：在 `message/transport.py` 中增加模块级 `_global_registry: TransportRegistry | None`，启动时设置，tool 直接 `from message.transport import _global_registry`。

---

### 第四步：实现 `SKILL.md`（指令型 + 工具型混合）

```markdown
# external_message

外部消息模块插件。提供消息发送工具和平台模块创建指令。

## 工具（可 function call）

### send_message

向指定外部平台发送文本消息。

| 参数 | 说明 |
|------|------|
| `platform` | 平台名：telegram / onebot / ... |
| `target` | 目标 ID：QQ号 / TG 用户 ID / 群号 |
| `chat_type` | private（私聊）或 group（群聊） |
| `message` | 要发送的文本内容 |

使用前确认目标平台已经在 `message/out/` 下配置且运行中。

### send_file

向指定外部平台发送文件。

| 参数 | 说明 |
|------|------|
| `platform` | 平台名 |
| `target` | 目标 ID |
| `chat_type` | private 或 group |
| `file_path` | 本地文件绝对路径 |

---

## 指令：创建外部消息平台模块

### 四步流程（必须遵守）

收到创建平台模块的请求后，不得直接创建文件。必须完成以下四步。

#### 第一步：判断是否真的需要新平台

检查 `message/out/` 下是否已有同名或功能重复的平台模块。已有平台能复用的不改建新的。

适合创建新平台：
- 接入全新外部消息平台（如 Discord、Line、微信）
- 现有平台模块无法满足需求

不适合：
- 已有平台换账号 → 修改现有 `message.json` 的 `bound_user`
- 临时发一条消息 → 直接用 `send_message` 工具

#### 第二步：确认基本信息

向用户确认：

| 字段 | 说明 | 限制 |
|------|------|------|
| 目录名 | 英文，如 `discord` / `wechat` | `^[A-Za-z][A-Za-z0-9_-]{0,63}$` |
| 显示名称 | 人类可读，如 "Discord Bot" | 非空字符串 |
| 绑定用户 | 消息归属的 kemo-agent 用户 | 必须是已有用户 |
| 能力声明 | 从 receive_text / send_text / receive_file / send_file 中选择 | 必须包含 receive_text 和 send_text |
| 工具白名单 | 该平台允许 agent 调用的工具，null = 全部允许 | 字符串数组或 null |

machine_id 由智能体自动生成（uuid hex）。

#### 第三步：确认适配器代码

读 `template/message_platform/` 下的四个模板文件：

| 模板 | 用途 | 需要用户提供 |
|------|------|------------|
| `message.json` | 平台配置 | 占位符替换 |
| `input.py` | 消息接收适配器 | 平台 SDK 对接代码 |
| `output.py` | 消息发送适配器 | 平台 SDK 对接代码 |
| `detect.py` | 健康检测 | 平台连通性检测逻辑 |

向用户展示替换占位符后的 `message.json`，确认无误。

`input.py` / `output.py` / `detect.py` 三个 Python 文件的 `raise NotImplementedError` 需要用户后续自行实现对接逻辑。如果用户能提供平台 SDK 文档或示例代码，智能体可以协助填充。

#### 第四步：检查冲突并创建

1. 列 `message/out/` 确认无同名目录
2. 汇总所有配置向用户最终确认
3. 创建 `message/out/<platform>/` 目录
4. 写入四个文件（`message.json` 占位符已替换）
5. 报告创建结果和下一步操作

### 安全约束

- 平台目录不得越出 `message/out/`
- 不得包含符号链接或目录联接
- `message.json` 的 `schema_version` 必须为 1
- `capabilities` 必须包含 `receive_text` 和 `send_text`
- `machine_id` 必须唯一
- `platform` 名必须唯一
```

---

### 第五步：Tool Schema 定义

```json
{
  "name": "external_message",
  "description": "外部消息模块：发送文本/文件到外部平台，或按指令创建新的平台模块。创建平台模块时请先读 SKILL.md 的指令型流程。",
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
        "description": "目标 ID：QQ号 / TG 用户 ID / 群号"
      },
      "chat_type": {
        "type": "string",
        "enum": ["private", "group"],
        "description": "私聊或群聊"
      },
      "message": {
        "type": "string",
        "description": "send_message 要发送的文本"
      },
      "file_path": {
        "type": "string",
        "description": "send_file 本地文件绝对路径"
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

注意：`create_platform` 不在 action 枚举中——它是指令型，由智能体按 SKILL.md 指引手动完成。

---

### 第六步：修改上下文传递

`run/tool.py` 在构造 tool context 时，增加 `transport_registry` 字段，使 `send_message` 和 `send_file` 能获取对应平台的 transport。

---

## 应达到的效果

1. 智能体可通过 `send_message` 直接发文本到 QQ / Telegram
2. 智能体可通过 `send_file` 直接传文件到 QQ / Telegram
3. 用户说"接入 Discord 机器人"时，智能体按 SKILL.md 四步流程引导用户确认，然后读 `template/message_platform/` 模板创建 `message/out/discord/` 下的四个文件
4. 用户说"给 XXX 发消息"时，智能体直接调 tool，不走创建流程
5. 创建与发送互不干扰，指令型和工具型各司其职
