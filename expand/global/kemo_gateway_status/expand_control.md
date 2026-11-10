## 注入层

# Kemo 网关运行状态拓展

这是一个默认未激活的全局只读拓展，用于连接单个 Kemo 网关的 `GET /status` 接口，读取运行阶段、版本、上游与路由、插件生命周期、适配器和模板版本、Provider/模型注册情况、调用与 Token 用量、费用以及脱敏日志，并生成 PNG 状态图表。

只有用户明确要求“激活 Kemo 网关状态拓展”时，才允许调用本模块的 `activate` 命令。激活需要网关根地址和网关专门配置的独立 `STATUS_TOKEN`；不得使用模型调用密钥、Web 登录 Token、admin Token 或 owner Token 代替。未激活时不得自行猜测地址、扫描端口或要求网关状态。

调用入口：`expand_call(scope="global", module="kemo_gateway_status", ...)`。Token 属于敏感凭据，不得在回复、记忆、知识库、任务计划或日志中复述；工具结果也不会返回 Token。

## 操作层

# 可用命令

## `initialize`

初始化本地控制台状态，保留现有网关地址、独立 Token 和采集参数。
未配置 Token 时保持未激活，不联网，也不生成含空 Token 的 `gateway_config.json`。
专属控制台的连接设置可提交独立 `STATUS_TOKEN` 调用下方 `activate`，验证成功才保存。

## `panel_configure_endpoint`

Web“用户配置”组件专用命令，只设置 `scheme`、`gateway_ip` 和 `gateway_port`。未配置独立
`STATUS_TOKEN` 时先保存待激活地址；已经激活时保留现有 Token 和采集参数，验证新地址成功后
才替换配置。验证失败保留旧配置并记录脱敏离线状态。该命令不开放任何网关管理写能力。

## `activate`

激活或重新配置拓展，并立即验证连接、生成摘要与图表。仅在用户明确授权并提供配置时调用。

参数：

- `base_url`：必填，Kemo 网关根地址，例如 `http://127.0.0.1:7531`。
- `status_token`：必填，网关 `.env` 中独立配置的 `STATUS_TOKEN`。
- `timeout_seconds`：可选，HTTP 超时，范围 `2..60`，默认 `15`。
- `ranking_limit`：可选，排行条数，范围 `1..100`，默认 `20`。
- `log_limit`：可选，最近日志条数，范围 `1..100`，默认 `20`。
- `period`：可选，默认概览范围，`today`、`24h`、`7d`、`30d`、`all`，默认 `today`。

行为：先用候选配置请求 `GET /status`；只有验证成功才原子保存本地配置并打开数据注入。失败不会覆盖已有可用配置。返回脱敏摘要和 PNG 图表产物，不返回 Token。

## `refresh`

立即重新采集状态、刷新 `input_data.md`、脱敏 JSON 快照和 PNG 图表。

参数：

- `date`：可选，格式为 `YYYY-MM-DD`，只控制原有单日辅助统计；不传时使用网关统计时区的当天。
- `period`：可选，`today`、`24h`、`7d`、`30d`、`all`，只控制 `statistics.overview`。
  不传时使用激活配置中保存的默认范围；本次查询不改变保存的默认值。

返回：脱敏状态摘要与图表产物。只读，无网关侧副作用。

范围概览按业务 Attempt 计数，排除模型测试和能力探测，重试分别计数；
成功率分母为已结束 Attempt，不能与原有单日辅助统计混用。`today` 从统计时区零点开始，
其他有限范围为滚动窗口，`all` 为已保留业务历史。费用未知不代表免费，换算费用为参考值。
插件状态可区分等待配置／授权、就绪、停用和异常；资源更新只展示已有缓存，不主动刷新索引。
没有缓存计划或磁盘清单不可读时，应报告未知或对应错误码，不能宣称已经最新。

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
