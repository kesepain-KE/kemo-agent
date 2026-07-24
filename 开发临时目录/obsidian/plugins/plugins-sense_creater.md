---
type: component
project: kemo-agent
domain: plugins
module: plugins-sense_creater
layer: L2
scope: project
status: active
summary: plugins/sense_creater/ — 感知模块热创建工具（list/create/validate，四步创建流程）
source: "plugins/sense_creater/"
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, sense_creater, 感知创建, sense]
---
# plugins/sense_creater/ — 感知模块热创建工具

`E:\code\kemo-agent\plugins\sense_creater\`

## 定位

热创建、列出并校验全局感知模块。感知模块只采集系统或环境数据，并通过 `sense.md` 单向注入 system prompt，不提供外部操控接口。

## 结构

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 工具注册描述（含四步创建流程指令） |
| `tool.py` | 工具入口，执行 list/create/validate |

## 工具定义

- name: `sense_creater`
- entrypoint: `tool.py:run`
- scope: 只有全局层（`global_sense/`），无需参数

### action 说明

| action | 功能 |
|--------|------|
| `list` | 列出 `global_sense/` 下所有模块 |
| `create` | 创建新感知模块（原子写入 3 文件后立即校验，失败回滚） |
| `validate` | 校验指定模块的清单字段、引用文件、Python 语法 |

## 四步创建流程（必须遵守）

1. **判断是否真的需要感知模块** — 采集→注入单向数据流；需操控时用 expand_creater
2. **确认基本信息** — 英文目录名、一句话功能说明、采集数据
3. **确认数据内容** — 结构化 Markdown 数据模板（sense.md）
4. **检查冲突与创建** — action=list 确认无同名、汇总后用户确认、action=create

## 创建时生成的 3 个文件

| 文件 | 说明 |
|------|------|
| `sense.json` | 五字段清单（name/data_md/recent_update/health/start_update） |
| `sense.md` | 初始感知数据 Markdown |
| `data_update.py` | 数据采集入口（可选自定义或安全骨架） |

## 安全约束

- 创建采用临时目录发布，发布后立即校验；失败会删除整个新模块
- 模块目录及清单引用文件不得是符号链接或目录联接，也不得跳出 `global_sense/`
- sense.json 严格包含五个字段：name/data_md/recent_update/health/start_update
- 自定义 Python 在写入前检查语法和疑似硬编码凭据
- sense.md 全文可能进入 system prompt，只应保存允许所有启用用户看到的数据

## 相关笔记

- [[plugins-manifest]]
- [[plugins-expand_creater]]
- [[global_sense-全局感知]]
- [[run-prompt_sources]]（SenseMeta）