# 插件开发与工具运行机制

本文面向需要创建、修改或排查 `plugins/` 工具插件的开发者。它说明插件如何被发现、
`SKILL.md` 如何同时承担操作说明和工具清单、Provider 工具循环如何执行，以及插件应如何
处理超时、取消、结果大小和权限边界。

Provider 返回工具调用是否完整、Chat/Kemo 两条协议怎样判断可执行终态，另见
`provider-tool-call-safety.md`。本文从插件注册和运行时执行角度补充该文档。

## 位置与发现

可执行工具只从仓库根目录的直接子目录发现：

```text
plugins/
└── <tool_name>/
    ├── SKILL.md
    └── tool.py
```

发现入口为：

- `plugins/manifest.py::discover_plugin_manifests()`；
- `run/tools.py::discover_tools()`。

运行时只扫描 `plugins/*/SKILL.md`。共享技能、用户技能、拓展、感知和子代理即使包含
Python 文件，也不能借此注册 Provider function call。

插件的三项名称必须一致：

1. `SKILL.md` 一级标题；
2. 插件目录名；
3. Tool JSON 中的 `name`。

例如目录为 `plugins/example_tool/` 时，标题和工具名都必须是 `example_tool`。任一不一致，
插件发现阶段就会失败，而不是等到调用时再猜测。

`SKILL.md` 必须只包含一个 `## Tool` 标题，并在该标题下提供 JSON 代码块。`entrypoint`
必须使用 `file.py:function` 形式，文件位于插件目录内，不得使用绝对路径或 `..` 跳出目录。

## SKILL.md 的双重职责

`SKILL.md` 包含两个不同层面：

- `## Tool` 之前的说明文本：进入主智能体插件说明，告诉模型何时以及怎样使用该工具；
- `## Tool` 中的 JSON：解析为 Provider 工具 Schema 和本地执行入口。

说明文本不是可执行代码。Tool JSON 也不会替代操作手册。开发插件时应同时维护两者：
Schema 负责机器可验证的输入边界，说明文本负责使用顺序、授权条件、失败处理和风险提示。

## Tool JSON 合同

最小 Tool 定义包含：

```json
{
  "name": "example_tool",
  "description": "执行一个受限示例操作。",
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string"}
    },
    "required": ["text"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```

字段含义：

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 是 | Provider 工具名；必须等于目录名和一级标题 |
| `description` | 是 | 发给 Provider 的简短工具说明 |
| `input_schema` | 是 | 根节点必须是 object JSON Schema |
| `version` | 是 | 插件自身版本字符串 |
| `enabled` | 是 | `false` 时保留清单但不进入可执行工具集合 |
| `entrypoint` | 是 | 插件目录内的 `file.py:function` |
| `strict` | 否 | Provider 结构化参数严格模式，默认 `false` |
| `timeout_policy` | 否 | `argument_or_default` 或 `agent_runtime` |
| `timeout_grace_seconds` | 否 | 显式 `timeout` 的外层看门狗清理宽限，默认 0、范围 0～30 秒；只用于整理正常结果，不得增加业务等待时长 |
| `execution_mode` | 否 | `process`（默认）在独立子进程执行并可在超时后终止；只有必须访问进程内对象的插件才声明 `thread` |

`process` 模式只向子进程传递可序列化的运行上下文，并重新加载清单声明的入口；超时或用户取消会终止整个隔离进程树。插件若依赖 Transport 注册表、进程内连接池或其他不可序列化对象，必须显式声明 `thread`，并实现对 `context.cancel_event` 的协作取消。不要仅为减少启动开销改成 `thread`：线程无法被 Python 安全强杀，超时后只能由看门狗限制残留数量和同调用重入。

### strict 的边界

`strict=true` 只有在整个参数 Schema 都满足目标 Provider 的严格结构化输出子集时才可使用。
这包括所有嵌套 object：它们也必须明确 properties、required 和
`additionalProperties: false`。

含开放对象、自由扩展参数或大量可选字段的普通工具应保持 `strict=false`。网关和客户端不应
偷偷修改插件 Schema 来迎合严格模式。结构化输出工具与普通业务工具是不同场景。

### timeout_policy 的边界

默认策略是 `argument_or_default`：

- 工具 Schema 明确声明 `timeout`，且调用参数提供该字段时，使用调用值；
- 否则使用 `config/global_config.json → tools.timeout`，当前默认 240 秒。

`agent_runtime` 只用于 `subagent_dispatch` 这类自身管理子代理整体期限的调度工具。它使用
子代理整体期限并增加外层看门狗宽限，避免普通工具超时先于子代理运行时。普通插件不得借该
策略无限延长执行时间。

少数工具会在内部等待期限恰好到达时返回正常的 `timeout` 业务结果。此类工具可以声明很短的
`timeout_grace_seconds`，使框架外层看门狗采用“显式 timeout + 清理宽限”；传入插件的业务参数
和等待上限本身不变。该字段不得超过 30 秒，也不能用于延长实际工作或轮询时间。

内置 `wait_for_condition` 是这一边界的参考实现：它不启动后台任务，只在 1～7200 秒内等待
固定时长、进程退出、路径出现/消失/变化或 TCP 端口打开/关闭。条件满足会提前返回；到达上限
返回正常的 `status=timeout`，并利用很短的清理宽限让业务结果先于框架外层看门狗落地。

## action 参数约定

框架没有强制所有插件都使用 `action`。是否采用 action 分发属于插件自己的合同。

已有插件常用模式包括：

- `file`：`stat/read/read_range/write/edit/search/...`；
- `memory_manage`：`list/get/search/upsert/...`；
- `subagent_dispatch`：`list/create/call/status/cancel`；
- `task_plan`：计划及步骤状态操作；
- `network`、`multimodal`：按能力声明选择网络或媒体动作。

采用 action 时，应把允许值写入 enum，并在说明文本中列出每个 action 的必填字段、是否写入
外部状态、是否需要确认以及重试边界。不能只声明一个开放字符串，再让入口函数自行猜测。

## Python 入口

与上面的 Tool JSON 对应，最小入口可以写成：

```python
from __future__ import annotations

from typing import Any


def run(text: str, *, context: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": text,
        "user": str(context.get("user") or ""),
    }
```

运行时按函数签名决定是否注入 `context`。插件无需自行解析 Tool JSON，也不能信任模型已经
正确校验参数；`run/tools.py` 会先检查必填字段、顶层额外字段、基础类型和数值上下限，入口
仍应校验业务关系、路径、安全确认和外部资源状态。

同步函数和 async 函数都可作为入口。async 返回值由运行时在独立工具线程中执行。

## 工具循环生命周期

一次正常工具循环依次经过：

```text
插件发现
  → 应用用户插件白名单与运行时策略
  → Tool Schema 发送给 Provider
  → Provider 返回完整工具调用
  → 协议层确认终态和参数 JSON 完整
  → run/tools.py 校验参数
  → 独立工具线程执行入口
  → 结果大小检查
  → 工具结果回填 Provider 历史
  → Provider 继续推理或给出最终回答
```

`tools.max_iterations` 当前统计一轮主对话实际执行的工具调用数量，而不是简单统计 Provider
请求次数。默认值为 80。为了允许执行 N 次工具后再生成最终回答，运行时内部 Provider 循环
上限设为 N+1；该内部上限不改变配置字段的工具调用语义。

一个 Provider 响应可以包含多个工具调用，每个调用分别占用一次额度。达到上限后，尚未执行的
调用标记为未执行，本轮以 `limited / max_tool_iterations` 结束。

子代理还会取全局 `tools.max_iterations` 与该子代理能力声明的上限中的较小值，不能通过子代理
绕过全局工具调用限制。

## 重复调用与连续失败

### 连续相同调用

`tools.consecutive_identical_call_limit` 默认 8。签名由完整工具名和规范化后的完整参数组成。
只有连续、完全相同的调用才累计；工具名或任一参数改变后计数重置。

运行时可对明确允许复用的无副作用结果重放已有结果，但不会把任意写操作都当作可安全复用。
超过限制后调用被阻止，避免模型陷入无意义循环。

### 连续工具失败

`history.consecutive_tool_fail_limit` 默认 5。只有同一工具连续失败才累计；成功或切换工具会
重置连续计数。达到阈值后，该工具从本轮后续 Provider Schema 中临时移除，其他工具仍可使用。

超大结果错误不算普通工具失败，因为工具本身可能已经成功完成，只是结果不能安全进入上下文。

## 超时、取消与线程边界

每次工具调用在独立 `ThreadPoolExecutor(max_workers=1)` 中执行。运行时维护独立取消事件：

- 用户紧急停止时设置工具取消信号并请求取消 Future；
- 工具达到期限时设置取消信号，等待短清理宽限后返回 `ToolTimeoutError`；
- Python 线程不能安全强杀，插件应主动检查 `context["cancel_event"]`，尤其是循环、网络等待和
  大批文件处理。

插件不得用吞掉取消异常、无限重试或启动无人管理的后台线程来绕过工具生命周期。如果任务天然
需要后台状态，应明确设计任务 ID、status/cancel 操作和持久化边界。

## 工具结果大小

`run/tools.py::MAX_TOOL_RESULT_CHARS` 当前为 100,000。结果先序列化为 JSON 字符串再计算字符数。
超过上限时正文完全不回填上下文，而是返回 `ToolResultTooLargeError` 和缩小范围的提示。

常见处理方式：

- 大文件先 `stat`，再使用 `read_range` 分段读取；
- 搜索结果增加目录范围、后缀、数量和分页限制；
- 大型二进制或报告写入 artifact，只返回路径、摘要和校验信息；
- 不应把超大正文截断后伪装成完整成功结果。

## 工具上下文

主智能体调用插件时，运行时可注入：

- `root`、`user`、`source`、`session_id`、`window`；
- `tool_timeout`、`agent_timeout` 和独立 `cancel_event`；
- 当前任务计划 ID/步骤 ID；
- 当前请求允许的知识作用域；
- 已上传文件的安全描述符和多模态传输注册表。

子代理调用插件时还会提供 `caller="subagent"`、`agent` 与 `agent_trigger`。插件只应读取自己
需要的字段，不能假定所有入口都提供相同上下文，也不能把主历史、凭据或绝对内部状态自行塞入
返回结果。

## 权限与白名单

主智能体的可用插件由 `plugins.whitelist` 过滤；空列表表示不额外收缩。`history_search` 还受
`memory.history_read_enabled` 控制。

子代理不继承主智能体工具权限。每个子代理只获得自身 `agent-config.json` 明确允许的插件；
`subagent_dispatch` 不会下发给子代理，避免递归调度链。

## 开发与验证流程

推荐流程：

1. 确定插件是否真的需要可执行 Provider 工具；纯操作说明应使用技能。
2. 建立 `plugins/<name>/SKILL.md` 和薄入口文件。
3. 让标题、目录名和 Tool `name` 保持一致。
4. 用最小、封闭的 Schema 表达输入，不把权限判断交给自然语言。
5. 在入口内实现业务校验、取消响应和安全返回。
6. 使用 `plugins/manifest.py` 和 `run/tools.py` 完成发现测试。
7. 在 `tests/` 中覆盖成功、参数错误、权限拒绝、取消、超时和结果过大。

常用验证：

```powershell
python -m pytest tests/test_extended_plugins.py -q
python -m pytest tests/test_runtime_features.py -q
python -m pytest tests/test_subagent_hotplug.py -q
```

插件清单错误应在发现阶段修复。不要在运行时捕获后静默跳过，否则用户会看到工具偶发消失，
而无法定位目录名、Schema 或入口合同错误。
