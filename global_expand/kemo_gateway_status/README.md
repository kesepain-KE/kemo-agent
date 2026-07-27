# Kemo 网关运行状态拓展

本模块通过 Kemo 网关公开的只读 `GET /status` 接口采集运行状态。它默认未激活，不会在拉取或部署 kemo-agent 后自动连接任何网关。

推荐由用户明确要求主智能体调用 `expand_call` 的 `activate` 命令。也可以复制 `gateway_config.example.json` 为被 `.gitignore` 忽略的 `gateway_config.json`，填写网关根地址和独立 `STATUS_TOKEN` 后手动执行：

```bash
python data_update.py
```

网关侧必须配置独立的 `STATUS_TOKEN` 并重启网关。该 Token 不能与模型调用密钥、Web Token 或热加载模型密钥相同。

采集成功后生成：

- `input_data.md`：适合进入 Prompt 的简短状态摘要；
- `data/gateway_status.json`：严格白名单过滤后的脱敏快照；
- `artifacts/gateway_status.png`：网关调用和 Provider 状态图表。

本地 `gateway_config.json` 保存敏感 Token，已被模块自己的 `.gitignore` 排除。不要把它上传、复制到知识库或长期记忆。

