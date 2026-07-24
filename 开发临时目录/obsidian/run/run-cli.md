---
type: component
project: kemo-agent
domain: run
module: run-cli
layer: L2
scope: project
status: active
summary: run/cli.py — CLI 运行桥（薄桥接层）
source: "run/run-cli.md"
updated: 2026-07-19
verified: true
tags: [kemo-agent, run, CLI, 桥接]
---
# run/cli.py — CLI 运行桥

`E:\code\kemo-agent\run\cli.py`

## 概览

薄桥接层。`cli.py`（入口）→ `run/cli.py`（桥接）→ `run/engine.py`（引擎）。只做字段校验，不做业务逻辑。

## 函数

### handle_cli_request(request: dict) -> dict

校验请求包含 `user`, `prompt`, `source`, `session_id` 后，调用 `engine.handle_request`。

### stream_cli_request(request: dict)

校验请求包含 `user`, `prompt`, `source`, `session_id` 后，调用 `engine.iter_request_events` 返回流式事件生成器。

## 说明

`run/cli.py` 仅 31 行，不包含 status/compress 桥接（那些在根 `cli.py` 中直接实现）。属于极薄桥接层，主要用途是让 cron 和消息适配器可以调用同一运行引擎。

## 相关笔记

- [[cli-总览]]
- [[run-engine]]
- [[run-总览]]
