# user_config.json 配置项手册

kemo-agent 用户级配置文件，位于 `users/<用户名>/user_config.json`。用户可在此覆盖全局默认值，也可通过 Web UI 配置面板修改。

---

## schema_version

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `schema_version` | int | `1` | 配置文件结构版本号，用于格式升级时的兼容判断 |

---

## provider — LLM API 配置

**这是用户首次使用时最需要填写的配置组。** `user_create.py` 的交互式引导也只配置这组。

| 字段 | 类型 | 说明 |
|------|------|------|
| `type` | string | Provider 类型。`"kemo"` = Kemo 网关本地适配（兼容所有 OpenAI 格式 API），`"chat"` = 标准 OpenAI Chat Completions |
| `base_url` | string | API 基础地址。Kemo 网关默认 `http://127.0.0.1:8741`，直连 OpenAI 则填 `https://api.openai.com/v1` |
| `api_key` | string | API 密钥。优先级高于 `api_key_env` |
| `api_key_env` | string | 从环境变量读取密钥的变量名。当 `api_key` 为空时生效，如 `"KEMO_API_KEY"` |
| `model` | string | 默认对话模型名，如 `"deepseek-chat"` |
| `stream` | bool | 是否启用流式输出，默认 `true` |

### 密钥优先级

`api_key`（明文） > `api_key_env`（环境变量名） > 全局 `.env` 兜底

---

## multimodal_models — 多模态模型覆盖

默认全部为空字符串，留空时使用 `provider.model` 作为兜底。按需填写特定能力的模型名。

| 字段 | 说明 |
|------|------|
| `vision` | 识图/多模态理解 |
| `image_generation` | 文生图 |
| `image_edit` | 图生图/图像编辑 |
| `audio_transcription` | 语音转文字 |
| `speech_generation` | 文生语音 |
| `speech_to_speech` | 语音生语音 |
| `video_generation` | 视频生成 |

---

## task_plan — 任务计划

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `auto_accept` | bool | `false` | 是否自动批准任务计划。`true` 时计划创建即执行，`false` 时需在 Web UI 手动批准 |

---

## skills — 技能白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享技能白名单。空数组 = 全部启用。填入技能目录名则只启用列出的 |

---

## expand — 拓展模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `shared_whitelist` | array | `[]` | 共享拓展白名单。空 = 全部启用 |
| `global_whitelist` | array | `[]` | 全局拓展白名单。空 = 全部启用 |

---

## perception — 感知模块白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `global_whitelist` | array | `[]` | 全局感知模块白名单。空 = 全部启用 |

---

## knowledge — 知识库开关

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `use_shared` | bool | `true` | 是否注入共享知识库索引 |
| `use_global` | bool | `true` | 是否注入全局知识库索引 |

> 用户级知识库始终启用，无需开关。

---

## kemo_graph — 知识图谱检索开关

四个字段全部默认 `false`。只有当 global_expand 中接入 kemo-graph 模块后才实际生效。

| 字段 | 说明 |
|------|------|
| `kemo_graph_global_knowledge` | 全局知识库 → 图谱检索 |
| `kemo_graph_shared_knowledge` | 共享知识库 → 图谱检索 |
| `kemo_graph_user_knowledge` | 用户知识库 → 图谱检索 |
| `kemo_graph_temporary_memory` | 三层临时记忆 → 图谱检索 |

> 注意：`global_config.json` 中也有同名开关。全局配置中的开关决定"图谱数据是否存在"，用户配置中的开关决定"该用户是否使用"。两者都开才生效。

---

## plugins — 插件白名单

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `whitelist` | array | `[]` | 内置插件白名单。空 = 全部启用。填入插件名则只启用列出的 |

---

## 与 global_config.json 的关系

| 维度 | global_config.json | user_config.json |
|------|-------------------|-----------------|
| 作用域 | 所有用户 | 单个用户 |
| 覆盖性 | 默认值 | 可覆盖全局值 |
| 典型配置 | 系统级参数（超时、限制、调度） | 用户级参数（API 密钥、模型、白名单偏好） |
| 修改方式 | 直接编辑文件或 Web 全局配置面板 | Web 配置面板或 `user_create.py --interactive` |

### 白名单规则

所有白名单字段（`shared_whitelist`、`global_whitelist`、`whitelist`）遵循相同规则：

- **空数组 `[]`** = 全部启用
- **有值** = 只启用列出的项
- 用户级白名单叠加全局白名单取交集
