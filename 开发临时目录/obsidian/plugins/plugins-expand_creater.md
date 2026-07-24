---
type: component
project: kemo-agent
domain: plugins
module: plugins-expand_creater
layer: L2
scope: project
status: active
summary: plugins/expand_creater/ — 拓展模块热创建工具（list/create/validate，四步创建流程）
source: "plugins/expand_creater/"
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, expand_creater, 拓展创建, expand]
---
# plugins/expand_creater/ — 拓展模块热创建工具

`E:\code\kemo-agent\plugins\expand_creater\`

## 定位

热创建、列出并校验当前用户拓展或共享拓展。工具会生成标准 `expand.json`、操控文档、操控入口、数据更新入口和初始数据文件；创建后立即使用真实 Expand 运行时契约复验。

## 结构

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 工具注册描述（含四步创建流程指令） |
| `tool.py` | 工具入口，执行 list/create/validate |

## 工具定义

- name: `expand_creater`
- entrypoint: `tool.py:run`
- scope: `user` / `shared`（不提供 `global`）

### action 说明

| action | 功能 |
|--------|------|
| `list` | 列出一个 scope 的模块、功能说明、输入健康状态和结构校验结果 |
| `create` | 创建新拓展模块（原子写入 5 文件后立即校验，失败回滚） |
| `validate` | 校验指定模块的清单字段、引用文件、Python 语法、操控文档结构、路径边界 |

## 四步创建流程（必须遵守）

SKILL.md 强制要求创建前依次完成四步：

1. **批判性判断** — 判断是否真需要拓展模块（而非子代理/技能/直接脚本）
2. **确认 scope 和基本信息** — user/shared 层、英文目录名、一句话说明
3. **确认注入层和操作层** — expand_control.md 的注入层/操作层内容
4. **检查冲突与创建** — action=list 确认无同名、汇总后用户确认、action=create

## 创建时生成的 5 个文件

| 文件 | 说明 |
|------|------|
| `expand.json` | 模块清单（name/explain/open_input/input_data/input_health/start_update/open_control/start_expand/start_control） |
| `expand_control.md` | 操控手册，含注入层和操作层两段 |
| `start_expand.py` | 操控入口（默认返回 not_implemented） |
| `data_update.py` | 数据采集入口 |
| `input_data.md` | 初始数据文件 |

## 安全约束

- create 不覆盖同名目录，目录级临时写入完成后才发布；创建后校验失败会回滚整个新模块
- 模块目录和清单引用文件不得是符号链接或目录联接，也不得跳出目标 scope
- 不得写入疑似 API Key、Token、密码或其他敏感凭据
- 默认 start_expand.py 只返回 not_implemented，不伪造执行成功

## 与 sense_creater 的区别

| | expand_creater | sense_creater |
|---|---|---|
| 目标目录 | `shared_expand/` 或 `users/<user>/expand/` | `global_sense/` |
| 数据流 | 数据注入与外部操控 | 单向采集并注入 |
| 生成文件 | 5 个 | 3 个 |
| scope | user/shared | 只有全局层 |

## 相关笔记

- [[plugins-manifest]]
- [[plugins-sense_creater]]
- [[global_expand-全局拓展]]
- [[run-prompt_sources]]（read_expand_meta）