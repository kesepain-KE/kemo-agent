---
type: component
project: kemo-agent
domain: run
module: run-config
layer: L2
scope: project
status: active
summary: run/config.py — 配置加载（provider.type 验证 chat/kemo）
source: "run/run-config.md"
updated: 2026-07-21
verified: true
tags: [kemo-agent, run, 配置, dotenv, provider, chat, kemo]
---
# run/config.py — 配置加载

`E:\code\kemo-agent\run\config.py`

## 概览

加载 `config/config_core.json` + `users/<user>/config.json`（深合并），注入环境变量密钥。

## 类

### ConfigError

`ConfigError(RuntimeError)`

## 函数

### load_dotenv / project_root / read_json_object / deep_merge / load_config

与旧版一致。

### provider_runtime_config

```python
def provider_runtime_config(config: dict) -> dict
```

从 config 提取 Provider 运行时配置，注入环境变量密钥。

关键校验：
- `provider.type` 必须是 `"chat"` 或 `"kemo"`（不再是 `"openai"`）
- kemo 模式默认 base_url：`http://127.0.0.1:8741`，不补 `/v1`
- chat 模式默认 base_url：`https://api.openai.com/v1`，自动补全 `/v1`

## 变更记录

| 旧版 | 新版 |
|------|------|
| provider.type 允许 "openai" 或 "kemo" | 允许 "chat" 或 "kemo" |
| kemo 模式 base_url 默认 `127.0.0.1:8741/v1` | 默认 `127.0.0.1:8741`，不补 `/v1` |
| 统一补全 `/v1` | 只有 chat 模式补 `/v1`，kemo 模式保持协议根地址 |
| api_key_env kemo 默认 `KEMO_API_KEY`，openai 默认 `OPENAI_API_KEY` | api_key_env kemo 默认 `KEMO_API_KEY`，chat 默认 `OPENAI_API_KEY` |
