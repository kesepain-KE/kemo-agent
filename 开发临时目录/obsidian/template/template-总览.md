---
type: component
project: kemo-agent
domain: template
module: template-总览
layer: L1
scope: project
status: active
summary: template/ — 模块模板系统（agent/expand/message/message_platform/sense/skills/task_cron/task_plan/user）
source: "template/template-总览.md"
updated: 2026-07-26
verified: true
tags: [kemo-agent, template, 模板, 骨架, 新建模块]
---
# template/ — 模块模板系统

`E:\code\kemo-agent\template\`

## 概览

提供各类模块的标准化模板，供 `user_create.py`、插件创建工具和手动新建时使用。

## 目录结构

| 子目录 | 用途 |
|--------|------|
| `agent/` | 子代理模板（agent.json + AGENT.md + trigger.md + executor.py） |
| `expand/` | 拓展模块模板（expand.json + data_update.py + input_data.md + expand_control.md） |
| `message/` | 文件夹消息插件模板（message.json + input/output/detect.py） |
| `message_platform/` | **已删除（2026-07-23）**。旧版外部消息平台模板（message.json + input/output/detect.py）已移除，不再需要手动创建。Telegram input 模块新增 `filters.COMMAND` handler 确保 slash 指令到达路由层。 |
| `sense/` | 感知模块模板（sense.json + data_update.py + sense.md） |
| `skills/` | 用户技能模板（SKILL.md） |
| `task_cron/` | 定时任务模板（cron_template.json） |
| `task_plan/` | 任务计划模板（plan_template.json） |
| `user/` | 用户目录骨架（完整用户目录结构 + storage.json + data_structure.md） |

## user/ 骨架结构

```
user/
├── agents/
├── avatar/
├── download/
├── expand/
├── file_upload/
├── history/
├── improve/
│   ├── half_year/
│   ├── one_month/
│   ├── permanent/
│   ├── seven_days/
│   └── storage.json
├── knowledge/
│   └── data_structure.md
├── task_cron/
├── task_plan/
├── user_skills/
│   ├── agent_create/
│   └── user_create/
├── user_config.json
├── user_soul.md
└── 模板说明.md
```

## 模板更新（2026-07-26）

### expand/ 模板重写

- `data_update.py`：`main()` → `update()`，新增原子写入（`atomic_write`）、健康状态写回（`write_manifest_health`）、报错时标记 `input_health=异常`
- `expand.json`：初始 `input_health` 从 `正常` 改为 `异常`，移除 `recent_update` 默认值
- `expand_control.md`：移除 `{{变量}}` 占位符，改为纯职责说明
- `input_data.md`：提示改为等待 RuntimeHost 自动刷新
- `模板说明.md`：全面重写，详细说明自动运行机制、子进程隔离、安全规则

### sense/ 模板重写

- `data_update.py`：`main()` → `update()` 优先，新增 `atomic_write`、`render_markdown`、`write_manifest_health`
- `sense.json`：初始 `health` 从 `正常` 改为 `异常`，`recent_update` 设为 `2000-01-01 00:00:00`
- `sense.md`：提示改为等待 RuntimeHost 自动刷新
- `模板说明.md`：全面重写，说明感知只读性质、子进程运行机制、文件合同

### user/ 模板

- `user_config.json`：新增 `reasoning_effort: "medium"`
- `user_soul.md`：新增独立性原则（不盲从、不迎合）

## 相关笔记

- [[cli-总览]]
- [[run-update]]
- [[plugins-expand_creater]]
- [[plugins-sense_creater]]
- [[plugins-skill_creater]]
