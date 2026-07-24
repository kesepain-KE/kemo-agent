---
type: domain_overview
project: kemo-agent
domain: cli
module: cli-总览
layer: L1
scope: project
status: active
summary: cli.py — 命令行入口（增强型工具调用展示 + 完整计划/Cron 管理能力）
source: "cli/cli-总览.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, cli, 入口, 命令]
---
# CLI — 命令行入口与部署工具

## 入口文件

| 文件 | 说明 |
|------|------|
| `cli.py` | 命令行交互入口（薄传输层） |
| `setup.py` | 首次部署引导脚本（2026-07-22 新增）→ [[setup-wizard]] |
| `user_create.py` | 用户创建与管理模块（2026-07-22 新增）→ [[user-create]] |
| `start_web.py` | Web 服务启动脚本 |
| `update.py` + `update/` | 模块化板块更新系统（2026-07-22 重构）→ [[run-update]] |

## 关键函数

| 函数 | 说明 |
|------|------|
| `discover_user` | --user → KEMO_USER 环境变量 → 唯一用户交互选择 |
| `emit_event_stream` | 流式输出，支持 text/json 模式 |
| `main` | 主入口 |
| `run_single` / `run_interactive` | 单次/交互模式 |
| `_truncate_args` | 工具参数摘要截断（最长 120 字符），JSON 紧凑序列化 |
| `_truncate_result` | 工具结果摘要截断（最长 100 字符），优先提取 message/text/error |

## 变更（2026-07-23）

### KEMO_USER 环境变量

`discover_user` 在 `--user` 参数之后、交互选择之前检查 `KEMO_USER` 环境变量。允许通过环境变量指定默认用户，适合脚本化和 Docker 场景。

### emit_event_stream 工具调用展示增强

`tool_call_start` 事件：显示工具名称和参数摘要（使用 `_truncate_args`）。
`tool_call_result` 事件：根据状态显示图标和结果：

| 状态 | 展示 |
|------|------|
| ok=True 或 status=completed | `✓ ToolName` |
| ok=False 或 status=failed | `✗ ToolName` |
| 其他 | `✓ ToolName: 摘要` |

### 交互命令（30 个）

### 会话管理（7 个）
```
/new /sessions /use /clear /history /status /compress
```

### 记忆管理（3 个）
```
/memory /remember /forget
```

### 任务计划（7 个）
```
/plans /plan /plan-show /plan-approve /plan-pause /plan-resume /plan-cancel
```

### 定时任务（10 个）
```
/crons /cron /cron-show /cron-pause /cron-resume /cron-cancel /cron-run /cron-start /cron-stop
```

### 退出（1 个）
```
/exit
```

## 相关笔记

- [[setup-wizard]]
- [[user-create]]
- [[run-update]]
- [[run-cli]]
