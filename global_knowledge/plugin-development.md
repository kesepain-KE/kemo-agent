# 插件开发与工具运行机制

子代理默认派发：普通公开本地非串行 LLM 子代理 call/resume 省略 wait 默认后台；
显式 wait=true 同步，特殊同步规划、串行维护、自定义执行器与外部代理默认同步。
list.default_wait 披露缺省策略；后台提交仍受必要依赖完成屏障约束，不能当作已完成。

子代理结果与续跑：`subagent_dispatch` 的 call/status/resume 默认仅返回结论、必要证据、
产物和未完成事项，不回传 tool_calls、事件或原始 metadata。工具详情保留在认证网页详情。
本地 LLM 业务任务时间或工具预算耗尽且真实退出后可显式 resume 同 task_id，
每轮重新计时并分配新段工具额度，累计调用/用量保留；私有快照恢复
已配对协议项而不重放已完成操作；仍活着的工具/线程保留屏障且不可恢复。
外部代理、自定义执行器与串行维护不支持此恢复合同，详见 `agent-task-coordination.md`。

本文面向需要创建、修改或排查 `plugins/` 工具插件的开发者。它说明插件如何被发现、
`SKILL.md` 如何同时承担操作说明和工具清单、Provider 工具循环如何执行，以及插件应如何
处理超时、取消、结果大小和权限边界。

Provider 返回工具调用是否完整、Chat/Kemo 两条协议怎样判断可执行终态，另见
`provider-reliability.md`。本文从插件注册和运行时执行角度补充该文档。

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
- `run/tools/__init__.py::discover_tools()`。

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

- 一级标题后、首个真实二级标题或 `---` 前的发现摘要：进入主智能体插件说明，并附作用域和来源路径；其余正文按需读取，不是 `## Tool` 之前的全文自动注入；
- `## Tool` 中的 JSON：解析为 Provider 工具 Schema 和本地执行入口。

说明文本不是可执行代码。Tool JSON 也不会替代操作手册。开发插件时应同时维护两者：
Schema 负责机器可验证的输入边界，说明文本负责使用顺序、授权条件、失败处理和风险提示。

摘要应包含触发场景与关键限制；正文说明前置条件、参数关系、成功证据、副作用和重试边界。匹配任务后先读取来源 `SKILL.md`，不得根据摘要猜测完整操作。统一编写规范和其他模块的职责划分见 `prompt-authoring-standard.md`。

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
受管理 Shell 作业（`job_exit`）、固定时长、外部进程退出、路径出现/消失/变化或 TCP 端口打开/关闭。
任务由 `shell(background=true)` 创建时，应优先使用返回的 `job_id` 等待，不要猜测裸 PID；外部
进程才使用 `process_exit`，并尽量同时提供可靠的进程创建时间。条件满足会提前返回；到达上限
返回正常的 `status=timeout`，并利用很短的清理宽限让业务结果先于框架外层看门狗落地。

受管理 Shell 后台作业每个用户最多同时保留 8 个活动记录。创建新作业前会检查超过 30 秒未更新的 `starting/running/cancelling` 记录：先核对工作进程和管理进程的创建时间身份，再把已经丢失的旧记录收口为 `interrupted/failed/cancelled`，随后重新执行配额检查。30 秒内的新 `starting` 记录不会被抢先对账，避免并发启动尚未登记 worker 身份时被误判。身份不完整或 PID 已复用时仍坚持安全失败，不按裸 PID 终止进程。

作业记录读取仅对 `PermissionError` 做有界重试（最多额外 3 次，退避合计 170 毫秒），
兼容 Windows 原子替换期间的短暂访问拒绝；持续权限拒绝、损坏 JSON 或其他 I/O 错误仍明确失败，
记录不存在仍返回不存在，不把读取失败转换成作业已完成。

#### 后台 Shell 完成通知

框架主/子模型工具上下文为新作业绑定 `root_task_id/owner_task_id`。记录使用
`notification_handled` 持久确认；无绑定的旧记录不自动套用新行为，也不回放全部旧日志。
`label` 为可选短标题（最多 160 字）；`completion_mode=required` 默认为本次交付依赖，
模型尝试交付前等待必要 Shell 到达真实终态；`detached` 用于服务/监听器，不阻塞交付，
但仍受原工具超时上限约束，并在实际结束后保留通知。二者均不扩展工具/路径权限。

`run/tools/background_notifications.py` 的 `ShellInbox` 按用户、来源、对话空间及发起子代理隔离：
主任务接收自己的通知；子代理优先处理自己的通知，结束/超时且真实退出后才允许主任务接管。
当前根任务的 required Shell（含所属子代理启动的作业）仍构成主任务交付屏障。
通知按完成时间及 job_id 稳定排序，每次最多 4 条；只在后续模型请求安全节点输入，
收到可接受模型响应后才确认，失败请求/进程重启不提前标记处理。
当前没有模型运行时仅保留待处理记录，不因 Shell 结束擅自创建模型请求或复活子代理。
通知遵循现有作业保留期，不承诺无限保存。

Worker 在 stdout/stderr 原始分流日志之外维护按采集顺序的有界五行尾部，
每行最多 400 字；这不是操作系统对两个管道的绝对时序保证。UTF-8 增量解码，
必要时回退宿主编码；常见凭据模式脱敏。尾部不受原始日志已达到容量上限影响。
正常排空两路输出后将尾部和完成状态一起写入；采集不完整/异常退出明确保留不完整标记，
不伪造最后五行。通知只含任务归属、状态、开始/完成时间、退出码、停止原因及输出尾部，
不含原始命令、工具卡或完整日志；需要日志时以 job_id 返回的路径主动查询。
退出码 0 不是业务成功证明，通知和日志都是外部数据，不成为用户指令。

后台 worker 捕获 stdout/stderr 时优先使用 `BufferedReader.read1(64 KiB)`，不可用才退回
`read`，让子进程已 flush 的小量输出即时落盘，而非等待整块缓冲或进程退出。
日志仍按流保留原始字节、有容量上限；超限或日志写失败后继续排空管道，避免反压卡死。
网页工作面板只读取当前用户作业记录与有界日志尾部，不触发对账、清理或取消；
UTF-8/宿主编码解码与显示范围见 `frontend-conventions.md` 的“右侧多标签工作面板”。

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
正确校验参数；`run/tools/__init__.py` 会先检查必填字段、顶层额外字段、基础类型和数值上下限，入口
仍应校验业务关系、路径、安全确认和外部资源状态。

同步函数和 `async def` 函数都可作为入口。入口返回可等待对象时，运行时在当前工具执行单元
（`thread` 的独立工具线程或 `process` 的隔离子进程）中新建事件循环，等待一次并取最终结果。
两条路径共用 `run/infra/async_utils.py::run_awaitable`，将返回值包装为原生协程后执行，
兼容 Python 3.10–3.13 的 `asyncio.run()` 参数要求；支持普通协程、自定义 `__await__()`
对象及 `types.coroutine` 生成器协程。同步入口只调用一次，普通同步结果原样进入结果检查。

此桥接不负责迁移其他事件循环中的任务。插件不得返回依赖其他事件循环继续运行的未完成
`Future` / `Task`；应在当前异步执行体内创建和等待这些对象。等待期间的异常、取消和清理
仍沿现有线程/进程边界处理，工具超时及用户取消仍使用运行时注入的 `context["cancel_event"]`。

## 工具循环生命周期

一次正常工具循环依次经过：

```text
插件发现
  → 应用用户插件白名单与运行时策略
  → Tool Schema 发送给 Provider
  → Provider 返回完整工具调用
  → 协议层确认终态和参数 JSON 完整
  → run/tools/__init__.py 校验参数
  → 按 execution_mode 在独立工具线程或隔离子进程执行入口并等待异步结果
  → 结果大小检查
  → 工具结果回填 Provider 历史
  → Provider 继续推理或给出最终回答
```

`tools.max_iterations` 当前统计一轮主对话实际执行的工具调用数量，而不是简单统计 Provider
请求次数。默认值为 80。为了允许执行 N 次工具后再生成最终回答，运行时内部 Provider 循环
上限设为 N+1；该内部上限不改变配置字段的工具调用语义。

一个 Provider 响应可以包含多个工具调用，每个调用分别占用一次额度。达到上限后，尚未执行的
调用标记为未执行，本轮以 `limited / max_tool_iterations` 结束。

子代理未显式指定时取全局 `tools.max_iterations` 与代理默认额度的较小值。
公开本地 LLM 非串行业务子代理可由主智能体通过 call/resume 的 `max_tool_calls`
覆盖本段额度，0 为不限次数；仍受独立 timeout、重复调用、连续失败、取消和 API 池保护。
超额返回 tool_limit / tool_budget_exhausted，保留安全点、结论和用量，显式续跑不重放副作用；
框架内部重试不重置本段计数。`guide` 安全点引导，`split` 显式拆分剩余工作，
预算预警与完整恢复合同见 `agent-task-coordination.md`。

## 重复调用与连续失败

### 连续相同调用

`tools.consecutive_identical_call_limit` 默认 8。签名由完整工具名和规范化后的完整参数组成。
只有连续、完全相同的调用才累计；工具名或任一参数改变后计数重置。

同一逻辑 Run 内，成功且符合 `run/retry/recovery.py::reuse_allowed` 策略的相同签名可以重放，
返回 `duplicate_reused`，不重新执行。该策略会排除 file 的动态读取、expand_call 的
status/query 等动态命令，以及 subagent_dispatch list/status、shell status、
network get/read，避免返回陈旧观测。成功的消息发送、Shell 启动、子任务派发/续跑、
写操作和未知插件仍默认可复用；不能误写成“只有无副作用结果才复用”，
因为禁止复用实际意味着重新执行，并非阻止副作用。
成功回执不证明本次重新执行；也不能为了强制重跑副作用随意改变参数绕过签名。
连续相同调用闸门可能先于复用分支阻止调用，不能假设相同签名永远返回旧结果。
两者的有效范围与错误序列恢复分别见 `run/conversation/tool_batch.py` 和 `run/retry/recovery.py`。

### 连续工具失败

`history.consecutive_tool_fail_limit` 默认 5。只有同一工具连续失败才累计；成功或切换工具会
重置连续计数。达到阈值后，该工具从本轮后续 Provider Schema 中临时移除，其他工具仍可使用。

超大结果错误不算普通工具失败，因为工具本身可能已经成功完成，只是结果不能安全进入上下文。

## 超时、取消与线程边界

线程模式的工具调用在独立 `ThreadPoolExecutor(max_workers=1)` 中执行；进程模式在隔离
子进程中执行，清理宽限后仍未退出则终止进程。两种模式均维护独立取消事件：

- 用户紧急停止时设置工具取消信号；线程模式请求取消 Future，进程模式在短清理宽限后终止仍未退出的进程；
- 工具达到期限时设置取消信号，等待短清理宽限后返回 `ToolTimeoutError`；
- Python 线程不能安全强杀，插件应主动检查 `context["cancel_event"]`，尤其是循环、网络等待和
  大批文件处理。

插件不得用吞掉取消异常、无限重试或启动无人管理的后台线程来绕过工具生命周期。如果任务天然
需要后台状态，应明确设计任务 ID、status/cancel 操作和持久化边界。

## 工具结果大小

`run/tools/__init__.py::MAX_TOOL_RESULT_CHARS` 当前为 100,000。结果先序列化为 JSON 字符串再计算字符数；这是内联回填预算，不是已执行动作的业务失败阈值。
超过预算且存在有效 root/user 上下文时，完整结果由 `run/tools/result_artifacts.py` 保存，返回
有界回执 `execution_completed=true`、`content_omitted=true`、原结果字符数与 artifact 路径/校验信息。
原 `ok=false` 仍是业务失败；`execution_completed` 只说明调用已执行完，不证明业务成功。
需要正文时按 artifact.path 分页核对，不重复执行原动作。

缺少有效上下文、完整结果文件无法落地时仍抛 `ToolResultTooLargeError`；它不计普通连续失败，
但工具可能已产生副作用，仍须先核对状态，不得沿用“缩小范围后重试”的通用提示。
expand 不另设 256k 事后失败阈值，使用同一回传边界。

常见处理方式：

- 大文件先 `stat`，再使用 `read_range` 分段读取；
- 搜索结果增加目录范围、后缀、数量和分页限制；
- 大型二进制或报告写入 artifact，只返回路径、摘要和校验信息；
- 不应把超大正文截断后伪装成完整成功结果。

### 失败与模型纠错状态

结果级 `ok=false` 和异常都按相应分类处理；失败不存成功缓存，已有签名缓存会被清除。
`limited` 表示本轮额度不足，`temporarily_unavailable` 表示连续失败达到系统阈值，
`identical_call_blocked` 表示连续相同签名超限；它们不能统一解释成成功。
`duplicate_reused` 是既有成功结果复用；`retry_reuse_blocked` 是恢复阶段保留的拒绝回执，
两者都不证明本次执行。查看外层 ok、error 和具体 status，再决定调整方案或对账。

`tools.invalid_tool_arguments_retries` 仓库配置为 2，由模型纠正不合法调用参数；
这不是无条件重跑已失败的副作用工具。来源为 `config/global_config.json` 和模型循环的参数恢复管线。
手册内的“两次分析/三次停止”是行为纪律，系统 `history.consecutive_tool_fail_limit` 是独立的配置闸门。

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

`history_search` 的关键词搜索、归档日期目录、物理归档列表与正文读取共用同一个工具注册和权限门；
新增 `action` 不得拆出绕过白名单/开关的入口。`dates/list` 只返回归档元数据并分页，
`read` 以 list 的 `archive_id` 定位单个物理归档、用 `next_cursor` 续读有界正文；不得把 session_id
当作唯一物理归档标识，也不得允许调用方传文件路径。兼容旧调用时省略 action 仍执行 search。
详细日期口径、消息可见范围与参数合同见 `plugins/history_search/SKILL.md`。

子代理不继承主智能体工具权限。每个子代理只获得自身 `agent-config.json` 明确允许的插件；
`subagent_dispatch` 不会下发给子代理，避免递归调度链。

## 三种多媒体输入方式与 attach_media

三种方式并存，不互相替代：

| 方式 | 触发与交付 |
|------|------------|
| 用户附件直接输入 | 用户上传或外部消息附件；依据当前模型能力和视觉路由进入首轮请求 |
| 专用 `multimodal` | 智能体调用专用模型分析、转写或生成媒体，得到分析结果或产物 |
| 工具装载 `attach_media` | 智能体把已取得的文件登记到当前 Run；下一次主模型请求原生携带，不另开分析模型 |

“下一次”指**当前 Run 的下一次 Provider 调用**，不是下一条用户消息，也不是任务计划队列。
典型流程是：下载/截图/生成文件 → 工具返回路径 → `attach_media` → 本批工具结果全部回填
→ 下一次模型请求。若同批调用先装载、后下载，文件尚未取得时会失败；不能引用未来工具结果。

工具支持 `asset_ids`、`paths`、`note`、`detail`（auto/low/high）：

```json
{
  "asset_ids": ["asset_example"],
  "note": "结合这张图片检查界面布局",
  "detail": "auto"
}
```

每次合计 1–8 项，Run 内最多 32 个不同媒体、总大小最多 1 GiB；单文件仍受统一资产限制：
图片 20 MiB、音频 100 MiB、视频 1 GiB、普通文件 100 MiB。文件扩展名、签名和图片解码
必须通过统一校验。重复内容按校验和与 MIME 去重，保留第一次登记的用途说明和 detail。

### 宿主与隔离进程的分工

插件入口仍按默认 `process` 隔离执行，只返回 `media_input_request` **意图**，不修改
`context`、全局变量或跨进程队列。主循环／子代理宿主验证内置目录、工具名称、入口和意图
与原调用参数的一致性后，才登记 Run 级队列。其他工具返回形似 JSON 不能触发装载。

`asset_ids` 只能引用当前 Run 已登记资产。本地 `paths` 必须属于以下来源之一：
当前用户 `download/file_upload` 内的文件、用户本轮明确给出的路径、本轮此前成功工具
结果报告的路径。工具参数或失败结果不构成路径授权。禁止远程 URL、跨用户路径、
符号链接及目录联接（包括父目录）；重发前再次验证存在性与内容校验和。
外部消息资产仍保留来源模块与绑定用户验证，不因换成路径就降低来源校验。

队列不继承到下一用户轮次或其他会话；子代理拥有自己的队列，仅接收其显式输入及本轮
取得的文件，不继承主会话媒体。子代理必须在自身 `agent-config.json` 插件白名单授权
`attach_media`；没有因此扩大任何内置子代理的默认权限。模型 override 按实际运行模型
能力检查，不能借主模型的能力查询结果放行。

### 原生交付与状态

Chat 通道仅支持已声明能力的 JPEG/PNG/WEBP/GIF 图片，编码为请求的原生图片块；
Chat 图片通过专用内部 `ChatImageContent` 按统一的 20 MiB 本地上限校验，保留原图字节、
MIME、SHA-256 和 detail，不静默缩放或转码；上游厂商仍可能有更小的独立限制。
用户直接附件、运行中引导、主对话及子代理的工具装载使用同一边界。
该内部模型不扩展 Kemo wire Schema：默认 `ImageContent` 仍限制内联图片为 1 MiB，
原生 Kemo Provider 边界再次拒绝超过此限制的 Chat 内部实例；Kemo 大图继续使用 Asset。
Kemo 使用现有 Asset 上传／就绪接口，并发送图片、音频、视频或文件内容块。所有媒体
分别检查能力；视觉路由选择 dedicated 时明确拒绝，不暗中调用 `multimodal`。
模型图片数量限制计算整个请求（包括用户附件与运行中引导），不只计算队列。

工具回执为 `status=queued`、`attachment_id`、`asset_ids`、安全描述符、
`effective_from=next_provider_request`、`delivery=native_main`。它只证明登记成功。
宿主将媒体投影为带工具来源说明的补充输入消息，放在完整工具调用/结果批次之后。
`note` 与媒体内容均为外部数据，不是用户新指令，更不是 system 指令。

运行诊断的 `media_inputs` 只保存安全元数据、调用来源与状态：

- `queued`：登记成功，尚未准备请求。
- `prepared`：媒体已验证并构建，尚未接受对应 Provider 的完整有效响应。
- `included`：携带媒体的请求已获得完整有效响应；**不证明模型正确理解了媒体**。
- `failed`：文件失效、变化或不可恢复的上传失败等；明确终止本次交付，不静默回退。

准备请求时的内容模型 `ValidationError` 转换为脱敏 `AttachmentError`，并将对应媒体
更新为 `failed`；不把包含 Base64 的原始校验输入写入界面或媒体诊断。
重新准备时模型能力或视觉路由变化导致的拒绝，也必须更新媒体失败状态。

### 网页只读联动

成功回执为当前用户 `download/file_upload` 提供安全的 `scope/relative_path/available`，
不暴露任意本机绝对路径；宿主审计与 `media_input_update` 事件携带相同投影，
正常结束/取消的 `done` 与已提交失败的 `error` 均携带最终 `media_inputs`；
暂态重试错误不冒充终态，不把尚待重试的媒体标成未发送。
网页工具卡与“相关文件”据此显示媒体和状态，点击后在右侧工作面板预览，不触发新模型调用。
文件刷新恢复、旧回执定位、终态未发送及历史交付未确认的展示约定，
见 `frontend-conventions.md` 的“原生媒体工具与相关文件联动”。
该联动不改变队列生命周期、模型能力、路径授权或媒体预算，不复制原文件。

队列在同一逻辑 Run 的网络／运行级重试中复用；上下文压缩保持当前轮媒体消息与工具
结果完整配对，重建时按稳定标识恢复，不重复叠加。Kemo 上传缓存绑定地址、账户、
模型、用户、资产与校验和；未过期成功上传不重复执行，过期后换幂等键重新上传。
已经由用户附件原生传入的资产不再上传或追加一次。

媒体在本 Run 后续请求中继续可用，Run 结束即释放队列；不将 Base64 或完整媒体输入
消息写入长期历史。持久历史中的工具回执只是元数据，下一用户轮次需要再看时必须重新
登记。请求预算使用现有上下文保护及每个媒体块至少 1024 Token 的本地估算，**不是厂商
精确计费或可解码保证**；超预算先按既有规则压缩历史，仍放不下则明确受限停止。暂时网络
错误保留待重试队列，不伪报 included；文件和能力错误也不会自动变成专用模型分析。

## 开发与验证流程

推荐流程：

1. 确定插件是否真的需要可执行 Provider 工具；纯操作说明应使用技能。
2. 建立 `plugins/<name>/SKILL.md` 和薄入口文件。
3. 让标题、目录名和 Tool `name` 保持一致。
4. 用最小、封闭的 Schema 表达输入，不把权限判断交给自然语言。
5. 在入口内实现业务校验、取消响应和安全返回。
6. 使用 `plugins/manifest.py` 和 `run/tools/__init__.py` 完成发现测试。
7. 在 `tests/` 中覆盖成功、参数错误、权限拒绝、取消、超时和结果过大。

常用验证：

```powershell
python -m pytest tests/plugins/test_extended_plugins.py -q
python -m pytest tests/core/test_runtime_features.py -q
python -m pytest tests/agents/test_subagent_hotplug.py -q
```

插件清单错误应在发现阶段修复。不要在运行时捕获后静默跳过，否则用户会看到工具偶发消失，
而无法定位目录名、Schema 或入口合同错误。
