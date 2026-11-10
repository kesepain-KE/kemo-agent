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

Web“用户配置”页只允许设置网关协议、IP/主机和端口。未激活时地址暂存在
`module/panel.values.json`；已激活时新地址必须先通过 `/status` 验证，失败不会覆盖旧配置。
独立 `STATUS_TOKEN` 不在该面板回显或修改，面板状态也不会进入 `input_data.md`。

已适配网关 2.0 的增量状态字段：上游启停与凭据健康、部署路由与冷却、高级插件生命周期、
适配器和模板的运行／磁盘版本、缓存更新计划，以及范围概览的用量与费用。
专属控制台提供“上游与路由”“插件与资源”页，可选择当天、24 小时、7 天、30 天或全部历史。

`activate` 可通过 `period` 保存默认概览范围，旧配置未填写时默认为 `today`；
`refresh` 可传 `period` 临时查询，控制台切换范围也只影响本次采集，后续定时采集使用保存的默认值。
`date` 继续控制旧版单日辅助统计，不改变范围概览。范围概览按持久化的业务 Attempt 统计，
排除模型测试和能力探测，重试分别计数；成功率分母为已结束 Attempt。
费用未知显示未知，币种换算使用网关返回的固定参考汇率及日期。

脱敏快照同时保留单日统计与范围概览；PNG 使用范围概览，缺少该字段的旧响应仍使用单日统计。
资源更新计划为空表示没有缓存计划，不能据此断定资源已是最新。新增字段继续采用嵌套白名单，
不保存上游地址、请求头、凭据密文、插件配置、错误详情或系统提示词。
