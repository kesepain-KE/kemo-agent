## 注入层

# Kemo 网关运行状态拓展

这是一个默认未激活的全局只读拓展，用于连接单个 Kemo 网关的 `GET /status` 接口，读取运行阶段、版本、Provider/模型注册情况、当日调用与 Token 统计以及脱敏调用日志，并生成 PNG 状态图表。

只有用户明确要求“激活 Kemo 网关状态拓展”时，才允许调用本模块的 `activate` 命令。激活需要网关根地址和网关专门配置的独立 `STATUS_TOKEN`；不得使用模型调用密钥、Web 登录 Token、admin Token 或 owner Token 代替。未激活时不得自行猜测地址、扫描端口或要求网关状态。

调用入口：`expand_call(scope="global", module="kemo_gateway_status", ...)`。Token 属于敏感凭据，不得在回复、记忆、知识库、任务计划或日志中复述；工具结果也不会返回 Token。

## 操作层

# 可用命令

## `activate`

激活或重新配置拓展，并立即验证连接、生成摘要与图表。仅在用户明确授权并提供配置时调用。

参数：

- `base_url`：必填，Kemo 网关根地址，例如 `http://127.0.0.1:7531`。
- `status_token`：必填，网关 `.env` 中独立配置的 `STATUS_TOKEN`。
- `timeout_seconds`：可选，HTTP 超时，范围 `2..60`，默认 `15`。
- `ranking_limit`：可选，排行条数，范围 `1..100`，默认 `20`。
- `log_limit`：可选，最近日志条数，范围 `1..100`，默认 `20`。

行为：先用候选配置请求 `GET /status`；只有验证成功才原子保存本地配置并打开数据注入。失败不会覆盖已有可用配置。返回脱敏摘要和 PNG 图表产物，不返回 Token。

## `refresh`

立即重新采集状态、刷新 `input_data.md`、脱敏 JSON 快照和 PNG 图表。

参数：可选 `date`，格式为 `YYYY-MM-DD`，用于查看指定统计日；不传时使用网关统计时区的当天。

返回：脱敏状态摘要与图表产物。只读，无网关侧副作用。

## `configuration_status`

查看拓展是否已经激活、已配置的网关地址和采集参数。不会发起网络请求，也不会返回 Token。

## `deactivate`

停用拓展，删除本地 `gateway_config.json`、状态快照与图表，并关闭 Prompt 数据注入。

参数：无。只影响 kemo-agent 本地拓展状态，不修改或重启网关。

# 失败形式与权限

- `401`：STATUS_TOKEN 缺失或无效。
- `503`：网关没有启用独立状态接口，或 STATUS_TOKEN 与其他网关 Token 重复。
- 连接、TLS、超时、JSON 格式错误均返回明确的脱敏错误，不包含 Token 或响应原文。
- 本拓展只调用 `GET /status`，不调用任何管理写接口，不具备启停 Provider、修改密钥或重启网关的权限。

