# user_config.json 配置项手册

> 文档版本：v2.1
> 最后核对：2026-07-23
> 事实来源：`template/user/user_config.json`、`run/config.py`、`provider/protocol/models.py`

kemo-agent 用户级配置文件，位于 `users/<用户名>/user_config.json`。用户可在此覆盖全局默认值，也可通过 Web UI 配置面板修改。

目录和数据文件职责见 `user-directory-skeleton.md`；环境变量兜底和密钥优先级见 `env-reference.md`。配置保存后，当前 Run 不会中途改变 Provider 或权限，下一次 Run 才使用新的合并结果；需要重建 RuntimeHost 组件的参数应重启服务。

配置分两类：
- **用户独有段**：`provider`、`agent_models`、`multimodal_models`、`multimodal_routing`、`knowledge`、`skills`、`expand`、`perception`、`plugins` — 只从用户配置读取，全局配置不兜底
- **框架覆盖段**：其余段按对象深合并，用户配置中的字段覆盖全局默认值，未提供的字段继承全局值

---

## schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

## provider — LLM API 配置

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
| `reasoning_effort` | string | `"medium"` | 思考强度：`minimal`、`low`、`medium`、`high`、`max`。缺失、`none` 或非法值统一回退为 `medium`，不可关闭推理 |
| `input_modalities` | string[] | `["text"]` | 主模型已确认支持的输入模态；必须包含 `text`。Chat 只允许增加 `image`；Kemo 还可声明 `audio`、`video`、`file`，并会与网关能力声明交叉验证 |

### 密钥优先级

`api_key`（明文） > `api_key_env`（环境变量名） > 全局 `.env` 兜底

### 地址优先级

1. `user_config.json → provider.base_url`
2. `KEMO_BASE_URL` / `OPENAI_BASE_URL` 环境变量
3. Provider 类型对应的内置默认地址（chat → OpenAI 默认，kemo → `http://127.0.0.1:8741`）

最终地址统一去除尾部 `/`。只有 `chat` 模式自动补全 `/v1`。

---

## agent_models — 子代理专用模型

子代理三档模型配置。任一字段留空时继承 `provider.model`。

| 字段 | 说明 |
|------|------|
| `default` | 普通子代理用模型 |
| `cheap` | 轻量子代理用模型；历史对话摘要也使用此档位 |
| `reasoning` | 推理型子代理用模型 |

---

## multimodal_models — 多模态模型覆盖

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

## multimodal_routing — 多模态路由

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `vision` | `"auto"` | `auto` = 主模型支持图片时优先直传，否则使用专用视觉模型；`main` = 仅主模型；`dedicated` = 仅 `multimodal_models.vision` |

路由对 Web 上传与外部消息资产一致生效。外部消息模块不能自行将图片作为 inline Content Block 发送给主模型。`multimodal` 工具还接受 `paths`：绝对路径或相对项目根目录的明确本地媒体会先登记、验证，再直接交给专用能力模型；这不会改变主模型的 `input_modalities` 声明。

图片文件在后端经过来源目录约束或显式本地路径登记、完整解码、真实格式与大小检查后，Chat 请求才会临时内联；Chat 图片仅接受 JPEG、PNG、WEBP 和 GIF，Base64 不写入 Web 状态或文本历史。Kemo 输入先通过认证 Asset API 流式上传，再以远端 `asset_id` 进入请求；生成结果经下载和 SHA-256 校验后防重名保存到用户 `download` 目录。专用插件不重复携带主对话历史。

识别类动作（图片理解、音频转写、视频理解）只对明确标记为可重试的瞬时错误进行一次额外尝试。生成、编辑和转换类动作不自动重试，避免重复计费或产生重复产物。失败结果不会进入同参数工具结果缓存，因此智能体根据错误分类决定再次调用时会真正发起新请求；同名工具连续失败保护仍然生效。

---

## provider_runtime — Provider 运行时并发控制

覆盖全局 `global_config.json → provider_runtime`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_concurrent_requests` | int | 10 | 进程级总闸，所有 LLM API 请求共享；工具执行期间释放槽位 |
| `request_semaphore_timeout` | float | 300.0 | 获取并发槽位的超时秒数 |

---

## task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto_accept` | bool | `false` | 是否自动批准任务计划。`true` 时计划创建即执行，`false` 时需手动批准 |

---

## tools — 工具调用

覆盖全局 `global_config.json → tools`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `timeout` | int | 240 | 工具未显式提供 `timeout` 时的默认秒数；显式有效参数会覆盖此值，并同时作用于插件内部期限和框架看门狗 |
| `max_iterations` | int | 80 | 单轮最大 Provider 迭代次数 |
| `consecutive_identical_call_limit` | int | 8 | 相同参数连续调用同一工具的容忍上限 |

> 注意：`tools.enabled` 不在用户配置中覆盖，仅全局配置控制。

---

## history — 对话历史

覆盖全局 `global_config.json → history`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `recent_full_rounds` | int | 3 | 保留完整工具/思考日志的最近轮数 |
| `consecutive_tool_fail_limit` | int | 5 | 同名工具连续失败后临时移除的容忍上限 |

---

## prompt — System Prompt 注入控制

覆盖全局 `global_config.json → prompt`。按对象深合并。

| 字段 | 类型 | 说明 |
|------|------|------|
| `char_limits` | object | 各 Prompt 段字符上限。同全局结构：`task_plan`、`perception`、`expand_data`、`skill_prompts`、`plugin_prompts` |
| `injection_mode` | object | 各模块注入模式。同全局结构：`permanent_memory`、`important_memory`、`temporary_seven_days`、`temporary_one_month`、`temporary_half_year`、`knowledge_index`、`task_plan`、`expand_data`、`perception`。可选值：`"full"` / `"truncated"` / `"off"` |

---

## knowledge — 知识库开关

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `use_shared` | bool | `true` | 是否注入共享知识库索引 |
| `use_global` | bool | `true` | 是否注入全局知识库索引 |

> 用户级知识库始终启用，无需开关。三个知识层的注入顺序：用户 > 共享 > 全局（权重从高到低）。

---

## skills — 技能白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享技能白名单。空数组 = 全部启用。填入技能目录名（支持相对路径如 `development/python`）则只启用列出的 |

> 用户技能（`users/<name>/user_skills/`）始终允许，不受白名单控制。技能只注入 Prompt 提示词，不注册可执行工具。`user_whitelist` 已从配置契约删除。

---

## expand — 拓展模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享拓展白名单。空 = 全部启用 |
| `global_whitelist` | array | `[]` | 全局拓展白名单。空 = 全部启用 |

> 用户拓展（`users/<name>/expand/`）始终按当前用户目录动态解析，不受白名单控制。

---

## perception — 感知模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `global_whitelist` | array | `[]` | 全局感知模块白名单。空 = 全部启用 |

> 感知模块位于 `global_sense/`，只采集系统数据，通过 `sense.md` 单向注入 system prompt，不提供操控接口。

---

## kemo_graph — 知识图谱检索开关

四个字段全部默认 `false`。只有当 `global_expand/` 中接入 kemo-graph 模块后才实际生效。

| 字段 | 说明 |
|------|------|
| `kemo_graph_global_knowledge` | 全局知识库 → 图谱检索 |
| `kemo_graph_shared_knowledge` | 共享知识库 → 图谱检索 |
| `kemo_graph_user_knowledge` | 用户知识库 → 图谱检索 |
| `kemo_graph_temporary_memory` | 三层临时记忆 → 图谱检索 |

> 注意：`global_config.json` 中也有同名开关。全局决定"图谱数据是否存在"，用户决定"该用户是否启用"。两者都开才生效。任一开关为 true 但未建立连接时返回 `not_connected`，不回退原始内容。

---

## plugins — 插件白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `whitelist` | array | `[]` | 内置插件白名单。空 = 全部启用。填入插件名则只启用列出的 |

---

## memory — 记忆系统

覆盖全局 `global_config.json → memory`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `extraction_mode` | string | `compression_only` | 记忆提取模式：`disabled` 完全关闭；`compression_only` 仅上下文压缩/保存时提取；`background` 允许后台提取；`on_commit` 每轮同步提取 |
| `recovery_max_rounds_per_scan` | int | 10 | Maintenance 每次扫描最多补提取的总轮数，范围 1–20 |
| `extraction_batch_rounds` | int | 5 | 每次模型分析的连续轮数，范围 1–20 |
| `extraction_max_candidates_per_batch` | int | 10 | 每批候选总上限；仍受每轮最多 2 条限制 |
| `temporary_injection_limits` | object | 100/200/300 | 三层临时记忆注入数量上限：`half_year`、`one_month`、`seven_days` |
| `important_memory_max_chars` | int | 5000 | 临时重要热画像最大字符数；热画像只镜像高价值临时碎片，不截断其正常生命周期 |
| `history_read_enabled` | bool | true | 是否允许智能体使用 `history_search` 工具 |

---

## agent_runtime — 智能体运行时

覆盖全局 `global_config.json → agent_runtime`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `queue_maxsize` | int | 50 | 用户级 `AgentScheduler` 有界队列最大长度；0 表示无界 |
| `default_timeout` | int | 600 | 子代理整体默认超时（秒）；到期自动请求协作式取消并等待清理，不受普通工具默认超时提前截断 |

---

## web — Web 服务

覆盖全局 `global_config.json → web`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_concurrent_chats` | int | 3 | 单用户最大并发聊天数 |
| `max_pending_chats` | int | 5 | 聊天等待队列上限 |
| `pending_chat_timeout` | float | 30.0 | 排队超时（秒） |

---

## message — 外部消息路由

覆盖全局 `global_config.json → message`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `max_workers` | int | 8 | 消息处理线程池大小 |
| `max_queued_messages` | int | 20 | 消息等待队列上限；0 为无界模式 |

---

## cron — 定时调度

覆盖全局 `global_config.json → cron`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `enabled` | bool | true | 是否启用 cron 调度器 |
| `poll_interval` | int | 30 | 任务轮询间隔（秒） |
| `avoid_congestion` | bool | true | 是否启用 Provider 拥塞避免 |
| `congestion_threshold_ratio` | float | 0.2 | 拥塞阈值比例 |

---

## task_cron_system — 系统定时任务

按对象深合并全局 `global_config.json → task_cron_system`。刷新频率由 RuntimeHost 的全局配置统一调度；用户层只适合覆盖自己的模块执行超时。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `sense_update_rate` | int | 5 | 系统级感知刷新间隔；用户配置中的值不改变全局任务频率 |
| `expand_update_rate` | int | 5 | 三层拓展统一刷新间隔；用户配置中的值不改变全局任务频率 |
| `module_update_timeout` | number | 120 | 当前用户拓展的单模块子进程超时（秒），最大 3600 |

---

## agents — 智能体上下文与压缩

覆盖全局 `global_config.json → agents`。按对象深合并。

| 字段 | 类型 | 全局默认值 | 说明 |
|------|------|-----------|------|
| `conserved_rounds` | int | 3 | 保留完整工具/思考日志的最近轮数 |
| `max_rounds` | int | 80 | 上下文最大对话轮数（触发压缩） |
| `rounds_after_compression` | int | 20 | 压缩后保留的轮数 |
| `token_limit` | int | 1000000 | 上下文 Token 上限 |
| `token_compression_ratio` | float | 0.3 | 输入预算比例 |
| `important_memory_review_hours` | int | 3 | 临时重要记忆巡检间隔（小时） |
| `daily_memory_review_time` | str | `"02:00"` | 每日记忆审阅时间（北京时间 HH:MM） |

---

## 完整字段速查

### 用户独有段（仅用户配置，全局不兜底）

| 配置组 | 主要字段 |
|--------|---------|
| `provider` | type, base_url, api_key, api_key_env, model, stream, reasoning_effort, input_modalities |
| `agent_models` | default, cheap, reasoning |
| `multimodal_models` | vision, image_generation, image_edit, audio_transcription, speech_generation, speech_to_speech, video_understanding, video_generation |
| `multimodal_routing` | vision |
| `knowledge` | use_shared, use_global |
| `skills` | shared_whitelist |
| `expand` | global_whitelist, shared_whitelist |
| `perception` | global_whitelist |
| `kemo_graph` | kemo_graph_global_knowledge, kemo_graph_shared_knowledge, kemo_graph_user_knowledge, kemo_graph_temporary_memory |
| `plugins` | whitelist |
| `task_plan` | auto_accept |

### 框架覆盖段（按对象深合并）

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
| `task_cron_system` | task_cron_system | 覆盖 |
| `agents` | agents | 覆盖 |

---

## 白名单规则

所有白名单字段（`shared_whitelist`、`global_whitelist`、`whitelist`）遵循相同规则：
- **空数组 `[]`** = 全部启用
- **有值** = 只启用列出的项
- 白名单配置过滤主智能体的 Prompt 选择和知识检索，不收缩子代理 `agent-config.json` 已授予的能力

> 注意：这些白名单不控制子代理。子代理只服从各自 `agent-config.json`，不与主智能体策略求交集。
