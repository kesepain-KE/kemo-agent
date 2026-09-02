# 配置参考功能说明

本文集中说明 `.env` 环境变量、部署级 `global_config.json` 和用户级 `user_config.json`。配置的默认值、字段校验和实际生效范围以当前加载器和配置 Schema 为准。

## `.env` 环境变量参数说明

项目根目录 `.env` 保存本机启动参数和密钥兜底。无密钥模板是 `.env.example`。框架使用内置解析器读取简单 `KEY=VALUE`，支持空行、`#` 注释、可选 `export ` 前缀以及单/双引号包裹的值。

### 加载与优先级

- 已存在于进程环境中的变量默认优先，`.env` 不覆盖它。
- Provider 密钥优先级：`user_config.json → provider.api_key` > `provider.api_key_env` 指向的环境变量 > 类型默认环境变量。
- Provider 模型优先级：`provider.model` > `KEMO_MODEL`/`OPENAI_MODEL`。
- Provider 地址优先级：`provider.base_url` > 环境变量 > 内置默认地址。
- Web 地址优先级：启动命令显式参数 > `WEB_HOST`/`WEB_PORT` > 内置默认值。

`.env` 只影响当前进程及其子进程。修改后，已启动的 RuntimeHost 通常需要重启才能完整生效。

### Provider

| 变量 | 适用模式 | 默认/说明 |
|------|----------|-----------|
| `KEMO_API_KEY` | `provider.type=kemo` | Kemo 网关密钥兜底 |
| `KEMO_BASE_URL` | kemo | 默认 `http://127.0.0.1:8741`，填写协议根地址，不要求 `/v1` |
| `KEMO_MODEL` | kemo | 仅当用户配置 `provider.model` 为空时使用 |
| `OPENAI_API_KEY` | `provider.type=chat` | Chat Completions 兼容密钥兜底 |
| `OPENAI_BASE_URL` | chat | 默认 `https://api.openai.com/v1`；缺少尾部 `/v1` 时框架自动补全 |
| `OPENAI_MODEL` | chat | 仅当用户配置 `provider.model` 为空时使用 |

`provider.api_key_env` 可以指定任意自定义环境变量名，例如 `MY_TEAM_API_KEY`。该自定义变量无需写入 `.env.example`，但应在部署说明中记录变量名，不能记录真实值。

### 网络代理

| 变量 | 说明 |
|------|------|
| `HTTP_PROXY` | HTTP 请求代理地址；留空直连 |
| `HTTPS_PROXY` | HTTPS 请求代理地址；留空直连 |

Provider 和使用标准网络栈的模块可继承代理。TLS 证书验证使用系统安全策略，不支持通过环境变量关闭验证。

### 工具插件

| 变量 | 说明 |
|------|------|
| `TAVILY_API_KEY` | `web_search` 使用的 Tavily 密钥；工具始终可被发现，为空时调用会返回配置引导且不会发起网络请求，配置后需重启智能体 |

其他热插拔模块可以定义自己的环境变量，但必须使用清晰、避免冲突的前缀，并在模块文档中说明。平台 Token 不应写入 `message.json`、知识库、技能或日志。

### Web 服务

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WEB_HOST` | `127.0.0.1` | 监听地址；`0.0.0.0` 会暴露到可达网络 |
| `WEB_PORT` | `1357` | 1–65535；端口冲突时启动器可继续探测后续端口 |
| `WEB_ACCESS_TOKEN` | 空 | 非空时启用 Token 登录；登录页通过 `POST /api/auth/token` 的请求体提交，不放入 URL |
| `WEB_USERNAME` | 空 | Web 页面登录用户名，与内部 `users/<name>` 无关 |
| `WEB_PASSWORD` | 空 | Web 页面登录密码，必须与 `WEB_USERNAME` 同时设置或同时留空 |
| `WEB_SESSION_SECRET` | 启动时随机生成 | 签名会话密钥；多进程或需要重启后保持登录时应显式设置强随机值 |
| `WEB_SESSION_COOKIE_NAME` | `kemo_agent_session` | Session Cookie 名称；同域多实例应使用不同名称 |
| `WEB_SESSION_COOKIE_SECURE` | `false` | 公网 HTTPS 部署应设为 `true`，为 Session Cookie 添加 `Secure`；本机纯 HTTP 保持 `false` |
| `WEB_AUTH_IP_MAX_FAILURES` | `0` | 单个 IP 在同一认证阶段允许的失败次数；留空或 `0` 表示不限次数 |
| `WEB_AUTH_IP_WINDOW_SECONDS` | `600` | 失败次数统计窗口，单位秒，必须大于 `0` |
| `WEB_AUTH_IP_LOCK_SECONDS` | `900` | 达到失败上限后的锁定时间，单位秒，必须大于 `0` |
| `WEB_AUTH_TRUSTED_PROXIES` | 空 | 可信反向代理 IP/CIDR，逗号分隔；仅直连来源命中时才读取 `X-Forwarded-For` |
| `KEMO_LOG_RETENTION_DAYS` | `90` | Cron 与外部消息 SQLite 结构化日志保留天数；`0` 表示不自动清理 |

认证流程按配置确定：只配置 Token 时验证 Token 后进入；只配置用户名密码时登录后进入；两者同时配置时必须先验证 Token，再验证用户名和密码，不能任选其一。双重认证的 Token 中间状态有效 5 分钟，完整签名会话默认有效 2 小时。

失败限制按“客户端 IP + 认证阶段”分别统计 Token 和用户名密码。达到上限时接口返回 HTTP 429 与 `Retry-After`；该状态保存在当前 Web 进程内，重启后清空。未配置可信代理时一律使用直连 IP，避免客户端伪造转发头绕过限制。

Token 与密码只出现在对应 POST 请求体中；浏览器所有者仍可在开发者工具的当次网络请求里查看自己输入的值，这是浏览器端无法隐藏的。服务端 Cookie 只保存签名认证状态，不保存 Token、用户名或密码。

### 示例

```dotenv
KEMO_API_KEY=replace-with-local-secret
KEMO_BASE_URL=http://127.0.0.1:8741
KEMO_MODEL=my-model

WEB_HOST=127.0.0.1
WEB_PORT=1357
WEB_USERNAME=
WEB_PASSWORD=
WEB_SESSION_SECRET=
WEB_SESSION_COOKIE_NAME=kemo_agent_session
WEB_SESSION_COOKIE_SECURE=false
WEB_AUTH_IP_MAX_FAILURES=
WEB_AUTH_IP_WINDOW_SECONDS=600
WEB_AUTH_IP_LOCK_SECONDS=900
WEB_AUTH_TRUSTED_PROXIES=

HTTP_PROXY=
HTTPS_PROXY=
TAVILY_API_KEY=
```

### 安全规则

1. `.env` 不提交版本库；只提交不含真实值的 `.env.example`。
2. 不把 `.env` 内容复制到知识库、记忆、截图、日志或错误报告。
3. 怀疑泄露时立即轮换密钥，不能只删除文件记录。
4. 对外监听 Web 时必须同时考虑认证、防火墙、反向代理和 HTTPS；HTTPS 部署必须设置 `WEB_SESSION_COOKIE_SECURE=true`。
5. 自动化部署优先使用操作系统或平台的 Secret 管理，而不是把生产密钥写入镜像。

---

## global_config.json 配置项手册

> 文档版本：v2.4
> 最后核对：2026-08-07
> 事实来源：`config/global_config.json`、`run/config/`、`run/engine.py`、`run/conversation/`、`run/context/`、`run/scheduler/`

kemo-agent 全局配置文件，位于 `config/global_config.json`。所有用户共享这些默认值，用户级 `user_config.json` 可覆盖其中非 `USER_ONLY_SECTIONS` 的字段。

修改配置前先查本手册；部署级密钥和 Web 启动参数见 本文“环境变量”章节，用户覆盖规则见 本文“用户配置”章节。未知字段是否生效以当前加载器和使用方代码为准，不应仅因 JSON 能保存就视为受支持。

---

### schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

### provider_runtime — Provider 运行时并发控制

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_requests` | int | `10` | 进程级总闸，Web、外部消息、Cron、维护任务和子代理共享；每一次真实 LLM API 请求独立占槽，工具执行期间释放槽位 |
| `request_semaphore_timeout` | float | `300.0` | 获取并发槽位的超时秒数；超时后抛出 `ProviderCongestionError` |

> 用户 `user_config.json` 中独立配置的 `provider_runtime` 会覆盖此全局值。

---

### tools — 工具调用

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 全局工具开关。`false` 时所有 Provider 工具 schema 均不注册，智能体只能纯文本对话 |
| `timeout` | int | `240` | 单次工具调用未提供 `timeout` 参数时的默认秒数；工具 Schema 声明且调用方显式提供有效 `timeout` 时，以显式值为准，插件内部期限与框架看门狗使用同一有效值 |
| `max_iterations` | int | `80` | 单轮对话允许处理的最大工具调用次数。每个调用分别计数，同一 Provider 响应中的并行调用也计入总数；达到上限后停止本轮，超出部分不执行 |
| `consecutive_identical_call_limit` | int | `8` | 同一工具使用完全相同参数连续请求的允许次数；工具或参数变化后重新计数。超过后阻止继续执行 |
| `invalid_tool_arguments_retries` | int | `2` | 主智能体或子智能体收到 Provider 的 `invalid_tool_arguments` 时，使用新 `request_id` 重新请求完整 JSON object 的次数。失败尝试的文本与思考会保留，工具调用须整批校验后才发布和执行；已经发布媒体时不重试。`0` 表示禁用恢复 |

> 用户配置 `tools` 为对象深合并，可覆盖其中任意字段。

单次工具内联 JSON 结果有不可配置的 100,000 字符核心硬限制。超限时框架丢弃正文，只向智能体、事件和历史写入 `ToolResultTooLargeError`、原始字符数与缩小范围提示；文件内容应改用 `file.stat` 和 `file.read_range` 分段读取。该受控拒绝不计入 `consecutive_tool_fail_limit`。

---

### history — 对话历史

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 历史数据格式版本号 |
| `recent_full_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超过此数的旧轮次会被 `context_manage` 逐轮压缩 |
| `consecutive_tool_fail_limit` | int | `5` | 同名工具连续失败的容忍上限。达到后本轮从 Provider 工具 schema 中临时移除该工具；其他工具穿插执行会重置连续失败计数 |

---

### history_summary — 历史摘要后台调度

历史摘要使用独立持久调度线程，不占用 Web 请求线程，也不进入有容量上限的子代理内存队列。会话关闭并排队后，即使用户关闭网页，任务仍会由运行中的 RuntimeHost 继续处理。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `poll_interval` | number | `5` | 持久摘要任务扫描间隔（秒），最小为 1 |
| `max_jobs_per_cycle` | int | `1` | 每个扫描周期最多领取的摘要任务数；多用户之间轮转领取，避免单用户长期独占 |
| `max_attempts` | int | `5` | 单个摘要任务的自动尝试上限；达到上限后进入 `exhausted`，等待用户手动重试 |
| `retry_delays_seconds` | int[] | `[30, 120, 600, 1800]` | 各次失败后的退避时间；超出数组长度时沿用最后一个值。Provider 拥塞和服务停止只推迟任务，不消耗失败次数 |

长对话按保守 Token 预算分块生成摘要；即使单轮正文超过预算，也会无损拆成多个片段。每块完成后写入持久断点，进程重启或中途失败后从最近断点继续，不重复处理已经成功的分块。

摘要结果优先通过受 Schema 约束的内部提交工具返回，不依赖模型自行拼写 JSON。若 Provider 不支持或模型没有遵循工具调用，系统会依次兼容文本 JSON、带“标题/摘要”标签的普通文本，并在连续格式失败时生成不含敏感凭据的本地保底卡片；Provider 网络、鉴权或服务错误仍进入持久重试，不会被格式保底掩盖。修改本段配置后需要重启运行时宿主才能应用到调度线程。

---

### prompt — System Prompt 注入控制

#### char_limits — 各 Prompt 段字符上限

所有上限均为注入时的截断保护，超过上限的内容会被截断。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `task_plan` | `6000` | 任务计划段注入上限 |
| `perception` | `20000` | 感知模块数据注入上限 |
| `expand_data` | `20000` | 拓展模块数据注入上限 |
| `skill_prompts` | `80000` | 技能提示词（共享技能 + 用户技能）注入上限 |
| `plugin_prompts` | `80000` | 插件提示词注入上限 |

> 以下 Prompt 段不使用 char_limits 字段控制：用户人格、全局人格、运行手册、子代理注册、知识库索引、四层记忆（永久 + 重要 + 三层临时）分别由各自独立的字符控制逻辑管理。各 Prompt 段注入模式固定为 `full`，不提供 `injection_mode` 配置项。

---

### memory — 记忆系统

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `storage_schema_version` | int | `1` | `improve/memory.sqlite3` 表结构版本号；运行时以数据库 `memory_meta` 为准 |
| `extraction_mode` | string | `compression_only` | 记忆提取模式：`disabled` 完全关闭；`compression_only` 仅上下文压缩/保存时提取；`background` 允许 Maintenance 每轮后台提取；`on_commit` 每轮同步提取 |
| `recovery_max_rounds_per_scan` | int | `10` | Maintenance 每轮扫描最多补提取的总轮数。运行时限制为 1–20 |
| `extraction_batch_rounds` | int | `5` | 一次 `self_improve` 模型运行最多分析的连续轮数。运行时限制为 1–20 |
| `extraction_max_candidates_per_batch` | int | `10` | 每批最多保留的记忆候选；同时受“每轮最多 2 条”限制，运行时硬上限为 40 |
| `important_memory_max_chars` | int | `20000` | 临时重要热画像的 Prompt 注入字符预算。注入时由 `run/config/` 的 Prompt 门面按该值截断 |
| `important_memory_output_max_chars` | int | `20000` | 临时重要热画像模型输出的防失控硬上限；与注入预算语义独立，超过后拒绝本次更新且不覆盖旧热画像 |
| `history_read_enabled` | bool | `true` | 是否允许智能体使用 `history_search` 工具读取历史对话 |

#### temporary_injection_limits — 临时记忆注入上限

按**文件数量**截断，优先保留高权重层级。仅限制单次 Prompt 注入，不限制磁盘存储数量。Kemo Graph 外挂不读取或改写这些上限，也不会减少本地记忆注入。

`memory_important_sources` 只记录热画像的来源关系和内容摘要，用于判断热画像是否仍然有效，不会改变普通临时记忆的 Prompt 注入资格。进入热画像的碎片仍按 `temporary_injection_limits` 在原层正常注入、加权、到期和晋升；热画像作为更高优先级的概括层额外强化这些事实，而不是替换权威碎片正文。任一来源正文或层级变化时旧热画像暂停注入，普通临时层始终保持正常注入，直到下次巡检重建热画像。

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `half_year` | `100` | 半年层最多注入 100 个记忆文件 |
| `one_month` | `200` | 月层最多注入 200 个记忆文件 |
| `seven_days` | `300` | 周层最多注入 300 个记忆文件 |

#### tiers — 记忆层级与晋升规则

| 层级 | `days` | `upgrade_threshold` | `next` | 说明 |
|------|-------|---------------------|--------|------|
| `seven_days` | 7 | 3 | `one_month` | 7 天到期，权重 ≥ 3 晋升到月层 |
| `one_month` | 30 | 10 | `half_year` | 30 天到期，权重 ≥ 10 晋升到半年层 |
| `half_year` | 180 | 60 | null | 180 天到期，权重 ≥ 60 晋升到永久记忆 |

权重规则：
- 仅在保存、手动压缩、Token 超限压缩等历史整理管线中，被 `self_improve` 依据用户原文命中时可加权
- Prompt 注入、用户查看和工具检索不加权；后台整理临时三层时不得读取 `important`
- 同一记忆每天合计最多 `+1`（每日锁）
- 晋升后新层级权重从 0 重新累计
- 到期未达阈值的直接删除，不降级保留
- 永久记忆没有索引、权重或到期时间

---

### agent_runtime — 智能体运行时

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `queue_maxsize` | int | `50` | 用户级 `AgentScheduler` 的有界队列最大长度；0 表示无界 |
| `default_timeout` | int | `600` | 子代理整体执行默认超时秒数；达到期限后自动发送协作式取消并等待清理，同步调度不会被普通工具默认超时提前截断 |
| `timeout_survival_seconds` | number | `120` | 子代理达到整体超时后的收尾存活期；存活期内自然完成会保留结果并标记 `completed_after_timeout`，0 表示禁用 |

> 注意：每个用户的 `AgentScheduler` 使用实例级串行锁和独立有界队列，不再由一个进程级锁串行所有用户。

---

### task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_steps` | int | `20` | 单个任务计划的最大步骤数 |

> 用户配置的 `task_plan.auto_accept` 控制是否自动执行计划；全局配置不包含此字段。

---

### cron — 定时调度

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enabled` | bool | `true` | 是否启用 cron 调度器 |
| `poll_interval` | int | `30` | 任务轮询间隔（秒）。运行时会自动取它与 `sense_update_rate`、`expand_update_rate` 的最小值，保证短周期任务按时被扫描 |
| `avoid_congestion` | bool | `true` | 是否启用 Provider 拥塞避免 |
| `congestion_threshold_ratio` | float | `0.2` | 拥塞阈值比例。当 Provider 可用槽位低于此比例时，推迟普通用户任务和重型系统任务；全局感知/拓展采集不退避 |

---

### task_cron_system — 系统定时任务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `sense_update_rate` | int | `5` | 全局感知模块数据刷新间隔（秒）。缺失或非法值回退到 5 |
| `expand_update_rate` | int | `5` | 全局、共享和各用户拓展模块的统一刷新间隔（秒）。缺失或非法值回退到 5 |
| `module_update_timeout` | number | `120` | 每个感知/拓展采集脚本的独立子进程超时（秒）；非法值回退到 120，最大 3600 |
| `runtime_checkpoint_seconds` | number | `300` | 高频系统任务的 `latest_run_at/next_run_at` 内存状态写回任务 JSON 的检查点间隔（秒，1..3600）；后台调度器通过跨进程租约保证每个 root 只有一个系统任务领导者，前台单次扫描在释放租约前写回位置，异常与正常停机仍会立即落盘 |
| `success_log_flush_seconds` | number | `300` | 高频系统任务成功日志的内存聚合窗口（秒，1..3600）；调度循环按真实截止时间冲刷，不依赖下一次成功事件，失败日志独立立即落盘，停机时强制冲刷剩余成功聚合 |

> 两个刷新间隔也影响 `cron.poll_interval` 的最小轮询粒度；单模块超时不改变轮询频率。
>
> 感知 Web API 使用与调度器相同的校验逻辑读取全局 `sense_update_rate`，返回 `update_interval_seconds` 和兼容显示文本。频率不写入单个 `sense.json`；用户配置中的同名字段也不作为全局调度或页面展示来源。
>
> 高频采集仍按 5 秒执行；两个持久化间隔只减少无变化状态和成功日志的写盘次数，不会降低采集频率，也不会延迟失败诊断。

---

### runtime_host — 运行时宿主

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `enable_background_scheduler` | bool | `true` | 是否在启动时自动开启统一后台调度器。启用时宿主自动管理 Cron 调度、上下文整理、记忆到期晋升检查及感知/拓展数据刷新 |

---

### message — 外部消息路由

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_workers` | int | `8` | 消息处理线程池大小 |
| `max_queued_messages` | int | `20` | 消息等待队列上限。为 0 时保持无界兼容模式；队列满抛出 `MessageQueueFullError` |

> 外部消息总容量 = `max_workers + max_queued_messages`。

---

### web — Web 服务

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `max_concurrent_chats` | int | `3` | 单用户最大并发聊天数。空闲闸门会自动采用新保存的 Web 限制 |
| `max_pending_chats` | int | `5` | 单用户聊天等待队列上限。队列满或等待超时返回 HTTP 503 |
| `pending_chat_timeout` | float | `30.0` | 排队等待超时秒数 |

---

### agents — 智能体上下文与压缩

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `conserved_rounds` | int | `3` | 保留完整工具日志和思考过程的最近轮数。超出此数的旧轮次由 `context_manage` 逐轮压缩 |
| `max_rounds` | int | `80` | 上下文最大对话轮数。达到后触发压缩。此值限制 SQLite runtime 窗口及 Provider 上下文的轮次上限，不限制用户可见 archive 归档 |
| `rounds_after_compression` | int | `20` | 压缩后保留的轮数 |
| `token_limit` | int | `1000000` | 上下文 Token 上限。预估总 token 超过此值时触发压缩 |
| `token_compression_ratio` | float | `0.3` | 输入预算比例。`input_budget = token_limit × ratio`，超过此比例时触发压缩 |
| `important_memory_review_hours` | int | `3` | 宿主级临时重要记忆巡检间隔（小时）。Cron 据此创建唯一系统 recurring 任务，到期后按用户分别执行；不支持用户级时间表 |
| `daily_memory_review_time` | str | `"02:00"` | 宿主级每日记忆整理时间（北京时间 HH:MM）。Cron 据此创建唯一系统 daily 任务，到期后按用户分别执行；不支持用户级时间表 |

`context_manage` 的摘要输入会合并正文、reasoning/think 和工具结论；核心运行时为每次摘要请求提供最多 20000 tokens 的输出预算，并要求非空、完整的结构化 JSON。网页端手动压缩只有在摘要缓存、SQLite runtime 窗口轮数、绝对轮次偏移和摘要覆盖范围重新读取校验通过后才报告成功；用户可见 archive 归档仍保留完整轮次。

---

### 与 user_config.json 的关系

`global_config.json` 提供全局默认值。用户可在 `user_config.json` 中覆盖部分字段。

| 维度 | global_config.json | user_config.json |
|------|-------------------|-----------------|
| 作用域 | 所有用户 | 单个用户 |
| 覆盖性 | 默认值 | 可覆盖全局值 |
| 用户独有字段 | 无 | `provider`、`agent_models`、`multimodal_models`、`multimodal_routing`、`knowledge`、`skills`、`expand`、`perception`、`plugins`、`agent_runtime`（部分） |
| 修改方式 | 直接编辑文件 | Web 配置面板或直接编辑文件 |

#### 白名单规则

用户配置中的白名单字段（`skills.shared_whitelist`、`expand.global_whitelist`、`expand.shared_whitelist`、`perception.global_whitelist`、`plugins.whitelist`）遵循：
- 空数组 `[]` = 全部启用
- 有值 = 只启用列出的项

> 注意：全局配置中不含白名单字段。白名单仅存在于用户配置中。

#### 覆盖行为

- 用户配置中的 `provider`、`knowledge`、`skills`、`expand`、`perception`、`plugins` 等段是用户专属，不从全局配置兜底
- 其他框架段按对象深合并：用户配置中的字段覆盖全局默认值，未提供的字段继承全局值
- 修改全局配置后无需重启，下次请求自动生效

---

## user_config.json 配置项手册

> 文档版本：v2.4
> 最后核对：2026-08-07
> 事实来源：`template/user/user_config.json`、`run/config/`、`provider/protocol/models.py`

kemo-agent 用户级配置文件，位于 `users/<用户名>/user_config.json`。用户可在此覆盖全局默认值，也可通过 Web UI 配置面板修改。

目录和数据文件职责见 `knowledge-and-user-data.md`；环境变量兜底和密钥优先级见本文“环境变量”章节。配置保存后，当前 Run 不会中途改变 Provider 或权限，下一次 Run 才使用新的合并结果；需要重建 RuntimeHost 组件的参数应重启服务。

配置分两类：
- **用户独有段**：`provider`、`agent_models`、`multimodal_models`、`multimodal_routing`、`knowledge`、`skills`、`expand`、`perception`、`plugins` — 只从用户配置读取，全局配置不兜底
- **框架覆盖段**：其余段按对象深合并，用户配置中的字段覆盖全局默认值，未提供的字段继承全局值

---

### schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

### provider — LLM API 配置

**这是用户首次使用时最需要填写的配置组。** `user_create.py` 的交互式引导也只配置这组。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `type` | string | — | Provider 类型。`"kemo"` = Kemo 网关原生模式，`"chat"` = 标准 Chat Completions 模式。启动前选择，运行中不自动回退 |
| `base_url` | string | — | API 基础地址。Kemo 默认 `http://127.0.0.1:8741`，chat 模式自动补全 `/v1` |
| `api_key` | string | — | API 密钥。优先级高于 `api_key_env` |
| `api_key_env` | string | — | 从环境变量读取密钥的变量名。当 `api_key` 为空时生效，如 `"KEMO_API_KEY"` |
| `model` | string | — | 默认对话模型名，如 `"deepseek-chat"` |
| `stream` | bool | `true` | 是否启用流式输出 |
| `timeout` | number | `120` | 普通 Provider 请求超时秒数。专用多模态调用未显式配置时会改用当前工具期限并预留 5 秒收尾；显式配置后仍受工具期限上限约束 |
| `reasoning_effort` | string | `"medium"` | 保存的逻辑思考档位。`chat` 固定支持 `minimal`、`low`、`medium`、`high`、`max`，缺失、`none` 或非法值回退为 `medium`；`kemo` 完全以当前模型能力声明的有序档位为准，不限定名称或数量，并过滤 `none`。已保存档位失效时优先回退 `medium`，否则使用声明首项；不支持推理或能力不可用且无缓存时，请求不提交 `reasoning` |
| `input_modalities` | string[] | `["text"]` | 主模型已确认支持的输入模态；必须包含 `text`。Chat 只允许增加 `image`；Kemo 还可声明 `audio`、`video`、`file`，并会与网关能力声明交叉验证 |

#### 密钥优先级

`api_key`（明文） > `api_key_env`（环境变量名） > 全局 `.env` 兜底

#### 地址优先级

1. `user_config.json → provider.base_url`
2. `KEMO_BASE_URL` / `OPENAI_BASE_URL` 环境变量
3. Provider 类型对应的内置默认地址（chat → OpenAI 默认，kemo → `http://127.0.0.1:8741`）

最终地址统一去除尾部 `/`。只有 `chat` 模式自动补全 `/v1`。

#### Kemo 动态思考档位

- 仅当已保存协议为 `kemo`、Base URL 与网关调用密钥有效且模型目录可读取时，Web 才从模型条目的 `capabilities_url` 获取当前模型档位；普通 `chat` 协议保持原固定五档链路。
- 网关返回的有序 `reasoning.efforts` 是界面和运行时的唯一可选项。顶部模型弹层与 Provider 配置页按返回顺序动态生成同一组卡片；返回三档就展示三档，返回七档就展示七档。框架不维护 Kemo 档位白名单，表示关闭思考的 `none` 即使被返回也必须过滤。客户端保存并原样提交其余 Kemo 逻辑档位，不按模型名猜测，也不执行 `reasoning_effort_map` 到厂商档位的转换。
- `reasoning.supported=false`、档位列表为空，或首次能力查询失败时，界面不显示固定五档，运行时省略 `reasoning`。刷新失败但存在短期成功缓存时可继续使用缓存，并明确标记为旧能力信息。
- 模型、Base URL 或 API Key 改变后会按新的能力缓存身份重新读取；Web 能力接口只接收模型名，API Key 不会发送给浏览器或出现在响应中。

#### Provider 工具调用终态

- `chat` 模式同时兼容现代 `tool_calls` 与旧式单个 `function_call`。工具参数必须是完整 JSON 对象；空参数可规范化为空对象，但无效 JSON 或数组、字符串等非对象根节点不会进入工具执行。
- `finish_reason=length`、`max_tokens`、`max_output_tokens` 或 `content_filter` 会映射为统一 `incomplete`。即使响应中已经出现工具名称或部分参数，也只保留诊断信息，不执行该批工具。
- `kemo` 模式以网关统一终态为准；若 `ToolCallItem.parse_error` 非空，框架在统一运行事件层拒绝执行。该保护不改变 Kemo 协议字段，也不触发跨协议回退。
- Chat 流优先使用标准 `[DONE]`。部分兼容服务在已经提供明确 `finish_reason` 后以正常 EOF 关闭时可以收束；没有 `[DONE]`、没有 `finish_reason` 或在 JSON 帧中途断开仍视为传输失败。

---

### agent_models — 子代理专用模型

子代理三档模型配置。任一字段留空时继承 `provider.model`。

| 字段 | 说明 |
|------|------|
| `default` | 普通子代理用模型 |
| `cheap` | 轻量子代理用模型；历史对话摘要也使用此档位 |
| `reasoning` | 推理型子代理用模型 |

---

### multimodal_models — 多模态模型覆盖

默认全部为空字符串。专用多模态工具只调用明确填写的能力模型，避免把不支持该操作的主模型当作兜底；主模型直传由 `provider.input_modalities` 和 Kemo 网关能力共同决定。

| 字段 | 说明 |
|------|------|
| `vision` | 识图/多模态理解 |
| `image_generation` | 文生图 |
| `image_edit` | 图生图/图像编辑 |
| `audio_transcription` | 语音转文字 |
| `speech_generation` | 文生语音 |
| `speech_to_speech` | 语音生语音 |
| `video_understanding` | 视频理解、时间轴摘要 |
| `video_generation` | 视频生成 |

> 不含 embedding 和 rerank。Chat 模式只保证文本、工具和图片识别。音频、视频、普通文件输入及所有媒体生成/转换只在 Kemo 模式启用；Kemo 同时校验输入/输出模态与 `extensions.operations`，不会跨协议自动回退。

### multimodal_routing — 多模态路由

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `vision` | `"auto"` | `auto` = 主模型支持图片时优先直传，否则使用专用视觉模型；`main` = 仅主模型；`dedicated` = 仅 `multimodal_models.vision` |

路由对 Web 上传与外部消息资产一致生效。外部消息模块不能自行将图片作为 inline Content Block 发送给主模型。`multimodal` 工具还接受 `paths`：绝对路径或相对项目根目录的明确本地媒体会先登记、验证，再直接交给专用能力模型；这不会改变主模型的 `input_modalities` 声明。

图片文件在后端经过来源目录约束或显式本地路径登记、完整解码、真实格式与大小检查后，Chat 请求才会临时内联；Chat 图片仅接受 JPEG、PNG、WEBP 和 GIF，Base64 不写入 Web 状态或文本历史。Kemo 输入先通过认证 Asset API 流式上传，再以远端 `asset_id` 进入请求；生成结果经下载和 SHA-256 校验后防重名保存到用户 `download` 目录。专用插件不重复携带主对话历史。

识别类动作（图片理解、音频转写、视频理解）只对明确标记为可重试的瞬时错误进行一次额外尝试。生成、编辑和转换类动作不自动重试，避免重复计费或产生重复产物。失败结果不会进入同参数工具结果缓存，因此智能体根据错误分类决定再次调用时会真正发起新请求；同名工具连续失败保护仍然生效。

---

### provider_runtime — Provider 运行时并发控制

覆盖全局 `global_config.json → provider_runtime`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_concurrent_requests` | int | 10 | 进程级总闸，所有 LLM API 请求共享；工具执行期间释放槽位 |
| `request_semaphore_timeout` | float | 300.0 | 获取并发槽位的超时秒数 |

---

### task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto_accept` | bool | `false` | 是否自动批准任务计划。`true` 时计划创建即执行，`false` 时需手动批准 |
| `auto_retry_on_fix` | bool | `false` | 修正 paused/failed 计划或重试 failed/cancelled 步骤后，是否自动恢复为 `approved` 并等待执行器原子领取；计划自身 `auto_accept=true` 时同样会自动恢复 |

---

### tools — 工具调用

覆盖全局 `global_config.json → tools`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `timeout` | int | 240 | 工具未显式提供 `timeout` 时的默认秒数；显式有效参数会覆盖此值，并同时作用于插件内部期限和框架看门狗 |
| `max_iterations` | int | 80 | 单轮对话允许处理的最大工具调用次数；并行工具调用分别计数 |
| `consecutive_identical_call_limit` | int | 8 | 相同参数连续调用同一工具的容忍上限 |
| `invalid_tool_arguments_retries` | int | 2 | 主智能体或子智能体收到不完整工具参数时，使用新请求 ID 自动重新生成参数的次数；文本与思考保留，工具调用整批校验后才发布和执行，已经发布媒体时不重试；`0` 表示禁用 |

> 注意：`tools.enabled` 不在用户配置中覆盖，仅全局配置控制。

单次工具内联 JSON 结果的 100,000 字符硬限制由核心统一执行，不是用户配置字段。超限正文不会进入 Provider 或历史；文件工具会提示使用 `stat` 和 `read_range` 分段读取，且本次受控拒绝不计入连续工具失败。

---

### history — 对话历史

覆盖全局 `global_config.json → history`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `recent_full_rounds` | int | 3 | 保留完整工具/思考日志的最近轮数 |
| `consecutive_tool_fail_limit` | int | 5 | 同名工具连续失败后临时移除的容忍上限 |

---

### prompt — System Prompt 注入控制

覆盖全局 `global_config.json → prompt`。按对象深合并。

| 字段 | 类型 | 说明 |
|------|------|------|
| `char_limits` | object | 各 Prompt 段字符上限。同全局结构：`task_plan`、`perception`、`expand_data`、`skill_prompts`、`plugin_prompts` |

各 Prompt 段注入模式固定为 `full`，不提供 `injection_mode` 配置项。旧配置中全部为 `full` 的声明可兼容读取，但不会再影响运行时。

---

### knowledge — 知识库开关

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `use_shared` | bool | `true` | 是否注入共享知识库索引 |
| `use_global` | bool | `true` | 是否注入全局知识库索引 |

> 用户级知识库始终启用，无需开关。三个知识层的注入顺序：用户 > 共享 > 全局（权重从高到低）。

---

### skills — 技能白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享技能白名单。空数组 = 全部启用。填入技能目录名（支持相对路径如 `development/python`）则只启用列出的 |

> 用户技能（`users/<name>/user_skills/`）始终允许，不受白名单控制。技能只注入 Prompt 提示词，不注册可执行工具。`user_whitelist` 已从配置契约删除。

---

### expand — 拓展模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享拓展白名单。空 = 全部启用 |
| `global_whitelist` | array | `[]` | 全局拓展白名单。空 = 全部启用 |
| `prompt_injection` | bool | `true` | 总注入闸门。`false` 时整个 `[expand_data]` 段不进入系统提示词，但不影响后台更新或主动调用拓展 |
| `realtime_injection` | bool | `false` | `false` 时每轮对话开始读取一次拓展快照；`true` 时每次逻辑 Provider 请求前重读最新快照。开启会降低 Prompt Cache 命中率 |

> 用户拓展（`users/<name>/expand/`）始终按当前用户目录动态解析，不受白名单控制。

---

### perception — 感知模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `global_whitelist` | array | `[]` | 全局感知模块白名单。空 = 全部启用 |
| `prompt_injection` | bool | `true` | 总注入闸门。`false` 时整个 `[perception]` 段不进入系统提示词，但后台采集仍可继续 |
| `realtime_injection` | bool | `false` | `false` 时每轮对话开始读取一次感知快照，工具续轮保持不变；`true` 时每次逻辑 Provider 请求前重读后台已发布的最新快照。开启会降低 Prompt Cache 命中率 |

> 感知模块位于 `global_sense/`，只采集系统数据，通过 `sense.md` 单向注入 system prompt，不提供操控接口。

---

### plugins — 插件白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `whitelist` | array | `[]` | 内置插件白名单。空 = 全部启用。填入插件名则只启用列出的 |

---

### memory — 记忆系统

覆盖全局 `global_config.json → memory`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `extraction_mode` | string | `compression_only` | 记忆提取模式：`disabled` 完全关闭；`compression_only` 仅上下文压缩/保存时提取；`background` 允许后台提取；`on_commit` 每轮同步提取 |
| `recovery_max_rounds_per_scan` | int | 10 | Maintenance 每次扫描最多补提取的总轮数，范围 1–20 |
| `extraction_batch_rounds` | int | 5 | 每次模型分析的连续轮数，范围 1–20 |
| `extraction_max_candidates_per_batch` | int | 10 | 每批候选总上限；仍受每轮最多 2 条限制 |
| `temporary_injection_limits` | object | 100/200/300 | 三层临时记忆注入数量上限：`half_year`、`one_month`、`seven_days`。Kemo Graph 外挂不读取或改写这些上限，也不会减少本地记忆注入 |
| `important_memory_max_chars` | int | 20000 | 临时重要热画像的 Prompt 注入字符预算；注入时按该值截断 |
| `important_memory_output_max_chars` | int | 20000 | 临时重要热画像输出防失控硬上限；与注入预算语义独立，超过后拒绝且不覆盖旧热画像 |
| `history_read_enabled` | bool | true | 是否允许智能体使用 `history_search` 工具 |

---

### agent_runtime — 智能体运行时

覆盖全局 `global_config.json → agent_runtime`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `queue_maxsize` | int | 50 | 用户级 `AgentScheduler` 有界队列最大长度；0 表示无界 |
| `default_timeout` | int | 600 | 子代理整体默认超时（秒）；到期自动请求协作式取消并等待清理，不受普通工具默认超时提前截断 |
| `timeout_survival_seconds` | number | 120 | 子代理超时后的收尾存活期（秒）；存活期内完成保留结果并标记 `completed_after_timeout`，设为 0 可恢复旧行为 |

---

### web — Web 服务

覆盖全局 `global_config.json → web`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_concurrent_chats` | int | 3 | 单用户最大并发聊天数 |
| `max_pending_chats` | int | 5 | 聊天等待队列上限 |
| `pending_chat_timeout` | float | 30.0 | 排队超时（秒） |

---

### message — 外部消息路由

覆盖全局 `global_config.json → message`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_workers` | int | 8 | 消息处理线程池大小 |
| `max_queued_messages` | int | 20 | 消息等待队列上限；0 为无界模式 |

---

### cron — 定时调度

覆盖全局 `global_config.json → cron`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `enabled` | bool | true | 是否启用 cron 调度器 |
| `poll_interval` | int | 30 | 任务轮询间隔（秒） |
| `avoid_congestion` | bool | true | 是否启用 Provider 拥塞避免 |
| `congestion_threshold_ratio` | float | 0.2 | 拥塞阈值比例 |

---

### task_cron_system — 系统定时任务

按对象深合并全局 `global_config.json → task_cron_system`。刷新频率由 RuntimeHost 的全局配置统一调度；用户层只适合覆盖自己的模块执行超时。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `sense_update_rate` | int | 5 | 系统级感知刷新间隔；用户配置中的值不改变全局任务频率 |
| `expand_update_rate` | int | 5 | 三层拓展统一刷新间隔；用户配置中的值不改变全局任务频率 |
| `module_update_timeout` | number | 120 | 当前用户拓展的单模块子进程超时（秒），最大 3600 |

---

### agents — 智能体上下文与压缩

覆盖全局 `global_config.json → agents`。按对象深合并，但系统 Cron 的两个宿主级调度字段除外。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `conserved_rounds` | int | 3 | 保留完整工具/思考日志的最近轮数 |
| `max_rounds` | int | 80 | 上下文最大对话轮数（触发压缩） |
| `rounds_after_compression` | int | 20 | 压缩后保留的轮数 |
| `token_limit` | int | 1000000 | 上下文 Token 上限 |
| `token_compression_ratio` | float | 0.3 | 输入预算比例 |

上下文摘要由 `context_manage` 统一处理，输入包含正文、reasoning/think 与工具结论，核心运行时的单次摘要输出预算为 20000 tokens。手动压缩会在 SQLite runtime 窗口和摘要缓存完成落盘校验后才返回成功；该输出预算不是用户配置字段。

`important_memory_review_hours` 与 `daily_memory_review_time` 只允许在
`config/global_config.json → agents` 中配置。系统只创建一份全局时间表，任务到期后再按用户分别执行；在 `user_config.json` 中填写这两个字段不会创建用户专属调度，不应声明。

---

### 完整字段速查

#### 用户独有段（仅用户配置，全局不兜底）

| 配置组 | 主要字段 |
|--------|---------|
| `provider` | type, base_url, api_key, api_key_env, model, stream, reasoning_effort, input_modalities |
| `agent_models` | default, cheap, reasoning |
| `multimodal_models` | vision, image_generation, image_edit, audio_transcription, speech_generation, speech_to_speech, video_understanding, video_generation |
| `multimodal_routing` | vision |
| `knowledge` | use_shared, use_global |
| `skills` | shared_whitelist |
| `expand` | global_whitelist, shared_whitelist, prompt_injection, realtime_injection |
| `perception` | global_whitelist, prompt_injection, realtime_injection |
| `plugins` | whitelist |
| `task_plan` | auto_accept, auto_retry_on_fix |

#### 框架覆盖段（按对象深合并）

| 配置组 | 对应全局段 | 说明 |
|--------|-----------|------|
| `provider_runtime` | provider_runtime | 覆盖全局默认 |
| `tools` | tools | 覆盖（不含 enabled） |
| `history` | history | 覆盖 |
| `prompt` | prompt | 覆盖 |
| `memory` | memory | 覆盖 |
| `agent_runtime` | agent_runtime | 覆盖 |
| `web` | web | 覆盖 |
| `message` | message | 覆盖 |
| `cron` | cron | 覆盖 |
| `task_plan` | task_plan | 覆盖；`auto_retry_on_fix` 默认关闭，按对象深合并 |
| `task_cron_system` | task_cron_system | 覆盖 |
| `agents` | agents | 覆盖 |

---

### 白名单规则

所有白名单字段（`shared_whitelist`、`global_whitelist`、`whitelist`）遵循相同规则：
- **空数组 `[]`** = 全部启用
- **有值** = 只启用列出的项
- 白名单配置过滤主智能体的 Prompt 选择和知识检索，不收缩子代理 `agent-config.json` 已授予的能力

> 注意：这些白名单不控制子代理。子代理只服从各自 `agent-config.json`，不与主智能体策略求交集。
