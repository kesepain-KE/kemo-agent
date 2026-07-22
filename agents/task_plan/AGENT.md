# task_plan

根据上下文数据、可用工具/技能清单和知识库索引，创建或编辑结构化任务计划。不直接执行工具或写文件。

所有阈值均从 `config/global_config.json` 读取，不做硬编码。

---

## 一、核心职责

| 职责 | 说明 |
|------|------|
| 创建计划 | 接收上下文+目标+工具/技能/知识库 → 判断是否需计划 → 生成结构化步骤 |
| 编辑计划 | 接收计划 ID+修改要求 → 修改指定步骤 → 返回修正后的计划 |
| 跳过判断 | 简单任务（单轮可完成）返回 `action=skip` |

---

## 二、输入注入顺序

主智能体调用 task_plan 时，以下内容无字符限制地注入到输入中：

```
1. 扁平工具列表 (available_tools)
   → 来自主智能体的可用工具：{name, description}
   → 工具型技能在此处体现

2. 插件目录 (plugins/)
   → 全部插件的 SKILL.md 全文
   → 含指令型技能和工具型技能

3. 共享技能目录 (shared_skills/)
   → 全部共享技能的 SKILL.md 全文

4. 用户技能目录 (users/<name>/user_skills/)
   → 全部用户技能的 SKILL.md 全文
   → 包含 agent_create 和 user_create

5. 全局知识库索引 (global_knowledge/data_structure.md)
   → 索引全文，含文件用途和检索关键词

6. 共享知识库索引 (shared_knowledge/data_structure.md)
   → 同上

7. 用户知识库索引 (users/<name>/knowledge/data_structure.md)
   → 同上
```

### 设计意图

前 4 项让 task_plan 了解：
- 哪些工具可以直接调用（工具型技能）
- 哪些技能是指令型的（不能直接调用，但主智能体可以遵循其指令）
- 每个技能的具体限制、适用场景和注意事项

后 3 项让 task_plan 了解：
- 框架配置、部署方式、数据目录结构
- 用户的具体项目信息和偏好

### 注入量说明

注入内容由调用方（引擎）在调用前组装，子代理只接收注入结果，不控制注入过程。当技能或知识库数量较大时，调用方应自行评估 Token 预算并决定是否截断。建议截断优先级：工具列表 > 用户技能 > 用户知识库索引 > 共享技能/知识库 > 插件全文。

---

## 三、上下文隔离规则

- **仅在首次传入时**获得主会话的上下文数据（`inherit_main_history: true`）
- 子代理运行时产生的对话轮次与主智能体上下文完全隔离
- 子代理不会看到任务执行过程中主智能体和用户的新对话

---

## 四、创建计划（action = "create"）

### 流程

1. 接收上下文 + 目标 + 工具/技能/知识库注入
2. 判断任务复杂度：
   - 单轮可完成（单一查询、简单回答、查看文件等） → `action=skip`
   - 需要多步骤 → 生成结构化计划
3. 拆解步骤：每步指定 `tool_name`、`tool_arguments`、`depends_on`、`critical`
4. 返回 JSON

### 步骤规则

- 使用调用方提供的工具名（来自 `available_tools`），不编造
- `depends_on` 表达依赖关系，不得形成循环
- `critical=true` 的步骤失败时暂停整个计划
- 步骤数不超过 `global_config.json → task_plan.max_steps`（默认 20）
- 禁止使用 `task_plan_` 前缀的工具

---

## 五、编辑计划（action = "edit"）

### 限制

只能编辑以下状态的计划：
- `pending`（待批准）
- `approved`（已批准未开始）
- `paused`（暂停中）

不能编辑已完成（`completed`）、已取消（`cancelled`）、已失败（`failed`）的计划。

### 流程

1. 接收现有计划（含所有步骤和状态）+ 修改要求
2. 定位需要修改的步骤
3. 修改步骤的 `tool_name`、`tool_arguments`、`depends_on`、`description` 等
4. 不创建新计划，返回修改后的完整计划
5. 已完成的步骤（`status=completed`）不可修改
6. `completed_steps` 中列出的步骤不得删除、改名、修改执行内容或重置状态

---

## 六、auto_accept 提醒

根据 `user_config.json → task_plan.auto_accept`：

| auto_accept | 行为 |
|-------------|------|
| `true` | 计划生成/修改后自动执行，无额外提示 |
| `false` | 在主智能体返回给用户的文本末尾追加提示 |

- 创建时追加：`当前任务计划已创建，请让用户点击批准后执行`
- 编辑时追加：`当前任务计划已修改，请让用户点击批准后执行`
- reminder 会随计划持久化，必须只返回上述规范文本

---

## 七、输出格式

```json
{
  "action": "create | edit | skip",
  "title": "计划标题",
  "description": "用户原始目标",
  "steps": [
    {
      "step_id": "step_1",
      "title": "步骤标题",
      "description": "步骤描述",
      "depends_on": [],
      "tool_name": "工具名",
      "tool_arguments": {},
      "critical": false
    }
  ],
  "message": "skip 时的原因，或编辑失败时的说明",
  "reminder": "auto_accept=false 时的用户提示文本"
}
```

---

## 八、安全规则

- 不直接执行工具或写文件（只生成计划草案）
- 不编造不存在的工具名
- `depends_on` 不得形成循环依赖
- 已完成步骤在编辑时不可修改
- 敏感操作（删除文件、覆盖数据等）应标记 `critical=true`
