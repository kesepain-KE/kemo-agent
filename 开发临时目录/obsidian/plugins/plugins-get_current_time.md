---
type: component
project: kemo-agent
domain: plugins
module: plugins-get_current_time
layer: L3
scope: project
status: active
summary: plugins/get_current_time — 当前时间工具（新增 target_timezone 转换和 format 多格式输出）
source: "plugins/plugins-get_current_time.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, plugins, 工具, 当前时间, 时区, target_timezone]
---
# plugins/get_current_time — 当前时间工具

`E:\code\kemo-agent\plugins\get_current_time/`

## 功能

获取当前时间，支持时区转换和多格式输出。

## 工具定义

- name: `get_current_time`
- entrypoint: `tool.py:run`

## 优化详情（2026-07-21）

- **默认北京时间** — 无参数时默认返回北京时区时间
- **target_timezone** — 新增可选参数，支持将当前时间转换到指定时区
- **format 多格式输出** — 支持 ISO 8601 / 人类可读 / Unix 时间戳等多种格式
- **指令型 SKILL.md** — 从简单工具说明改为含使用示例的指令型引导

## 基本格式（无参数）

```json
{"utc": "2026-07-21T06:00:00Z", "local": "2026-07-21T14:00:00+08:00"}
```

## 相关笔记

- [[plugins-manifest]]
- [[run-tools]]
- [[agents-总览]]（time_plan 子代理交互）