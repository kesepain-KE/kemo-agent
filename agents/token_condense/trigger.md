# 注册信息

- **名称**: token_condense
- **触发**: Token 预算超限时由引擎自动唤起
- **职责**: 按完整轮次生成结构化压缩摘要，不得切断一轮完整对话
- **模型**: cheap
- **工具**: 无（纯 LLM 推理，单轮调用）

# 操作信息

## 调用方式

由引擎 `run/engine.py` 在 Token 上限触发时自动调用。`allowed_callers: ["engine"]`，主智能体不可直接调用。

## 输入

与 context_manage 相同：`previous_summary`、`rounds`、`trigger`（此处为 `token_limit`）

## 输出

与 context_manage 相同的 7 字段结构化摘要

## 注意事项

- 不读取主会话、不访问知识库、不调用工具
- 只处理调用方显式传入的旧对话文本和工具调用数据
- 不得切断一轮完整对话
- 保留事实、要求、决策、未完成事项、工具结果、实体和简短叙述
