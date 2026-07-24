---
type: component
project: kemo-agent
domain: agents
module: agents-registry
layer: L2
scope: project
status: active
summary: agents/_runtime/schema.py — 子代理注册表与发现
source: agents/_runtime/schema.py
updated: 2026-07-21
verified: true
tags: [kemo-agent, agents, registry, discover, schema-v2]
---
# agents/_runtime/schema.py — 子代理注册表与发现

## 职责

- 定义 AgentDefinition / AgentRegistry / AgentCapabilities 数据模型
- 扫描 `agents/` 和 `users/<user>/agents/` 发现子代理
- 校验清单完整性（schema 版本、指令可读、执行器安全）
- 构造 AgentRegistry 供调度使用

## 数据模型

### AgentDefinition

核心字段：
- **基础**：name, version, description, enabled, instruction, executor, timeout, execution, write_policy, input_schema, output_schema
- **来源**：source(builtin/user), directory, manifest_path, config_path
- **trigger 系统（新增）**：trigger_file(string), trigger_content(string), trigger_registration(string)

### AgentCapabilities

能力配置（来自 agent-config.json v2）：
- exposure: internal / tool（由 `internal_mode` 布尔值推导）
- allowed_callers: 允许调用方列表
- plugin_tools / shared_skills: 工具白名单
- max_iterations: 工具循环上限（默认 20）
- global_knowledge / shared_knowledge: 知识库范围布尔开关
- inherit_main_history / inherit_current_request: 上下文继承设置

移除 `knowledge.body_access`、`knowledge.max_index_chars`。

### AgentRegistry

查询方法：
- `get(name)` — 获取指定代理（已禁用则报错）
- `enabled_agents()` — 所有已启用代理
- `public_agents(caller)` — 对外暴露且允许当前调用方调用的代理

## 发现机制

### 双模式清单加载

`_load_manifest()` 按 `agent.json` 是否含 `schema_version` 字段分流：

**精简模式**（无 `schema_version`，新设计）：
- 清单仅含 `name/version/description/trigger` 四字段（`_COMPACT_MANIFEST_FIELDS` 校验）
- 读取同目录 `trigger.md` → 解析 `# 注册信息` 段为 `trigger_registration`
- 由 `_BUILTIN_DEFAULTS` 按 name 注入 executor/execution/write_policy/model_profile
- `timeout` 从 `global_config.json → agent_runtime.default_timeout` 读取（默认 600s）

**完整模式**（含 `schema_version: 2`，旧兼容）：
- 读取 instruction(AGENT.md)、executor、execution、write_policy 等全字段
- 使用 `_load_legacy_capabilities()` 加载旧格式能力配置

### trigger.md 解析

`_trigger_registration()` 从 trigger.md 中取 `# 注册信息` 下方到下一个 `# ` 或文末的文本作为注册字符串。

### 执行器安全校验

`_validate_executor()`：
- 用户子代理：仅允许 `builtin:llm`，目录不得包含任何 `.py` 文件
- 内置子代理：格式 `file.py:function`，文件必须在同目录且存在

## 代码证据

| 关系 | 目标 | 源码符号 | 条件 | 置信度 |
|------|------|---------|------|--------|
| calls | `run/users.py` | discover_agents | 加载用户子代理时 | high |
| configured_by | agent-config.json | _load_capabilities | 读取能力配置时（v2） | high |
| configured_by | agent-config.json | _load_legacy_capabilities | 读取旧版能力配置时 | high |
| calls | `agents/_runtime/resources.py` | build_agent_prompt_bundle | 代理执行前装配 prompt | high |
| calls | trigger.md | _load_compact_manifest | 精简模式加载 trigger 注册信息 | high |
| reads | global_config.json → agent_runtime | _read_agent_timeout | 精简模式读取默认超时 | verified |

## 相关笔记

- [[agents-总览]]
- [[agents-runtime]]
- [[run-users]]
- [[config-总览]]
