---
type: component
project: kemo-agent
domain: run
module: run-users
layer: L2
scope: project
status: active
summary: run/users.py — 用户目录工具
source: "run/run-users.md"
updated: 2026-07-18
verified: partial
tags: [kemo-agent, run, 用户管理]
---
# run/users.py — 用户目录工具

## 职责

- 验证用户名
- 发现用户目录
- 创建用户目录副本
- 写入空 JSON 默认值

## 代码证据

| 关系 | 目标 | 源码路径 | 源码符号 | 条件 | 不发生条件 | 置信度 | 核验日期 |
|---|---|---|---|---|---|---|---|
| reads | [[users-多用户系统]] | run/users.py | validate_user_name / user_dir / list_users | 读取或准备用户目录时 | 用户目录不存在 | high | 2026-07-18 |
| called_by | [[web-service]] | run/users.py | list_users | Web 查询用户列表时 | 非用户接口 | high | 2026-07-18 |
| called_by | [[agents-registry]] | run/users.py | user_dir / list_users | 加载用户子代理时 | 未指定 user | high | 2026-07-18 |

## 相关笔记

- [[run-总览]]
- [[users-多用户系统]]
