---
type: component
project: kemo-agent
domain: archive
module: task_plan-任务计划核心
layer: L2
scope: project
status: archived
summary: task_plan — 任务计划执行核心
source: "archive/task_plan-任务计划核心.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, task_plan, 任务计划, 状态机, 生命周期]
created: 2026-07-17
---
# task_plan — 任务计划执行核心

**状态**：✅ 第七轮：计划生成/持久化/审批/执行/暂停/恢复/崩溃恢复闭环

## 存储

```
users/<user>/task_plan/<plan_id>.json
```

原子写入（tmp + os.replace），per-user RLock。

## 计划数据契约

```json
{
  "schema_version": 1,
  "plan_id": "plan_a1b2c3d4",
  "title": "任务标题",
  "description": "用户原始目标",
  "user": "kesepain",
  "source": "cli",
  "session_id": "default",
  "status": "pending",
  "auto_accept": false,
  "revision": 1,
  "current_step": "step_1",
  "steps": [{...}]
}
```

每步：`step_id`、`title`、`description`、`status`、`depends_on`、`tool_name`、`tool_arguments`、`critical`、`result`、`error`、`started_at`、`finished_at`

## 状态机

### 计划状态（7 种）

```
pending → approved → running → completed
                    ↓         ↑
                  paused →───┘
                    ↓
                  failed
任意状态 → cancelled
```

### 步骤状态（6 种）

```
pending → running → completed / failed / skipped / cancelled
```

### 关键规则

- 关键步骤失败 → 计划暂停
- 非关键步骤失败 → 记录后继续
- 进程重启 → `running` 步骤恢复为 `pending`，计划 → `paused`

## 三层架构

### run/task_plan_store.py — 存储层

| 方法 | 说明 |
|------|------|
| `create(plan)` | 原子创建（ID 冲突拒绝） |
| `read(plan_id)` | 读取（损坏文件报错） |
| `list_plans()` | 列出全部（损坏文件跳过） |
| `update(plan_id, mutator)` | 原子读-改-写（revision 自增） |
| `delete(plan_id)` | 删除 |
| `recover_interrupted()` | 启动时恢复 interrupted 状态 |

校验：ID 格式 / 循环依赖 / 管理工具拒绝 / 工具白名单

### run/task_plan_service.py — 生成服务

```python
generate_plan(root, user, goal, ...)       → plan dict
edit_plan(root, user, plan, edit_request)  → plan dict
```

- 通过 AgentRunner 调用 task_plan 子代理
- 注入相关记忆（5条/1000字符）和可用工具清单
- 三模式：`create` / `edit` / `skip`
- 异常：`PlanSkipped`（不需要计划）/ `PlanGenerationError`（超时/非JSON/未知工具/非法依赖）

### run/task_plan_executor.py — 执行核心

```python
execute_plan(root, user, plan_id, ...)  → Iterator[RunEvent]
approve_plan / pause_plan / resume_plan / cancel_plan
get_plan / list_plans
```

执行流程：
1. 从磁盘重读最新计划
2. 按依赖选择下一步（pending + 所有 deps completed）
3. 持久化 `running` → 执行 → 持久化结果
4. 关键失败暂停 / 非关键继续
5. 已完成步骤不重放
6. 每步 yield RunEvent（含 plan_id/step_id）

## 配置

```json
"task_plan": {
  "auto_accept": false,
  "max_steps": 10
}
```

## CLI 命令（7 个）

| 命令 | 说明 |
|------|------|
| `/plans` | 列出所有计划 |
| `/plan <目标>` | 生成计划并落盘 |
| `/plan-show <ID>` | 查看步骤详情 |
| `/plan-approve <ID>` | 批准并立即执行 |
| `/plan-pause <ID>` | 暂停运行中的计划 |
| `/plan-resume <ID>` | 恢复暂停的计划 |
| `/plan-cancel <ID>` | 取消计划 |

## 子代理升级

`task_plan` agent.json v2.0.0：严格 input/output Schema，三模式支持，不直接执行工具或写文件。
