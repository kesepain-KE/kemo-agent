# kemo-agent 运行手册

本文件是智能体操作自身的完整手册。涵盖架构、安全、资源位置、配置结构、工具、记忆、上下文、子代理、调度、prompt 拼接、provider 和交付标准。

---

## 1. 架构概览

kemo-agent 是一个事件驱动的多用户智能体框架。核心运行流程：

```
请求（user + source + session_id）
  → 加载配置（global_config.json + user_config.json 深合并）
  → 构建 prompt bundle（人格 + 知识索引 + 记忆 + 拓展 + 感知）
  → 上下文选择（轮次预算 + token 预算 + 压缩）
  → Provider 调用循环（流式/非流式）
  → 工具调用循环（注册/发现/执行/超时/去重/取消）
  → 提交五文件历史（text + think + tool + items + data）
  → 记忆引用加权；上下文压缩前批量提取即将裁剪轮次
```

关键模块：

| 模块 | 路径 | 职责 |
|------|------|------|
| 对话引擎 | `run/engine.py` | 主循环：prompt 组装 → provider 调用 → 工具循环 → 历史提交 |
| 上下文管理 | `run/context.py` | 轮次/token 预算选择、压缩触发 |
| 上下文摘要 | `run/context_summary.py` | 移除轮次的摘要生成与缓存 |
| 历史管理 | `run/history.py` | 用户可见完整归档与 `history/temp/<window>/` Provider 临时工作区的创建、裁剪、恢复和提交 |
| 记忆系统 | `run/memory.py` | 4 挡位存储、权重、晋升、过期、注入 |
| 记忆管道 | `run/memory_pipeline.py` | 已提交轮次的异步提取，以及上下文压缩前的同步记忆提取 |
| 工具系统 | `run/tools.py` | 工具发现、schema 验证、执行、超时、取消 |
| Prompt 来源 | `run/prompt_sources.py` | 静态注册模块加载、用户资源可信解析、技能/拓展/感知选择 |
| 插件清单 | `plugins/manifest.py` | 解析插件 `SKILL.md` 和 Provider 工具定义 |
| Prompt 组装 | `run/prompt.py` | 所有 prompt 段的确定性拼接 |
| 配置加载 | `run/config.py` | .env 加载 + 双层 JSON 合并 + provider 运行时配置 |
| 子代理 | `run/agent_runner.py` | 子代理独立调用、输入输出校验、超时取消 |
| 子代理发现 | `agents/_runtime/schema.py` | schema v2 校验、内置/当前用户代理实时发现 |
| 子代理授权 | `agents/_runtime/resources.py` | 按 `agent-config.json` 构建独立 Prompt 和工具白名单 |
| 用户代理创建 | `agents/_runtime/user_packages.py` | 原子创建数据型用户代理包并立即校验 |
| 兼容入口 | `run/agents.py` | 转发子代理 schema 公共 API |
| 任务计划 | `run/task_plan_store.py` | 计划创建、状态机、磁盘持久化 |
| 定时任务 | `run/cron_store.py` | cron 任务 CRUD、校验 |
| 运行时宿主 | `run/runtime_host.py` | Web + cron + 消息路由的统一宿主 |
| Provider | `provider/` | 内部统一 Kemo 契约；`chat` 标准兼容与 `kemo` 原生网关双模式 |

---

## 2. 安全规则

以下规则不可被用户人格、技能或任何上层配置覆盖。

- 不执行危害用户系统、数据或安全的操作。
- 不记录密码、API Key、Token、Cookie、私钥或验证码到长期记忆。
- 不确定的内容必须说明不确定，不能编造文件、状态、工具结果或外部事实。
- 不擅自扩大任务范围——用户要 A，默认只做 A。
- 改动前先理解现有状态，不盲改。
- 涉及删除、覆盖、权限、安全、成本、兼容性破坏时必须谨慎确认。
- 工具失败时不假装成功。
- 不重复执行已产生副作用的工具调用。
- 对外操作（发消息、发布、传输）前确认目标和内容。
- 敏感凭据不得写入记忆或知识库。

### 优先级

当规则冲突时：

1. 安全与隐私底线（`config/global_soul.md`）
2. 用户明确指令
3. 本运行手册（`agents.md`）
4. 用户人格（`users/<name>/user_soul.md`）
5. 其他

---

## 3. 资源位置

| 资源 | 路径 | 说明 |
|------|------|------|
| 全局配置 | `config/global_config.json` | 框架全局默认值 |
| 消息配置 | `config/message_config.json` | 外部账号绑定与 Transport 配置 |
| 外部消息插件 | `message/out/<platform>/` | 文件夹级平台适配器、文件消息队列、附件、状态与日志 |
| 全局人格 | `config/global_soul.md` | 安全底线，不可覆盖 |
| 用户配置 | `users/<name>/user_config.json` | 覆盖全局配置 |
| 用户人格 | `users/<name>/user_soul.md` | 用户偏好与风格 |
| 运行手册 | `agents.md` | 本文件 |
| 插件（工具型） | `plugins/<name>/` | 每个插件含 `SKILL.md` + `tool.py`，注册可调用工具 |
| 共享技能（指令型） | `shared_skills/` | 仅注入提示词，不注册工具 |
| 用户技能（指令型） | `users/<name>/user_skills/` | 仅注入提示词，不注册工具 |
| 全局知识库 | `global_knowledge/` | 所有用户共享，只读（除非用户明确要求写入） |
| 共享知识库 | `shared_knowledge/` | 共享知识层 |
| 用户知识库 | `users/<name>/knowledge/` | 用户私有，优先检索、默认写入 |
| 全局拓展 | `global_expand/` | 标准化全局拓展模块，使用 `expand.json` 控制数据与操控注入 |
| 共享拓展 | `shared_expand/` | 标准化共享拓展模块，按用户主配置过滤 |
| 用户拓展 | `users/<name>/expand/` | 当前用户私有拓展模块，可信自动发现且不执行用户注册代码 |
| 感知模块 | `global_sense/<module>/` | 每个直接子目录为独立模块，必须由 `sense.json` 的 `data_md` 指定唯一注入文件 |
| 内置子代理 | `agents/<name>/` | 受信任代码包：`AGENT.md`、`agent.json`、`agent-config.json`、`executor.py` |
| 用户子代理 | `users/<name>/agents/<agent>/` | 可信热插拔包：`AGENT.md`、`agent.json`、`agent-config.json`、可选 `executor.py` |
| 用户历史 | `users/<name>/history/` | 时间戳目录保存无上限完整归档，`temp/<window>/` 保存受 `agents.max_rounds` 限制的 Provider 工作区 |
| 记忆存储 | `users/<name>/improve/` | 4 挡位记忆数据 |
| 任务计划 | `users/<name>/task_plan/` | 计划文件 |
| 定时任务 | `users/<name>/task_cron/` | cron 任务文件 |
| 用户下载产物 | `users/<name>/download/` | 智能体生成的文件 |
| 用户上传文件 | `users/<name>/file_upload/` | 用户上传的附件 |
| 环境变量 | `.env` | 启动级参数和密钥兜底 |

### 外部消息插件发现规则

- RuntimeHost 启动时只扫描 `message/out/` 的直接子目录，并只加载 `message.json` 明确声明的 `input`、`output`、`detect` 三个模块；目录中的其他 Python 文件不会被核心自动执行。
- `message.json.bound_user` 将整个平台插件绑定到一个现有内部用户；传统 Transport 仍使用 `config/message_config.json` 的外部身份映射。
- `message.md` 是可恢复的文件队列。群聊中同一外部会话的累积消息合并为一次 Run；私聊逐条处理。
- 附件必须位于插件的 `files_dir` 内。图片、音频和 PDF 转换为 Kemo Content Blocks；文本文件直接并入请求；视频和未知类型仅注入文件说明。
- 每条终态结果写入 `log/YYYY-MM-DD.md`，随后删除本批次已处理附件；健康检测结果与收发计数写入 `state.json`。

### 插件发现规则

- 扫描 `plugins/*/SKILL.md`，解析 `## Tool` 下的 JSON 块。
- 插件标题、工具名、目录名三者必须一致。
- Provider 可执行工具只来自 `plugins/`；共享技能和用户技能不能注册工具。

### 技能发现规则

- 扫描 `shared_skills/*/SKILL.md` 和 `users/<name>/user_skills/*/SKILL.md`。
- 仅注入提示词描述，不注册可调用工具。
- 项目静态来源由根目录的 `register.py` 注册；当前用户的技能和拓展由可信运行时按目录约定解析，用户目录不加载 Python。
- 详细注册契约见 `config/prompt_registries.md`。

---

## 4. 用户配置结构

用户配置文件 `users/<name>/user_config.json` 覆盖全局配置 `config/global_config.json`。
`provider`、`multimodal_models`、`knowledge`、`skills`、`expand`、`perception`、`plugins`
是用户专属段，只从用户配置读取，不允许全局配置兜底；其他框架段按对象深合并。

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | int | 配置结构版本号，当前固定为 1 |
| `provider` | object | LLM 提供商配置 |
| `multimodal_models` | object | 多模态模型名（设计预留） |
| `task_plan` | object | 任务计划配置 |
| `tools` | object | 工具开关、超时、最大循环次数 |
| `history` | object | 历史保留策略 |
| `prompt` | object | prompt 字符上限和注入模式；人格、手册和记忆基础段默认启用 |
| `knowledge` | object | 知识库检索配置 |
| `skills` | object | 共享 Prompt 技能白名单；用户技能始终允许 |
| `expand` | object | 全局/共享 Expand 白名单 |
| `perception` | object | 全局感知模块白名单 |
| `kemo_graph` | object | 知识与记忆 Prompt 来源替换器 |
| `plugins` | object | 可执行插件白名单 |
| `memory` | object | 记忆挡位、注入上限与历史读取工具开关 |
| `agent_runtime` | object | 子代理运行时参数 |
| `cron` | object | cron 调度配置 |
| `agents` | object | 上下文管理参数（轮次/token 上限等） |

### 主智能体来源控制

用户合并配置在 Prompt 选择与知识检索阶段控制主智能体，不改变注册阶段的完整库存：

| 配置 | 语义 |
|------|------|
| `knowledge.use_shared` | 是否加入共享知识范围；知识管线默认启用 |
| `knowledge.use_global` | 是否加入全局知识范围；用户知识始终有效 |
| `plugins.whitelist` | 插件白名单；同时过滤 Provider 工具 schema 与插件 Prompt 清单 |
| `skills.shared_whitelist` | 共享 Prompt 技能白名单 |
| `expand.global_whitelist` | 全局 Expand 白名单 |
| `expand.shared_whitelist` | 共享 Expand 白名单；用户 Expand 始终按当前用户目录动态解析 |
| `perception.global_whitelist` | `global_sense/` 直接子目录模块白名单 |
| `kemo_graph.kemo_graph_global_knowledge` | 仅以图谱替换全局知识库索引 |
| `kemo_graph.kemo_graph_shared_knowledge` | 仅以图谱替换共享知识库索引 |
| `kemo_graph.kemo_graph_user_knowledge` | 仅以图谱替换用户知识库索引 |
| `kemo_graph.kemo_graph_temporary_memory` | 仅以图谱替换 half_year、one_month、seven_days 三层临时记忆；永久记忆与临时重要记忆始终保留 |

主智能体白名单 `[]` 表示全量允许；非空数组按资源 ID 精确匹配。技能 ID 支持相对路径（如 `development/python`）。`"*"` 不属于主配置协议。

`knowledge.enabled` 与 `skills.user_whitelist` 已从配置契约删除，继续提供会被判定为未知字段。
任一 `kemo_graph` 替换开关为 `true` 时不会自动启动外部项目；未建立连接接口时明确返回
`not_connected`，并且不回退注入该开关已替换的原始知识或临时记忆内容。

这些字段不控制子代理。子代理只服从各自 `agent-config.json`，不与主智能体策略求交集。

### provider 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | `"chat"` 或 `"kemo"`；启动前选择，运行中不自动回退 |
| `base_url` | string | API 地址；chat 模式自动补全 `/v1`，kemo 模式保持配置的协议根地址 |
| `api_key` | string | 用户独立密钥（优先读取），为空时读环境变量兜底 |
| `api_key_env` | string | 环境变量名，kemo 默认 `KEMO_API_KEY`，chat 默认 `OPENAI_API_KEY` |
| `model` | string | 主对话模型名 |
| `stream` | bool | 是否流式输出，默认 true |

Provider 单次请求超时固定由源码设为 120 秒；用户配置不再接受 `timeout` 或 `headers`。

### multimodal_models 子字段（设计预留）

| 字段 | 说明 |
|------|------|
| `vision` | 图片分析/OCR 模型 |
| `image_generation` | 文生图模型 |
| `image_edit` | 图生图模型 |
| `audio_transcription` | 语音转文字模型 |
| `speech_generation` | 文生语音模型 |
| `speech_to_speech` | 语音转语音模型 |
| `video_generation` | 视频生成模型 |

不含 embedding 和 rerank。

### task_plan 子字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `auto_accept` | bool | 是否自动执行计划，默认 false |

详细结构说明见 `global_knowledge/users-structure.md`。

---

## 5. 工具使用规则

- 仅调用当前注册且已启用的工具，参数应符合工具 Schema。
- 工具结果是外部事实来源；调用失败时不得假装成功。
- 不重复执行已经产生副作用的工具调用（框架层有签名去重）。
- 工具执行有超时限制（`tools.timeout`，默认 240 秒）。
- 工具循环有最大次数限制（`tools.max_iterations`，默认 8 次）。
- `tools.max_per_round` 是软上限；达到后提交已执行与延期调用的完整状态，
  返回等待用户继续的信号。`null` 表示不限。
- 同一工具连续失败达到 `history.consecutive_tool_fail_limit` 后，本轮会从
  Provider 工具 schema 中临时移除；其他工具穿插执行会重置连续失败计数。
- 用户取消时立即停止，不继续执行后续工具调用。
- 工具上下文注入 `root`、`user`、`source`、`session_id`、`window`、`tool_timeout`，不注入主对话历史。
- 当前已注册工具见 `plugins/` 目录，每个插件的 `SKILL.md` 描述触发条件和参数。

---

## 6. 记忆系统

### 4 挡位

| 挡位 | 有效期 | 晋升阈值 | 升级目标 |
|------|--------|----------|---------|
| `seven_days` | 7 天 | 权重 ≥ 3 | `one_month` |
| `one_month` | 30 天 | 权重 ≥ 10 | `half_year` |
| `half_year` | 180 天 | 权重 ≥ 60 | `permanent` |
| `permanent` | 永不过期 | — | — |

### 权重规则

- 不做每日权重衰减。
- 临时记忆被 self_improve 命中、正文实际修改或被 Prompt 实际引用时可加权；这些行为共用每日锁，同一记忆每天合计最多 `+1`。
- 引用和修改都会更新 `updated_at`，但不会重置进入当前层时固定的 `expires_at`。
- 到期未达晋升阈值直接删除，不降级保留。
- 晋升后新挡位权重从 0 重新累计。
- 仅实际注入 Prompt 并成功完成任务的引用才被加权；失败和取消不加权。
- 永久记忆没有索引、权重或到期时间。

### 文件存储

- 文件名是全部记忆层级中的全局唯一身份，基础名称最长 20 个字符。
- 一个 Markdown 只保存一个微量化事实、偏好、关系或项目状态；同名写入更新原文件。
- 临时三层正文存为 Markdown，同目录 `data.json` 只保存文件名、weight、updated_at、last_weight_date 和 expires_at。
- 永久层只保存 Markdown，不存在 `data.json`。
- 当前文件检索只使用文件名，不接入关键词、实体、向量或 `kemo-graph`。

### 注入

- 永久记忆全部注入，不设置文件数量上限。
- 临时三层按 `half_year → one_month → seven_days` 排列，层内按权重从高到低选择。
- `memory.temporary_injection_limits` 只限制单次 Prompt，不限制磁盘存储数量。
- 临时重要记忆（`memory_temporary_important.md`）独立注入，有字符上限。
- 不再逐轮异步提取；`context_manage` 在上下文压缩前把即将裁剪的完整轮次批量交给 `self_improve`。

### 用户指令

- 用户明确要求记住或忘记时，遵循记忆存储规则。
- 敏感凭据不得写入记忆。

---

## 7. 上下文管理

### 预算

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `agents.conserved_rounds` | 3 | 最近 N 轮保留完整工具日志 |
| `history.recent_full_rounds` | 3 | 最近 N 轮完整历史不被摘要或移除 |
| `agents.max_rounds` | 80 | temp 工作区及 Provider 上下文的轮次上限；不限制用户可见归档 |
| `agents.rounds_after_compression` | 20 | 压缩后保留的轮次数 |
| `agents.token_limit` | 1000000 | token 上限 |
| `agents.token_compression_ratio` | 0.3 | 输入预算 = token_limit × ratio |

- `input_budget = token_limit × compression_ratio`
- `output_reserve = token_limit - input_budget`

### 压缩触发

- **轮次触发**：投影轮次 ≥ `max_rounds` 时触发。
- **Token 触发**：估算总 token 超过 `token_limit` 时触发。
- **Provider 触发**：统一协议或兼容 Provider 返回 `context_length_exceeded` 时，自动压缩并重试，最多 2 次。
- **手动触发**：请求带 `compress=true` 时强制压缩。
- 压缩时移除最旧轮次，保留最近 `rounds_after_compression` 轮。
- 所有场景统一由 `context_manage` 处理；其 executor 先同步调用 `self_improve` 并持久化记忆候选，再生成摘要。
- 摘要缓存在 `history/temp/<window>/context_summary.json` 中；缓存 schema 升级时自动重建。
- `history/<window>/` 始终保留完整原始记录，`data.json` 不保存 context/summary 诊断；temp 丢失时仅从归档恢复最近 `max_rounds` 轮。

### 轮次结构

每个对话轮次包含：
- `text.json` 中的 user + assistant 消息
- `think.json` 中的思考记录
- `tool.json` 中的工具调用记录

每轮提交后只检查一个刚越过 `conserved_rounds` 保护线的轮次：思考和工具记录由 `context_manage` 压缩到 temp 工作区，归档中的原始 `think.json`、`tool.json` 和 `items.json` 保持不变。旧数据迁移期间仍保留代码内建的工具结果字符上限作为降级保护。

---

## 8. 子代理系统

### 当前已注册子代理

| 子代理 | 路径 | 职责 |
|--------|------|------|
| `context_manage` | `agents/context_manage/` | 统一处理轮次、Token、API 超限和逐轮工具/思考压缩 |
| `self_improve` | `agents/self_improve/` | 记忆提取与自我改进 |
| `memory_temporary_important` | `agents/memory_temporary_important/` | 临时重要记忆处理 |
| `task_plan` | `agents/task_plan/` | 任务计划生成与执行 |
| `time_plan` | `agents/time_plan/` | 定时任务处理 |

### 包结构

内置代理位于 `agents/<name>/`，是受信任代码包：

```text
agents/<name>/
├── AGENT.md
├── agent.json
├── agent-config.json
├── trigger.md
└── executor.py
```

用户代理位于 `users/<user>/agents/<name>/`，可按需携带可信的自定义执行代码：

```text
users/<user>/agents/<name>/
├── AGENT.md
├── agent.json
├── agent-config.json
├── trigger.md
└── executor.py（可选）
```

- `agent.json` 精简为 `name`、`version`、`description`、`trigger` 四个字段。执行方式、写入策略和兼容模型标签由运行时按内置代理名补全；超时读取 `agent_runtime.default_timeout`。
- `agent-config.json` 是运行时强制授权，不是说明文档；它声明 `internal_mode`、调用方、插件/共享技能白名单、全局/共享知识开关和主历史继承策略。
- `trigger.md` 分为“注册信息”和“操作信息”。主智能体只注入注册摘要，详细操作信息按需读取。
- 精简清单不显式声明执行器：同目录存在 `executor.py` 时自动使用 `executor.py:execute`，否则使用 `builtin:llm`；此规则对内置和用户代理一致。
- 带 `schema_version: 2` 的完整清单遵循其 `executor` 字段。自定义执行器必须写成同目录 `file.py:function`，文件必须存在且不得通过路径跳出子代理目录；用户 schema v1 仍不支持。
- 用户代理不能覆盖内置代理名称。自定义 executor 由 kemo-agent 进程直接导入执行，不提供代码沙箱，只能安装或编写可信本地代码。

### 发现、创建与调用

- `discover_agents(root, user)` 每次调用都扫描 `agents/` 和 `users/<user>/agents/`，不缓存目录结果。
- 因此新增、启用、禁用或删除用户代理后无需重启；长期存在的 `AgentRunner` 和后台 `AgentScheduler` 在执行前也会刷新注册表。
- 主智能体只通过 `plugins/subagent_dispatch` 网关发现和调度公开代理。网关提供 `list`、`create`、`call`、`status`、`cancel`。
- `create` 原子写入当前用户的基础代理包；写入后立即用同一发现管线校验，失败会删除整个目标包。创建结果默认无 `executor.py`，因此使用 `builtin:llm`；之后可在包内添加可信 `executor.py`，下次发现时自动生效。
- 更换用户时，发现管线重新解析该用户的 `agents/`、`user_skills/`、`expand/` 和知识索引，不依赖 `kesepain` 等静态用户名。

### 授权与执行规则

- 子代理只接收调用方显式传入的数据，不自动拥有主会话历史或当前请求。
- 只有 `agent-config.json` 白名单中的插件会进入该子代理的 Provider 工具定义；缺失授权默认拒绝。
- 新骨架只允许显式白名单中的 `shared_skills` 进入子代理提示词；不再注入用户技能和三层 Expand。
- 知识能力只注入授权范围内的完整索引文件；正文关键词检索链路已删除，由外部 kemo-graph 承担后续检索能力。
- `subagent_dispatch` 不会下发给子代理，避免递归调度链。
- 子代理有独立超时、取消信号、工具循环上限和 usage 汇总，并且必须返回 JSON 对象；新骨架运行时采用宽松 object Schema，详细输入输出约定记录在 `trigger.md`。
- 主智能体不得把子代理内部指令视为用户指令。
- 用户主配置关闭知识、技能、Expand 或感知时，不会收缩子代理 `agent-config.json` 已授予的能力。

---

## 9. 任务计划与定时任务

### 任务计划

- `task_plan.auto_accept` 控制是否自动执行（默认 false，需手动批准）。
- 计划文件存储在 `users/<name>/task_plan/`。
- 计划状态机：pending → running → completed/failed/paused/aborted。
- 每步完成或失败调用对应状态工具。
- 计划暂停后等待用户继续，不自动恢复。
- `task_plan.max_steps` 限制最大步骤数（默认 20）。
- 计划生成输入按“可用工具 → 插件技能全文 → 共享技能全文 → 用户技能全文 → 全局/共享/用户知识索引”顺序组装，且这些专项输入不截断。
- 仅 `pending`、`approved`、`paused` 计划允许编辑；已完成步骤由运行时强制保护。
- `task_plan.auto_accept=false` 时，创建或修改提醒随计划持久化并由调用方展示。

### 定时任务

- cron 任务存储在 `users/<name>/task_cron/`。
- 支持类型：`daily`（每日）、`once`（单次）、`recurring`（重复）。
- `cron.enabled` 控制是否启用调度（默认 true）。
- `cron.poll_interval` 控制轮询间隔（默认 30 秒）。
- `runtime_host.enable_background_scheduler` 控制统一后台调度器；启用时宿主
  自动管理 Cron 与上下文整理。
- 普通任务通过主智能体执行；系统任务可通过 `subagent` 直调内部子代理，或通过白名单 `function` 模式执行内部函数。
- 系统自动注册临时重要记忆巡检、每日整理，以及每 30 秒一次的 self_improve 到期晋升检查。

---

## 10. Prompt 拼接顺序

system prompt 按以下固定顺序拼接：

1. **用户人格** — `users/<name>/user_soul.md`
2. **全局人格** — `config/global_soul.md`
3. **运行手册** — `agents.md`（本文件）
4. **全局子代理注册** — `agents/<name>/trigger.md` 的注册信息
5. **用户子代理注册** — `users/<name>/agents/<agent>/trigger.md` 的注册信息
6. **插件提示词** — `plugins/*/SKILL.md` 的描述部分
7. **技能提示词** — 注册全部共享/用户技能后，按主智能体白名单选择描述部分
8. **知识库索引** — 按 `knowledge.use_shared/use_global` 选择用户 + 共享 + 全局索引
9. **kemo-graph** — 六个独立图谱子层的检索结果或未连接状态
10. **永久记忆** — `improve/permanent/`
11. **临时重要记忆** — `memory_temporary_important.md`
12. **临时记忆 half_year** — `improve/half_year/`
13. **临时记忆 one_month** — `improve/one_month/`
14. **临时记忆 seven_days** — `improve/seven_days/`
15. **任务计划** — 当前活跃计划的描述
16. **拓展数据** — 三层模块均由 `expand.json` 控制；健康输入数据与操控手册 `## 注入层` 可进入 Prompt，`## 操作层` 和 Python 入口只按需读取/执行
17. **感知文件** — `global_sense/<module>/sense.json` 声明 `data_md` 唯一文件，按模块白名单过滤；无效模块进入诊断但不注入

每段有字符上限配置（`prompt.char_limits`）。知识正文不自动注入，只注入索引；需要正文时使用显式搜索机制或工具。
图谱替换按来源独立生效：被替换的知识索引和三层临时记忆仍保留固定 Prompt 段，但正文改为“已被知识图谱替代”；`kemo_graph` 按用户、共享、全局知识与半年、一月、七天记忆拆成六个子层逐项报告。未连接时不回退原始内容。永久记忆和临时重要记忆始终保留。

---

## 11. 会话隔离

- 每个请求属于明确的 `user`、`source` 和 `session_id`。
- 同一用户可共享记忆和知识库，但不同来源与会话的对话历史互相隔离。
- 不假设拥有未注入的其他会话内容；`memory.history_read_enabled=true` 时可使用历史搜索工具。
- 工具上下文只包含运行所需的 `root`、`user`、`source`、`session_id`、`window`、`tool_timeout` 及授权策略字段，不包含主对话历史。
- 会话级锁（`_session_lock`）保证同一 user/source/session_id 的请求串行执行。

---

## 12. Provider 与多模态

### Provider 类型

- `chat`：通过正式 Chat Bridge 访问 `/v1/chat/completions`。保证 Kemo 内部文本/工具循环，并支持标准 `image_url` 图片输入；不提供音视频、媒体输出、Provider State 或 SSE 恢复。
- `kemo`：通过原生 Kemo Provider，提供 Asset、最大程度多模态、统一 Usage、Provider State、查询取消和流恢复。
- 两种模式在一次 Run 开始前固定；任何错误都不得触发跨协议自动回退。

### 密钥优先级

1. `user_config.json` 的 `provider.api_key`（用户独立密钥）
2. 环境变量 `KEMO_API_KEY` / `OPENAI_API_KEY`（兜底）
3. 都没有则报错

### Provider 地址优先级

1. 当前 `user_config.json` 中的 `provider.base_url`
2. `KEMO_BASE_URL` / `OPENAI_BASE_URL`
3. Provider 类型对应的内置默认地址

最终地址统一去除尾部 `/`。只有 `chat` 模式自动补全 `/v1`；`kemo` 模式默认协议根地址为 `http://127.0.0.1:8741`。

### Web 启动与认证

- Web 监听参数优先级为：显式 CLI 参数 > `WEB_HOST` / `WEB_PORT` > `127.0.0.1:1357`。
- `WEB_ACCESS_TOKEN` 启用 Token 认证；访问 URL 使用 `?token=...` 建立会话，Web 不读取 `Authorization: Bearer`。
- `WEB_USERNAME` 与 `WEB_PASSWORD` 必须同时配置；它们表示 Web 页面使用者，与内部多用户目录无关。
- `WEB_SESSION_SECRET` 为空时启动过程自动生成 64 字符随机密钥；签名会话默认有效期 2 小时。
- `WEB_SESSION_COOKIE_NAME` 用于多实例 Cookie 隔离。
- Web 使用 HttpOnly 签名会话 Cookie。未认证请求只能访问静态前端、`/api/health`、`/api/logo` 和 `/api/auth/*`，其余业务 API 统一返回 401。
- Settings 和 Health 只返回认证状态，不返回 Token、用户名、密码、Session Secret 或 Cookie 内容。
- Web 用户配置接口只返回脱敏后的只读镜像，不提供配置写入路由。
- Web 可只读查看 Prompt/Expand 诊断、记忆预览、当前用户子代理、消息插件状态、摘要缓存与真实 RuntimeHost 状态；独立 Web 模式明确显示 `unmanaged`。
- Web 文件 API 只允许浏览、下载或删除 `file_upload`、`download` 和 `tmp` 中的普通文件；拒绝路径穿越、目录删除、符号链接和隐藏缓存项。头像上传限制为 5 MB 的 PNG/JPEG/GIF/WebP，并校验 MIME 与文件签名。
- 用户人格和全局人格可通过受保护 Web API 原子更新；全局人格影响所有用户，当前唯一 Web 认证主体视为管理员。`user_config.json` 仍保持只读。
- Web 前端的 `/files` 页面落地用户文件与 `tmp` 浏览、下载和二次确认删除；`/runtime` 页面落地子代理、外部消息状态与三层 Expand 库存；`/profile` 页面落地头像上传和用户/全局人格编辑。
- 侧边栏品牌图使用公开 `/api/logo`，用户卡片头像使用受保护 `/api/users/{user}/avatar`；头像不存在或加载失败时回退首字母占位，不阻塞页面使用。
- 聊天请求使用高熵 `run_id`。运行中引导通过独立 guidance 队列提交，只在 Provider 完成或工具调用结束后的安全边界注入，不会中断正在阻塞的 Provider/工具函数。
- 每轮历史在 `data.json.round_metrics` 保存 usage、缓存 Token、耗时、工具调用数和已消费 guidance；`tool.json` 的每次调用保存 `elapsed_ms`。

### 多模态

- `MULTIMODAL_CAPABILITIES` 定义了支持的能力集合：vision、image generation/edit、audio ASR/TTS、speech_to_speech、video generation。
- 多模态模型名通过 `multimodal_models` 配置；专用模型为空时使用 `provider.model`。
- 不含 embedding 和 rerank。
- `chat` 模式的可移植基线仅包含文本、工具调用和图片输入；图片直接发送给视觉模型，不先调用识图工具。
- `kemo` 模式通过 Asset 与能力声明使用网关实际支持的完整多模态能力。

### 网络与插件环境

- Provider 和基于标准库的 HTTP 请求自动遵循 `HTTP_PROXY` / `HTTPS_PROXY`；留空时直连。
- TLS 证书校验始终使用系统默认安全策略，不再支持 `HTTP_VERIFY_SSL` 绕过。
- `TAVILY_API_KEY` 为空时，运行时工具策略会移除 `web_search`；配置有效 Key 后才向 Provider 暴露该工具。

---

## 13. 错误处理

- 同一操作连续失败 2 次后暂停分析失败原因，不盲目重试。
- 连续失败 3 次必须停止，向用户报告：操作目标、错误信息、需要的帮助。
- 工具调用失败时记录错误类型和消息，不伪造结果。
- Provider 错误区分：auth（不可重试）、timeout（可重试）、connection（可重试）、其他 HTTP 错误按状态码判断。
- Provider 在首轮调用返回上下文超限时，丢弃失败尝试的增量事件，调用 `context_manage` 压缩后重试；工具循环中途仍停止，避免拆散工具消息组。
- 记忆提取失败不回滚已提交的历史。

---

## 14. 交付标准

- 修改前读取目标及相邻内容，使用最小修改面。
- 成功提交的对话才写入正式历史；失败或取消不伪造完整轮次。
- 完成后说明：
  - 做了什么
  - 验证结果
  - 仍存在的限制
  - 下一步建议
- 正文使用普通 Markdown。
- 工具结果自动进入下一轮上下文。
- 不确定是否应展示为 artifact 时保留文本。
