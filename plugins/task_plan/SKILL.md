# task_plan

管理当前用户任务计划的运行时状态：查看、列出、修正计划、重试或重置失败步骤、标记步骤完成或失败，以及批准、暂停、恢复和中止计划。计划文件由 `PlanStore` 管理，位于 `users/<user>/task_plan/`。

## 使用原则

### 硬性规则

- 主智能体在“单轮连续执行”模式下执行完一个计划步骤后，必须立即调用 `step_done` 并写入结果摘要；根据返回的 `next_step` 继续执行，直到计划完成或暂停。
- 步骤执行失败时必须调用 `step_fail`；失败步骤会被记录，计划自动暂停。
- 当控制提示明确声明步骤状态由框架执行器维护（executor-managed）时，不得调用 `step_done` 或 `step_fail`，避免重复写入。
- `create` 不属于本工具；复杂的自然语言重编排仍优先走 `task_plan` 子代理。`edit` 只用于带 revision 的确定性字段修正。
- `edit`、`retry_step`、`reset_step` 必须携带最近一次读取到的 `revision`，版本变化时重新 `view` 后再操作。
- 已完成步骤不得编辑、重置或删除。`retry_step` 会把 failed/cancelled 步骤恢复为 pending；只有计划自身 `auto_accept=true` 或用户配置 `task_plan.auto_retry_on_fix=true` 时，paused/failed 计划才自动恢复为 approved。`reset_step` 只重置步骤，永远不改变计划状态。
- `task_plan` 是管理工具，不能被写成计划中的执行步骤。

### 必须调用的场景

| 场景 | action |
|---|---|
| 查看当前计划或某个计划的完整信息 | `view` |
| 列出当前对话空间的计划 | `list` |
| 修正计划标题、描述或未完成步骤的执行字段 | `edit` |
| 修正失败原因后重试 failed/cancelled 步骤 | `retry_step` |
| 只重置 failed/cancelled 步骤但不激活计划 | `reset_step` |
| 刚执行完一个步骤 | `step_done` |
| 步骤执行失败 | `step_fail` |
| 用户批准计划 | `approve` |
| 用户暂停计划 | `pause` |
| 用户继续计划 | `resume` |
| 用户取消计划 | `abort` |

## 状态说明

计划状态包括 `pending`、`approved`、`running`、`paused`、`completed`、`failed`、`cancelled`。步骤状态包括 `pending`、`running`、`completed`、`failed`、`skipped`、`cancelled`。

`resume` 将暂停计划恢复为 `approved`，再由后台调度器、CLI 或 Web/App 起跑入口中的唯一执行器原子领取为 `running`。`abort` 复用核心执行器的取消状态机：计划变为 `cancelled`，尚未执行的步骤也变为 `cancelled`。

## 参数说明

| 参数 | 必需条件 | 说明 |
|---|---|---|
| `action` | 始终必需 | `view` / `list` / `edit` / `retry_step` / `reset_step` / `step_done` / `step_fail` / `abort` / `approve` / `pause` / `resume` |
| `plan_id` | 除 `list` 外通常必需 | `view`、`abort` 省略时自动选择第一个非终态计划 |
| `step_id` | `retry_step`、`reset_step`、`step_done`、`step_fail` | 步骤 ID，例如 `step_1` |
| `revision` | `edit`、`retry_step`、`reset_step` | 最近一次 `view`/`list` 返回的计划 revision |
| `title` / `description` | `edit` 可选 | 新计划标题或描述，至少与 steps 中的一项同时提供 |
| `steps` | `edit` 可选 | 步骤补丁数组；每项含 step_id，可修改 tool_name/tool_arguments/depends_on/critical |
| `result` | 可选 | `step_done` 的执行结果摘要，建议不超过 200 字 |
| `error` | `step_fail` | 错误描述 |

## 返回字段

- 所有动作返回 `ok`。
- `view` 和状态变更动作成功时返回完整 `plan`。
- `list` 返回 `plans` 与 `total`。
- `edit`、`retry_step`、`reset_step` 返回 `activated` 与 `reason`；`activated=true` 只表示计划已恢复为 approved，仍由执行器通过 approved→running 的原子领取开始执行。
- 失败时返回 `error`，不会把普通计划状态错误抛给主智能体。

## Tool

```json
{
  "name": "task_plan",
  "description": "管理任务计划运行状态：查看或列出计划、修正计划、重试或重置失败步骤、标记步骤完成或失败，以及批准、暂停、恢复和中止。主智能体执行计划步骤后必须调用 step_done 或 step_fail。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["view", "list", "edit", "retry_step", "reset_step", "step_done", "step_fail", "abort", "approve", "pause", "resume"],
        "description": "任务计划运行态操作"
      },
      "plan_id": {
        "type": "string",
        "description": "计划 ID；view 和 abort 可省略并自动选择非终态计划"
      },
      "step_id": {
        "type": "string",
        "description": "步骤 ID；step_done 和 step_fail 必需"
      },
      "revision": {
        "type": "integer",
        "minimum": 1,
        "description": "edit/retry_step/reset_step 的乐观并发版本"
      },
      "title": {
        "type": "string",
        "minLength": 1,
        "description": "edit 可选的新计划标题"
      },
      "description": {
        "type": "string",
        "minLength": 1,
        "description": "edit 可选的新计划描述"
      },
      "steps": {
        "type": "array",
        "description": "edit 的步骤补丁；已完成步骤禁止修改",
        "items": {
          "type": "object",
          "properties": {
            "step_id": {"type": "string"},
            "tool_name": {"type": ["string", "null"]},
            "tool_arguments": {"type": "object"},
            "depends_on": {"type": "array", "items": {"type": "string"}},
            "critical": {"type": "boolean"}
          },
          "required": ["step_id"],
          "additionalProperties": false
        }
      },
      "result": {
        "type": "string",
        "description": "step_done 的执行结果摘要"
      },
      "error": {
        "type": "string",
        "description": "step_fail 的错误描述"
      }
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.2.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
