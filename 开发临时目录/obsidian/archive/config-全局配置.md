---
type: component
project: kemo-agent
domain: archive
module: config-全局配置
layer: L2
scope: project
status: archived
summary: config — 全局配置
source: "archive/config-全局配置.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, config, task_plan, 计划配置]
created: 2026-07-15
---
# config — 全局配置

**状态**：✅ 第七轮：task_plan 配置段

## task_plan 配置

```json
"task_plan": {
  "auto_accept": false,
  "max_steps": 10
}
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `auto_accept` | `false` | 创建后自动批准（`true` 时跳过审批） |
| `max_steps` | 10 | 单计划最大步骤数 |

`auto_accept=true` 时创建计划后可直接执行，否则需 `/plan-approve`。

## 之前已有的配置段

- `provider` — Provider 配置
- `tools` — 工具超时/迭代
- `history` — 历史窗口
- `prompt` — System Prompt 开关
- `memory` — 档位/注入/提取
- `agent_models` — 子代理模型档位
- `agent_runtime` — 队列/超时
- `agents` — n1-n8 参数
