---
type: component
project: kemo-agent
domain: plugins
module: plugins-external_message
layer: L2
scope: project
status: active
summary: plugins/external_message — 外部消息发送插件（send_message/send_file）
source: "plugins/plugins-external_message.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, plugins, external_message, 消息, 发送, 平台]
---
# plugins/external_message — 外部消息发送插件

`E:\code\kemo-agent\plugins\external_message\`

## 概览

工具型插件，向已配置并绑定当前用户的外部消息平台主动发送文本或文件。不负责创建平台模块（创建走 SKILL.md 四步指令流程）。

## 文件

| 文件 | 说明 |
|------|------|
| `SKILL.md` | 插件描述 + Tool JSON 定义 + 创建平台四步流程说明 |
| `tool.py` | `run()` 入口函数 |

## 工具定义

```json
{
  "name": "external_message",
  "actions": ["send_message", "send_file"],
  "version": "1.0.0",
  "entrypoint": "tool.py:run"
}
```

### send_message

向指定平台、会话类型和目标 ID 发送非空文本。

### send_file

向指定平台、会话类型和目标 ID 发送本地文件。

## 安全约束

- 发送前确认平台已在 `message/out/` 配置、绑定当前用户并运行中
- 目录不得越出 `message/out/`
- 不得包含符号链接或目录联接
- `bound_user` 必须是已有用户
- 不得把凭据硬编码进模板文件

## 创建平台四步流程（指令型）

1. 判断是否需要新平台（先检查 `message/out/`）
2. 确认基本信息（目录名、显示名称、绑定用户、能力声明、工具白名单）
3. 确认适配器代码（读取 `template/message_platform/` 模板）
4. 检查冲突并创建

## 相关笔记

- [[message-总览]]
- [[message-router]]
- [[template-总览]]
