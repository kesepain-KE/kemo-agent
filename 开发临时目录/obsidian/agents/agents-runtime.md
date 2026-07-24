---
type: component
project: kemo-agent
domain: agents
module: agents-runtime
layer: L2
scope: project
status: active
summary: agents/_runtime/ — 子代理运行时基础（history_summary Agent + agent_models 配置 + strict I/O schema 可选加载）
source: agents/_runtime/
updated: 2026-07-23
verified: true
tags: [kemo-agent, agents, runtime, schema, prompt装配, executor, manual_review, memory_gate, context_manage_boundary, history_summary, agent_models, strict_schema]
---
# agents/_runtime/ — 子代理运行时基础

`agents/_runtime/` — 子代理的发现、清单解析、prompt 装配和工具注册。

## 文件结构

| 文件 | 职责 |
|------|------|
| `__init__.py` | Trusted runtime package 标记 |
| `schema.py` | AgentDefinition / AgentRegistry / AgentCapabilities 数据契约 + discover_agents() |
| `resources.py` | build_agent_prompt_bundle() + build_agent_tool_registry() 装配逻辑 |
| `user_packages.py` | 用户代理包管理 |
| `user_resources.py` | 用户资源管理 |

## schema.py — 发现与清单

### 核心类

- `AgentDefinition` — 单个子代理的完整定义
- `AgentRegistry` — 注册表容器
- `AgentCapabilities` — 能力配置
- `AgentError` / `AgentManifestError` / `AgentDisabledError`

### discover_agents(root, user=None)

- 扫描 `agents/`（内置）和 `users/<user>/agents/`（用户）
- 校验：名称/目录一致、schema 版本、指令文件可读、执行器不越界

### 执行器自动检测（2026-07-22 变更）

旧版：用户代理固定 `builtin:llm`，禁止 Python 文件；内置代理固定 `executor.py:execute`。

新版：**精简清单不显式声明执行器**，由运行时自动检测：

```python
executor_path = path.parent / "executor.py"
executor = "executor.py:execute" if executor_path.is_file() else "builtin:llm"
```

此规则对内置和用户代理一致。带 `schema_version: 2` 的完整清单遵循其 `executor` 字段。

### 用户代理权限变更

| 子代理 | 旧 internal_mode | 新 internal_mode | 旧 allowed_callers | 新 allowed_callers |
|--------|:---:|:---:|------|------|
| context_manage | true | **false** | ["engine"] | **["main_agent", "engine"]** |
| memory_temporary_important | true | **false** | ["scheduler"] | **["main_agent", "scheduler"]** |
| self_improve | true | **false** | ["scheduler", "context_manage"] | **["main_agent", "scheduler", "context_manage"]** |
| task_plan | true | **false** | ["main_agent"] | ["main_agent"] |
| time_plan | true | **false** | ["main_agent"] | ["main_agent"] |

主智能体现在可以通过 `subagent_dispatch` 主动调度 `context_manage`、`memory_temporary_important` 和 `self_improve`。

## context_manage 职责变更（2026-07-23）

context_manage 不再负责记忆提取：

- **旧职责**：裁剪旧轮次 → 调 self_improve 提取记忆 → 自身生成结构化摘要
- **新职责**：引擎游标先完成延期记忆提取 → context_manage 只生成摘要
- executor 接收 `skip_memory_extraction=True` 时跳过记忆提取
- 不经过引擎的旧式直接调用保留 executor 兼容行为

## self_improve 记忆价值硬门槛（2026-07-23 新增）

```python
# 失败关闭策略：默认不创建记忆
```

**该记**：用户偏好/身份/长期目标/设备稳定事实（已确认）、架构决策/技术偏好（视为工作记忆）、配置值与偏好、拓展模块信息、行为纠正规则。

**不该记**：工具/插件清单、一次性测试、任务运行状态、报错诊断、未确认过程状态（含"待验证"措辞）、用户问题本身、敏感凭据。

来源标记为"对话摘要"不构成拒绝理由。

### 候选过滤（executor.py）

```python
accepted, rejected = [], 0
for candidate in result.data["candidates"]:
    if not (candidate.get("durable") is True and candidate.get("evidence")):
        rejected += 1
        continue
    accepted.append(candidate)
```

- 仅允许 `durable=True` 且有 `evidence` 签名的候选进入持久化
- 单轮最多 2 条，批量调用最多 5 条
- 无符合条件的候选返回空数组

### 工作记忆晋升特征

180d→permanent 晋升时触发技能生成。新增特征：
- 配置值与配置偏好（端口策略、超时默认值等）
- 拓展模块（expand）用途与配置信息
- 仅限已确认的稳定事实

## self_improve 执行器变更（2026-07-21）

### 第三触发模式：manual_review

```python
TRIGGERS = frozenset({"context_compression", "memory_promotion", "manual_review"})
```

- 输入：`{ trigger: "manual_review", request: "用户的具体审阅/整理/搜索要求" }`
- 执行器使用 memory_manage 搜索相关记忆，返回 `candidates`
- 执行器自动将 candidates 写入 `MemoryStore`（通过 `upsert_candidates`）
- 纯搜索允许返回空 candidates 并在其他输出字段中说明结果
- `request` 必须是非空字符串
- 手动模式不执行层间晋升；晋升仍只走 `memory_promotion`

### executor.py 变更

- `TRIGGERS` 集合新增 `"manual_review"`
- 输入校验：manual_review 需要非空 `request` 字符串
- 输出校验：manual_review 需要 `candidates` 数组
- 执行器在 manual_review 模式下使用 `MemoryStore.upsert_candidates()` 持久化结果
- `result.metadata["memory_update"]` 记录持久化元数据

## memory_temporary_important 触发变更（2026-07-21）

新增第三路触发：主智能体通过 `subagent_dispatch` 主动唤起。

| 调用方 | 路径 | 说明 |
|--------|------|------|
| cron `CronScheduler` | `AgentRunner.run()` 直调 | 按 schedule 自动触发 |
| 主智能体 | `subagent_dispatch` 工具 | 用户手动要求触发巡检或每日整理 |

## time_plan executor 变更

`agents/time_plan/executor.py` 从简单 `context.run_model(input_data)` 扩展为带输入输出校验的执行器：

- 输入校验：action 必须是 create/edit/delete，按 action 检查必需字段
- 输出校验：action 一致性、type 必须是 recurring/daily/once、recurring interval >= 60、daily time 格式 HH:MM、prompt 非空
- 支持 skip 动作跳过创建

## history_summary 子代理（2026-07-23）

`agents/history_summary/` — 为已关闭的对话生成卡片标题与摘要。

### 内置默认值

- `execution: background_serial` — 由 MaintenanceScheduler 串行领取
- `write_policy: derived_cache` — 输出直接写入 history_index 记录
- `model_profile: cheap` — 使用 `agent_models.cheap` 档位模型（经济模型）

### strict I/O Schema

`schema.json` 可选文件，定义了 `input_schema` 和 `output_schema`。`_load_package_schemas()` 从 `schema.json` 加载严格模式，不存在时使用 `_LOOSE_OBJECT_SCHEMA` 宽松模式。检测未知字段时抛 `AgentManifestError`。

### agent_models 配置（2026-07-23 新增）

`config.py` 新增 `resolve_agent_model()` 函数，按 `model_profile` 档位选择子代理模型：

```python
profiles = frozenset({"default", "cheap", "reasoning"})
```

- 三档模型在 `user_config.json.agent_models` 中配置（`default`/`cheap`/`reasoning`）
- 任一档位留空时继承 `provider.model`
- `agent_runner.py` 的 `resolve_agent_provider_config()` 调用此函数，在 provider 配置中注入 `model` 和 `model_profile` 字段

### AgentOutputError 扩展

`AgentOutputError` 新增 `raw_text` 属性，保存 JSON 解析失败时的原始输出文本，供 Maintenance 错误日志使用。

### time_plan trigger.md 新增硬性调用规则

- 用户自然语言创建/修改定时任务时，主智能体必须先调用 time_plan，再调用 task_time
- 编辑已有任务时，应先用 `task_time get` 读取现有任务，再交给 time_plan
- 只有程序已经确定完整参数时才能直接调用 task_time

## 相关笔记

- [[agents-总览]]
- [[run-prompt_sources]]
- [[run-knowledge]]
- [[run-tools]]