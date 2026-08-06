# kemo-agent 架构概览

本文从开发者视角说明 kemo-agent 如何接收一次请求、构建上下文、调用 Provider、执行工具、
提交历史并驱动后台维护。`agents.md` 是智能体自身的操作手册；本文聚焦代码模块、请求生命周期、
并发隔离和数据流。

## 设计定位

kemo-agent 是本地优先、多用户、事件驱动的 Agent Runtime。它把对话、工具、子代理、记忆、
知识、计划、定时任务、感知、拓展和外部消息连接在同一个用户工作空间中。

核心对话运行时位于 `run/`，不依赖 Web 框架。FastAPI、CLI、外部消息和 Cron 都是入口适配层，
最终复用 `run/engine.py` 暴露的稳定公共门面。

事件是入口与运行时之间的主要通信方式。流式文本、思考、工具调用、工具结果、使用量、错误和
最终状态都表示为 `RunEvent`，Web 将其编码为 SSE，CLI 和消息路由则消费同一事件语义。

## 顶层运行组件

| 目录/模块 | 主要职责 |
|-----------|----------|
| `run/engine.py` | 对话运行时稳定公共门面 |
| `run/conversation_runtime.py` | 一轮对话的主编排、Provider/工具循环、提交和终态 |
| `run/request_input.py` | 用户文本、多模态和上传文件输入规范化 |
| `run/provider_events.py` | Provider 响应事件与终态解释 |
| `run/prompt.py` | 系统 Prompt 的固定顺序拼装和字符预算 |
| `run/prompt_sources.py` | 插件、技能、知识索引、拓展和感知来源发现 |
| `run/history.py` / `run/history_store.py` | archive/runtime 双窗口和历史数据库事务 |
| `run/memory.py` / `run/memory_store.py` | 记忆生命周期、提取模式和 SQLite 门面 |
| `run/tools.py` | 插件发现、参数校验、超时、取消和结果限制 |
| `run/agent_runner.py` | 子代理独立 Prompt、Provider、工具循环和超时存活期 |
| `run/agent_queue.py` | 用户级后台子代理有界队列 |
| `run/module_runtime.py` | 感知/拓展等模块的子进程协议、锁、取消和健康状态 |
| `run/runtime_host.py` | Cron、维护、历史摘要、任务计划和消息路由生命周期 |
| `provider/` | Kemo 原生协议、Chat 兼容桥和统一内部模型 |
| `web/` | FastAPI API、认证、领域路由/服务和 React 前端 |

## 一次请求的完整生命周期

### 1. 入口建立身份

入口把请求规范化为：

- 项目根目录；
- `user`；
- `source`（Web、CLI 或消息平台）；
- `session_id`；
- 用户文本、上传文件和可选运行中引导通道；
- 取消事件及任务计划上下文。

不同入口不能绕过用户身份。历史、记忆、知识、文件和配置都按用户目录或明确的全局/共享层级
解析。

### 2. 进入稳定门面

`run/engine.py` 只转发稳定 API：

- `iter_request_events()`；
- `stream_request()`；
- `handle_request()`；
- `compress_context()`；
- `context_status()`。

复杂逻辑留在领域模块，入口不应直接导入 `conversation_runtime` 的内部函数。

### 3. 获取会话锁并准备历史窗口

`conversation_runtime` 使用项目根目录、用户、来源和会话 ID 组成会话身份，并取得会话级锁。
同一会话的提交串行执行，避免两个请求同时覆盖 runtime 窗口；不同用户和不同会话可并行。

历史数据库同时维护：

- archive：完整、不可因上下文压缩而丢失的归档；
- runtime：当前 Provider 使用的可裁剪上下文窗口。

更早消息按需从 `history_messages` 分页读取，不在 Web 启动或每轮请求时扫描全部历史。

### 4. 合并配置并建立资源策略

`run/config.py` 读取 `config/global_config.json` 和用户 `user_config.json`。框架级运行参数按对象
深合并，Provider、模型和用户资源选择保持用户所有权。

资源策略决定当前主智能体可见的插件、技能、知识层、拓展和感知。子代理不继承该策略，而是
服从自己的 `agent-config.json`。

### 5. 构建 PromptBundle

`run/prompt.py` 按稳定顺序构建 Prompt：

```text
用户人格
→ 全局人格
→ agents.md
→ 全局/用户子代理注册摘要
→ 插件说明
→ 共享/用户技能
→ 三层知识索引
→ 永久记忆
→ 临时重要热视图
→ 半年/月/七天临时记忆
→ 任务计划
→ Expand 数据摘要
→ 感知数据
```

知识库只自动注入索引文件，普通正文按需读取。Kemo Graph 是手动调用的侧载文档站，不替换
本地知识和记忆 Prompt。

### 6. 估算上下文并按需压缩

运行时把固定 Prompt、历史窗口、当前用户输入、工具 Schema、思考和工具结果计入上下文预算。
达到轮次或 Token 条件时，`context_manage` 子代理生成摘要，摘要与 runtime 窗口裁剪在历史库
事务中共同提交。

手动压缩、Token 超限压缩和 API 超限恢复使用同一上下文管理链路，不能通过删除 archive 正文
来腾出窗口。

### 7. 调用 Provider

Provider 内部统一为 Kemo 协议对象：

- `provider/kemo_gateway.py` 访问 Kemo 原生网关；
- `provider/openai_chat.py` 访问 Chat Completions 兼容接口；
- `provider/adapters/compat.py` 在 Chat 与内部 Kemo 结构之间转换。

两条协议都必须在工具执行前确认终态和参数 JSON 完整。Chat 的截断、内容过滤和损坏参数不会
被包装成半个工具调用继续执行。详细规则见 `provider-tool-call-safety.md`。

### 8. 执行工具循环

Provider 返回可执行工具调用后，运行时：

1. 检查本轮工具调用总额度；
2. 检查连续相同调用和连续失败；
3. 从 Tool Registry 获取定义；
4. 校验参数；
5. 在独立工具线程执行；
6. 将成功或结构化错误结果回填消息历史；
7. 再次调用 Provider，直到最终回答或明确终态。

`tools.max_iterations` 当前表示一轮允许执行的工具调用数，默认 80。工具正文序列化后超过
100,000 字符时不会进入上下文，而是返回范围读取提示。完整机制见 `plugin-development.md`。

### 9. 处理运行中引导

Web 运行中引导进入独立 guidance 邮箱。它不会中断正在阻塞的 Provider 或工具，而是在下一次
Provider/工具安全边界取出。

引导可以包含文本、图片、音频、视频和普通文件。资产重新经过用户目录隔离、签名、大小和模型
能力校验；当前 Run 无法接收时，文本和附件作为整体排到下一轮。

### 10. 提交终态

成功、限制、取消和失败都会生成明确终态。提交事务写入：

- archive 和 runtime 窗口；
- 用户/助手消息索引；
- usage、缓存 Token、工具调用次数和耗时；
- 已消费引导的 UI 安全元数据；
- 摘要和记忆提取任务状态。

工具产生外部副作用后，即使最终 Provider 失败，运行时也不能假装工具从未执行。

## 记忆与后台维护

记忆提取时机由 `memory.extraction_mode` 控制：

- `compression_only`：普通提交登记 deferred，在保存或上下文压缩时整理；
- `background`：后台维护器可领取普通 pending 轮次；
- `on_commit`：提交后同步提取；
- `disabled`：关闭自动提取。

历史证据按连续批次交给 `self_improve`，候选成功写入 `memory.sqlite3` 后才推进游标。临时重要
记忆是可重建热视图，不得反向参与三层临时记忆加权。

`RuntimeHost` 管理：

- CronScheduler；
- MaintenanceScheduler；
- HistorySummaryScheduler；
- TaskPlanScheduler；
- 外部消息 MessageRouter；
- 感知和拓展刷新系统任务。

Web 启动入口默认同时启动 RuntimeHost。独立调用核心引擎时，应明确决定是否需要后台宿主。

## 并发与隔离模型

### Provider 总闸

`provider_runtime.max_concurrent_requests` 默认 10，使用进程级 BoundedSemaphore。每次真实 LLM
请求独立占槽；工具执行期间不占 Provider 槽，避免主智能体调用子代理时发生嵌套自锁。

等待超过 `provider_runtime.request_semaphore_timeout`（默认 300 秒）会产生明确拥塞错误。

### 会话级锁

同一用户、来源和会话 ID 串行提交。它保护历史窗口一致性，不把所有用户全局串行化。

### Web 用户闸门

每个用户默认最多 3 个并发 Chat，另有 5 个有界等待位置；等待超过 30 秒返回 503。等待发生
在线程中，不阻塞 FastAPI 事件循环和其他用户 API。

### 外部消息队列

MessageRouter 默认 8 个工作线程和 20 个排队位置。消息幂等状态存入用户历史数据库，路由健康
状态存入 `runtime/logs.sqlite3`。

### 子代理队列

每用户 `AgentScheduler` 默认队列长度 50。后台写入型子代理在用户级串行；不同用户拥有独立
调度器和锁。

子代理默认整体超时 600 秒，超时后默认保留 120 秒收尾存活期。存活期内完成保留结果并标记
`completed_after_timeout`；仍未完成才进入 `timed_out` 或 `timed_out_running`。

### 模块子进程

感知和拓展等模块使用 `run/module_runtime.py` 的 stdin/stdout JSON 协议在独立 Python 子进程中
执行。运行时限制路径、重定向、输出捕获、超时和取消，并使用模块锁避免同一模块并发写状态。

这不是操作系统级安全沙箱。模块仍必须是可信本地代码。

## 数据存储分工

| 路径 | 权威内容 |
|------|----------|
| `users/<user>/history/history.sqlite3` | archive/runtime 窗口、消息索引、上下文摘要、外部消息幂等 |
| `users/<user>/improve/memory.sqlite3` | 四档记忆、权重证据、幂等操作和热视图来源 |
| `users/<user>/task_plan/task_plans.sqlite3` | 计划、步骤和依赖 |
| `runtime/logs.sqlite3` | Cron 和外部消息路由结构化状态/日志 |

配置、模块清单、附件、Cron 定义和插件持久消息队列仍使用文件，不为了形式统一盲目入表。

深入阅读：

- `history-storage.md`；
- `memory-storage.md`；
- `runtime-state-storage.md`；
- `logging-storage.md`。

## Web 分层

`web/app.py` 创建 FastAPI 应用、安装认证/异常处理、注册领域路由，并保留聊天 SSE 和前端托管
边界。

```text
web/app.py
  → web/routes/       HTTP 路由与请求/响应边界
  → web/service.py    兼容服务门面
  → web/services/     按领域拆分的实现
  → web/schemas.py    API 数据合同
  → web/frontend/     React + Vite 前端
```

路由层不应重新实现历史、记忆或模块业务规则。核心数据仍由 `run/` 和对应 Store 管理。

## 更新与重启

`update.py` 按 core、agents、plugins、web 等板块执行更新，并保护用户数据、运行时数据库、配置
和模块派生存储。详细边界见 `version-and-update-modules.md`。

Web 重启由受保护接口启动 `restart.py` 辅助进程：等待旧进程退出后，以兼容启动参数重新拉起
`start_web.py`。`.env` 在新进程启动时重新加载。

更新和重启不是同一操作。更新负责同步代码和依赖，重启负责让新进程加载磁盘状态。

## 开发任务导航

| 目标 | 首选文档 |
|------|----------|
| 开发 Provider 工具 | `plugin-development.md` |
| 创建子代理 | `subagent-creation.md` |
| 创建感知 | `sense-creation.md` |
| 创建拓展 | `expand-creation.md` |
| 创建技能 | `skill-creation.md` |
| 接入外部消息 | `external-message-route-creation.md` |
| 修改历史存储 | `history-storage.md` |
| 修改记忆存储 | `memory-storage.md` |
| 修改配置 | `global-config-reference.md`、`user-config-reference.md` |

修改任何核心链路后，应先运行相关定向测试，再运行完整 `python -m pytest -q`。创建或实质修改
子代理、拓展、消息、感知、技能或用户包时，还必须运行 `tests/template_tests/<kind>/` 对应
合同验收。

