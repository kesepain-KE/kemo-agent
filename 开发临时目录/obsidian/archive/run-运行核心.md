---
type: component
project: kemo-agent
domain: archive
module: run-运行核心
layer: L2
scope: project
status: archived
summary: run — 运行核心
source: "archive/run-运行核心.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, task_plan, 计划存储, 执行器]
created: 2026-07-15
---
# run — 运行核心

**状态**：✅ 第七轮：任务计划存储/生成/执行三层

## 文件结构

```
run/
├── __init__.py            # handle_request / EngineError
├── config.py              # 配置加载 + dotenv
├── users.py               # 用户管理
├── history.py             # 对话窗口 + 会话管理
├── prompt.py              # System Prompt
├── tools.py               # 工具发现/校验/执行
├── context.py             # 上下文预算与整轮选取
├── context_summary.py     # 摘要缓存（子代理驱动）
├── agents.py              # 子代理清单发现
├── agent_runner.py        # 独立 AgentRunner
├── agent_queue.py         # 后台串行调度队列
├── agent_service.py       # 调度器注册表
├── memory.py              # 四档记忆存储引擎
├── memory_pipeline.py     # 记忆提取管线
├── task_plan_store.py     # 计划存储层（新增）
├── task_plan_service.py   # 计划生成服务（新增）
├── task_plan_executor.py  # 计划执行核心（新增）
├── engine.py              # 事件引擎
├── cli.py                 # CLI 桥接
└── 运行核心.txt
```

---

## task_plan_store.py — 存储层

### PlanStore

```python
store = PlanStore(root, user)
```

| 方法 | 说明 |
|------|------|
| `create(plan)` | 原子创建（ID 冲突拒绝） |
| `read(plan_id)` | 读取（损坏文件报错） |
| `list_plans()` | 列出全部（损坏文件跳过） |
| `update(plan_id, mutator)` | 原子读-改-写（revision 自增） |
| `delete(plan_id)` | 删除 |
| `recover_interrupted()` | 启动时恢复 interrupted 状态 |

### 校验规则

- `plan_id` 格式：`plan_<8hex>`
- `step_id` 格式：`step_<n>`
- 循环依赖检测（DFS）
- 管理工具拒绝（`task_plan_*` 前缀）
- 工具白名单校验
- per-(root, user) RLock

### 崩溃恢复

启动时扫描所有计划，将 `running` 步骤恢复为 `pending`，计划状态转为 `paused`。不自动重放有副作用的步骤。

---

## task_plan_service.py — 生成服务

### generate_plan()

```python
generate_plan(root, user, goal, ...) → plan dict
```

1. 注入相关记忆（5条/1000字符）
2. 注入可用工具清单（name + description）
3. 通过 AgentRunner 调用 task_plan 子代理（reasoning 档位）
4. 解析 JSON → 校验步骤/工具/依赖
5. normalize_plan → 返回可落盘的计划

### edit_plan()

```python
edit_plan(root, user, plan, edit_request) → plan dict
```

传入现有计划 + 修改要求，子代理返回更新后的 steps。

### 异常

| 异常 | 场景 |
|------|------|
| `PlanSkipped` | 子代理返回 action=skip |
| `PlanGenerationError` | 超时/非JSON/Markdown包裹JSON/未知工具/非法依赖 |

---

## task_plan_executor.py — 执行核心

### execute_plan()

```python
execute_plan(root, user, plan_id, ...) → Iterator[RunEvent]
```

每步：
1. 从磁盘重读最新计划
2. 按依赖选择下一步（pending + deps 全 completed）
3. 原子持久化 `running` → 执行工具 → 原子持久化结果
4. yield tool_call_start / tool_call_result / done / error
5. 关键步骤失败 → 暂停计划
6. 非关键步骤失败 → 记录后继续

### 管理函数

```python
approve_plan(root, user, plan_id)  # pending → approved
pause_plan(root, user, plan_id)    # running/approved → paused
resume_plan(root, user, plan_id)   # paused → running
cancel_plan(root, user, plan_id)   # 任意 → cancelled
get_plan(root, user, plan_id)      # 读取
list_plans(root, user)             # 列出
```
