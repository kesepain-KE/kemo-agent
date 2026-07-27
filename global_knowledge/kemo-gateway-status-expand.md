# Kemo 网关运行状态拓展

`global_expand/kemo_gateway_status/` 是框架内置、默认未激活的全局拓展。它面向“一个 kemo-agent 连接一个 Kemo 网关”的常见部署方式，通过网关公开的只读状态接口采集运行信息，并生成适合主智能体使用的摘要、脱敏快照和 PNG 图表。

## 权限与接口边界

拓展只调用：

```http
GET /status
Authorization: Bearer <STATUS_TOKEN>
```

网关侧必须在 `.env` 配置独立的 `STATUS_TOKEN` 并重启。该 Token 不能与模型调用密钥、Web 登录 Token、admin Token 或 owner Token 共用。

拓展不会调用管理后台的重启、Provider 配置、密钥列表等接口，也不能修改网关状态。状态请求禁止自动跟随 HTTP 重定向，避免把 Token 带到配置地址之外的主机；HTTPS 使用系统证书校验。

## 默认状态与激活

源码和首次部署状态为 `open_input=false`，且不存在 `gateway_config.json`。此时定时入口只报告未激活，不连接网关，也不会把网关数据注入 Prompt。只有用户明确要求激活并提供网关根地址与独立状态 Token 时，主智能体才应调用：

```text
expand_call(
  scope="global",
  module="kemo_gateway_status",
  command="activate",
  params={
    "base_url": "http://127.0.0.1:7531",
    "status_token": "<独立 STATUS_TOKEN>"
  }
)
```

可选参数：

| 参数 | 默认值 | 范围 | 说明 |
|------|--------|------|------|
| `timeout_seconds` | `15` | `2..60` | 状态请求超时 |
| `ranking_limit` | `20` | `1..100` | Provider、模型和网关 Key ID 排行条数 |
| `log_limit` | `20` | `1..100` | 最近成功/失败日志条数 |

`activate` 会先使用候选配置请求 `/status`。只有接口、鉴权和响应合同全部验证成功后，才原子保存本地配置并打开 Prompt 注入；失败不会覆盖原有可用配置。

## 命令

| 命令 | 行为 |
|------|------|
| `activate` | 验证并保存配置，立即生成摘要、快照与图表 |
| `refresh` | 使用现有配置立即刷新；可传 `date=YYYY-MM-DD` |
| `configuration_status` | 只查看激活状态、网关地址和采集参数，不联网、不返回 Token |
| `deactivate` | 删除本地凭据、快照和图表，关闭 Prompt 注入，不修改网关 |

## 本地产物

激活后模块维护：

- `input_data.md`：进入 Prompt 的简短运行摘要；
- `data/gateway_status.json`：严格字段白名单过滤后的脱敏快照；
- `artifacts/gateway_status.png`：`1600×900` 状态图表；
- `gateway_config.json`：本机敏感配置，不进入 Git；
- `_last_run.json`、`_runtime.json` 和锁文件：本地运行诊断，不进入 Git。

持久化白名单明确排除最高优先级系统提示词、状态 Token、模型调用密钥、Provider 密钥、请求正文、原始错误正文和未知未来字段。PNG 与 Markdown 只使用脱敏聚合数据。

## 可见状态

拓展可提供：

- 网关运行阶段、活动执行数量与版本检查状态；
- Provider 启用状态、注册模型与可用模型数量；
- 当日调用、成功率、平均延迟和缓存命中率；
- 输入、缓存输入、输出、推理及总 Token；
- Provider、模型和脱敏网关 Key ID 排行；
- 只含统一错误代码和运行元数据的脱敏调用日志。

## 更新与部署

该模块是全局拓展中的内置例外。`update/core.py` 会同步它的静态实现与说明，但必须保留部署机上的 `gateway_config.json`、`input_data.md`、`data/`、`artifacts/` 和运行状态。存在本地配置时，更新后继续保持激活；源码默认清单不能覆盖部署机的真实激活状态。

常见失败：

- `401`：状态 Token 缺失或无效；
- `503`：网关未配置状态 Token，或状态 Token 与其他网关 Token 重复；
- 连接/TLS/超时：检查 `base_url`、反向代理、证书和局域网访问；
- 响应合同错误：确认连接的是支持 `kemo.gateway_status` 的 Kemo 网关版本。

