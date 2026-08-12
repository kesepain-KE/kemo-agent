# task_plan

管理当前用户任务计划的运行时状态：查看、列出、标记步骤完成或失败，以及批准、暂停、恢复和中止计划。计划文件由 `PlanStore` 管理，位于 `users/<user>/task_plan/`。

## 使用原则

### 硬性规则

- 主智能体在“单轮连续执行”模式下执行完一个计划步骤后，必须立即调用 `step_done` 并写入结果摘要；根据返回的 `next_step` 继续执行，直到计划完成或暂停。
- 步骤执行失败时必须调用 `step_fail`；失败步骤会被记录，计划自动暂停。
- 当控制提示明确声明步骤状态由框架执行器维护（executor-managed）时，不得调用 `step_done` 或 `step_fail`，避免重复写入。
- `create` 和 `edit` 不属于本工具；创建和修改计划必须走 `task_plan` 子代理。
- `task_plan` 是管理工具，不能被写成计划中的执行步骤。

### 必须调用的场景

| 场景 | action |
|---|---|
| 查看当前计划或某个计划的完整信息 | `view` |
| 列出当前对话空间的计划 | `list` |
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
| `action` | 始终必需 | `view` / `list` / `step_done` / `step_fail` / `abort` / `approve` / `pause` / `resume` |
| `plan_id` | 除 `list` 外通常必需 | `view`、`abort` 省略时自动选择第一个非终态计划 |
| `step_id` | `step_done`、`step_fail` | 步骤 ID，例如 `step_1` |
| `result` | 可选 | `step_done` 的执行结果摘要，建议不超过 200 字 |
| `error` | `step_fail` | 错误描述 |

## 返回字段

- 所有动作返回 `ok`。
- `view` 和状态变更动作成功时返回完整 `plan`。
- `list` 返回 `plans` 与 `total`。
- 失败时返回 `error`，不会把普通计划状态错误抛给主智能体。

## Tool

```json
{
  "name": "task_plan",
  "description": "管理任务计划运行状态：查看或列出计划、标记步骤完成或失败，以及批准、暂停、恢复和中止。主智能体执行计划步骤后必须调用 step_done 或 step_fail。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["view", "list", "step_done", "step_fail", "abort", "approve", "pause", "resume"],
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
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
