## 注入层

Kemo Bridge 2.1.0 连接手机工作台与外部 kemo-agent。采集数据只有对端数量、最近活动和近 24 小时异常，不能据此获得管理权限。已获授权的调用方可用 `peer_list` 查看出站目标，用 `peer_chat_submit` 提交、`peer_chat_query` 续查。

## 操作层

# Kemo Bridge 2.1.0 接入契约

## 公共认证

请本机操作员在控制台注册对端，并通过可信渠道交付接入地址、登记的对端 UUID 及本机为它签发的连接密钥。手机与外部智能体必须分别注册；外部智能体的登记 UUID 取自其自己的桥接控制台。每次 HTTP 请求及 WS 握手都发送：

```http
Authorization: Bearer <peer-secret>
X-Kemo-Peer-Id: <peer-uuid>
```

以 `GET /v1/version`（手机）或 `GET /peer/v1/version`（智能体）核对版本和 `public_url`。智能体分支另外在 `instance_uuid` 返回服务端的本机 UUID；请求头的 `X-Kemo-Peer-Id` 仍是来访实例的 UUID。地址未配置时为 `null`；这不代表服务离线。成功通过身份校验后，响应头回显 `X-Kemo-Peer-Id`、`X-Kemo-Bound-User`、`X-Kemo-Channel`、`X-Kemo-Capabilities`（逗号分隔）。客户端不能指定角色或切换绑定用户。

| HTTP | 错误码 | 含义 |
| --- | --- | --- |
| 401 | `auth_failed` | 密钥无效、已轮换、对端停用或吊销 |
| 403 | `peer_not_registered` | 密钥有效，所报 UUID 未注册 |
| 403 | `identity_mismatch` | 密钥和 UUID 分属不同对端；本机记录泄露告警 |
| 403 | `capability_not_granted` | 路由、方法或通道未授予该能力 |
| 403 | `artifact_not_granted` | 无该文件授权或授权已吊销 |
| 410 | `artifact_gone` | 已授权路径上的文件不可用 |

身份与能力错误以 `{"error":"错误码"}` 返回。WS 握手拒绝使用 4401/4403 及对应 reason；已建立的设备 WS 在下一次处理消息或心跳时复验。轮换后必须同时更新请求与重连凭据。

## 智能体分支：`/peer/v1`

固定能力：`chat`、`status`、`artifact_download`、`artifact_upload`。不提供配置、历史列表、定时任务、任务计划、知识、技能、拓展或设备操作 API。

1. `GET /peer/v1/status` 返回 `{"online":true,"busy":false,"version":"2.1.0"}`。busy 只反映该 peer 当前的桥接对话，不透露本地主人的其他会话。
2. `POST /peer/v1/chat` JSON：`{"session_id":"对端会话号","run_id":"本轮唯一编号","prompt":"请简要回复"}`。`run_id` 可省略；显式值限字母、数字、下划线和连字符。桥接按 peer 隔离这些标识。响应为 SSE；读取 `text_delta.content`，以 `{"type":"done","status":"completed"}` 或其他终态结束。只投影回复正文与终态，不返回推理、工具卡片和上游元数据。短时掉线可用同一 session/run ID 重新请求并去重 SSE ID；新编号会提交新一轮。
3. 收到本机提供的 `grant_id` 后，`POST /peer/v1/artifacts/{grant_id}` 协商元数据；用**同一组身份头** GET 返回的 `download_url`。未配置公开地址时相对当前 URL 使用。支持 `Range: bytes=0-65535`；206 返回实际区间、416 表示不可满足。不要用 `asset_id` 代替 grant。
4. `POST /peer/v1/upload` 使用 multipart：`file` 必填，`filename`、`display_name` 可选。不传目录，文件只写入绑定用户的 `file_upload/`。返回 `file_id`、项目相对 `path`、`size_bytes`。上传不会自动进入对话，也不自动产生下载授权。

对话失败时，终态帧例如 `{"type":"done","status":"failed","code":"upstream_auth_missing","transient":false,"phase":"auth"}`。只有 `failed`、`cancelled`、`interrupted` 附加这三个字段；成功仍是 `{"type":"done","status":"completed"}`。取消时 `code` 为 `null`、`transient` 为 `false`；上游报告中断或流提前结束时用 `upstream_stream_incomplete`。

- `code`：`upstream_auth_missing`、`upstream_auth_rejected`、`upstream_unreachable`、`upstream_timeout`、`upstream_http_error`、`upstream_invalid_payload`、`upstream_stream_incomplete` 或 `bridge_internal`。
- `transient`：布尔值，表示故障是否可能是暂时的，不表示可以用新的运行编号自动重发。
- `phase`：`auth`、`connect`、`request`、`stream` 或 `bridge`。不返回上游原始错误内容或本机取证信息。

出站客户端在上述 POST 加 `X-Kemo-Chat-Ack: 1`，只接收首帧 `{"type":"accepted"}`，对话脱离 HTTP 连接继续执行。然后用 `GET /peer/v1/chat?session_id=<原始会话>&after_event=<游标>` 加 `X-Kemo-Run-Id: <原始运行编号>` 读取已保存的 SSE。单页最多处理 128 条事件，含控制帧最多 1 MiB（实际 UTF-8 SSE 字节数）；客户端最多接受 4 MiB。超长正文在新写入时拆为可分别续查的正文帧，原文不截断。若老日志有单帧大到放不进一页，返回 HTTP 413 `peer_event_too_large`，不跳过该帧。没有新增正文时也立即返回。非终态页以 `{"type":"page","after_event":...}` 收尾；游标已读到终点时，该帧可携带 `terminal`。只查原 run，不会创建新 run；不存在或已过期返回 404。常规 POST 不加 ACK 头仍持续推送正文与终态，成功终态帧不变。

最小接入示例（示例变量应从本机操作员安全获取；不要把真实值写入文档或日志）：

```bash
curl -fsS "$BRIDGE_URL/peer/v1/status" \
  -H "Authorization: Bearer $PEER_SECRET" -H "X-Kemo-Peer-Id: $PEER_ID"

curl -fsSN "$BRIDGE_URL/peer/v1/chat" \
  -H "Authorization: Bearer $PEER_SECRET" -H "X-Kemo-Peer-Id: $PEER_ID" \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"bridge-demo","run_id":"bridge-demo-1","prompt":"回复：连接正常。"}'
```

## 手机工作台分支：`/v1`

- `/chat`、`/guidance`、`/conversations*`、`/runs*` 保留手机工作台的对话、回放、取消、压缩和撤销。WS `/v1/ws?session_id=...` 必须绑定会话；设备 ID 即已认证 peer UUID，客户端自报设备 ID 不参与授权。
- `POST /v1/device-commands` 传 `action`、`arguments`、可选 `ttl_seconds`。只允许 `alarm.create`、`timer.start`、`calendar.event.create`、`todo.create`，进入队列且标记 `confirmation=system_ui`。手机通过 WS 的 `device.command.ack` 回传 `command_id` 与 `status`；提交回执不等于已执行。
- `/cron` 和 `/task_plans` 可管理绑定用户的任务；`/knowledge*`、`/skills` 只读。技能可用 scope、enabled 筛选，仅返回元数据。
- 扩展只读：`GET /v1/expands` 读列表，`GET /v1/expands/data` 或 `GET /v1/expands/{scope}/{name}/data` 读实际注入的 Markdown。未注入则 `data: null`；不读取操作手册、运行状态、面板动作或采集原文作为回退。没有调用或切换拓展的 API。
- `GET /v1/token_usage` 读取已有 CardStream 数据，日期按上海时区且含起止日，未传起止取当天；`group_by=day|session|model`。存量记录没有可信历史模型归属，归入 `unknown`；没有已配置日额度时 `daily_limit: null`。
- `GET/PATCH /v1/reasoning` 读当前模型声明的 `available`，只保存其中的档位到用户配置。如上游无能力声明，列表为空。 `GET/PATCH /v1/config` 脱敏后回显：已设敏感字段为 `"***"`，未设为 `null`，写入只接受整体字符串或清空，不接受占位符、子字段或对象补丁。
- `GET /v1/files` 只列目录文件；`POST /v1/upload` 只落绑定用户上传目录。下载必须先由本机登记 grant，手机在 `GET /v1/artifacts` 列出自己的 grant；`GET /v1/artifacts/{grant_id}` 查元数据、后缀 `/content` 下载。手机可 `POST /v1/artifacts/{grant_id}/revoke` 撤销自己的授权。旧按目录路径直下的入口不可用。

## 本机出站命令

本机 A 调 B：在 A 的工作台「对外凭据 → 导入出站凭据」保存 B 的桥接 HTTP(S) 基址（不含 `/peer/v1`）、**A 在 B 登记的** `peer_id`、备注和 **B 签给 A** 的密钥。A 发 `Authorization: Bearer <outbound.secret>` 和 `X-Kemo-Peer-Id: <outbound.peer_id>`；A 签给 B 的密钥不能用于这一方向。工作台原子写入私有的 `_workspace_credentials.yaml`；缺省 `allowed_users` 仅授权 `kesepain`，显式空列表不授权任何人。用户名区分大小写，不允许空值、重复值；每条最多 64 个，每个最多 128 个字符。已有规则不合法时整份文件不可用，不悄悄跳过。再次导入同地址与登记 UUID 会覆盖这一条，包括授权名单。

以下均经 `expand_call(scope="global", module="kemo_bridge", command=..., params=...)`。地址只由工作台配置，模型不能改写地址、导入密钥或追随 HTTP 重定向。未获准目标与不存在的目标同样返回 `target_not_found`。

| 命令 | 参数与结果 |
| --- | --- |
| `peer_list` | 无参数；`targets` 只含 `target_id`、`label`、`url`，没有密钥和密钥提示。选择目标用 `target_id`，不用协议头里的 UUID。 |
| `peer_status` | 可选 `target_id`；查指定目标或并行查所有获准目标。批量结果按目标分列，每项独立超时或失败。 |
| `peer_chat_submit` | `target_id, session_id, prompt, run_id?`；短触发，只等第一个事件。建议在调用前生成并保存 `run_id`。发送前用尽预算返回 `budget_exhausted_before_sent` 和原编号，可以用同一请求重提；请求可能已发但未收到回执则返回 `state:"unknown"`，只用同一编号续查，不重发。可能已发的同号同请求返回原提交回执；不同 prompt 返回 `run_prompt_conflict`。 |
| `peer_chat_query` | `target_id, run_id, after_event?`（首次为 0）；可选 `session_id` 用于核对。只给本次增量正文；下次传最近一次成功返回的 `after_event`。账本确认尚未发出时返回 `run_not_submitted`，不访问对端；可能已发时，对端 404 不能证明未提交。 |
| `peer_chat` | **本命令单次最多实际等待约 8 秒。** `max_wait_seconds` 表示调用方希望等待多久（秒，默认 60，允许 (0, 300]），不延长单次持锁时间。提交后循环短查询，聚合本次调用收到的正文。 |

`peer_chat` 的 `ok:true` 回执还包含 `waited_seconds`（本次实际耗时，秒）、`budget_remaining_seconds`（请求预算减去实际耗时，不低于 0）。剩余预算由调用方自行跟踪，不会在本次 action 里继续等待，也不是每次续查的时限。未结束（`running`）或接单未知（`unknown`）时，`next_action` 提供 `peer_chat_query` 和原 `target_id`、`run_id`、`after_event`，不要换号重提；其余状态的 `next_action` 为 `null`。

对话结果以 `ok:true` 返回 `state`、`text`、`frames` 和续查句柄，不返回 SSE 原始帧。`frames` 计数本次可见的正文与终态帧，不计接单和分页控制帧；HTTP 401 转为一条合成的失败终态。`failed/cancelled/interrupted` 附 `failure:{code,transient,phase}`。配置或网络等动作错误以 `ok:false` 返回，由宿主作为操作异常处理。

若 `peer_chat` 已接单但中途查询返回动作错误，不交付本次暂存的正文；错误中的 `run_id` 和 `after_event` 指向最后已交付的位置（首次通常为 0），按它续查可重新读到暂存正文。宿主错误说明也会带上这两个值。

同方向多轮始终传相同的**原始** `session_id`，每轮换一个新的 `run_id`；不要回填对端内部 `kb_...` 号。会话归属以「远端 `instance_uuid`、来访 `peer_id`、原始 `session_id`」为键。同一远端和来访身份的不同地址共享归属，不能由另一个本机用户复用会话。客户端通过已认证的 `GET /peer/v1/version` 确认 UUID，按目标缓存 **24 小时**。取不到有效身份时拒绝（`remote_identity_unavailable`）；已登记目标返回不同 UUID 时拒绝（`remote_identity_changed`），不重绑旧归属。旧账本若有无法确认目标的同名会话，保守保留旧归属。启用出站前，先将接收端更新到支持版本身份与只读 GET 续查的版本；客户端不会用 POST 替代查询。

本地账本从首次收到对端明确终态起保留回执 **30 天**；以后在新提交时按批次清理。未确认终态的记录（包括 `reserved`、`sent_unknown`、已接单尚未结束、本地断流或鉴权失败）不按年龄清理，清理也不解除会话归属。这个窗口只保证本地去重，不延长对端的回放保留期；过期后也不要复用旧 `run_id` 重提。

接收端成功认证审计对同一 peer 每分钟最多记一次，鉴权和撤销检查仍逐请求进行。空页不记 `message_sent`；有正文时每个 run 只记一次。`peer_audit` 在 daemon 内分批清理超过 **30 天** 的旧记录，不清理未终态 run 关联的记录。

## 本机登记产物与运行控制

- 由本机控制台或受信任的本机进程登记单个文件。内部请求使用 `POST /internal/artifacts` 和 `X-Kemo-Internal: <session_secret>`；JSON 包含 `peer_id`、`file_path`（绝对路径），可选 `media_kind`、`display_name`、`asset_id`、`created_by_run`。只接受该 peer 用户的 `download/` 或 `file_upload/` 中的普通文件。对端不能发起登记。
- `artifact_grants.file_path` 是下载依据。只要原路径仍有可读文件且授权有效，可在 120 秒后或多天后下载。已过期的模型 `asset_id` 不可再引用；grant 不延长它的寿命，也不删除文件。
- `GET /internal/health` 和 `POST /internal/device-command` 同样校验内部共享头，不能仅凭来源 IP。反代只应公布两条对端分支，不应转发 `/internal`；桥接监听器不挂载管理 API。
- 本地 `expand_call(scope="global", module="kemo_bridge", ...)` 支持上述出站命令，以及 `initialize`、`status`、`start`、`stop`、`deactivate`、`restart` 等运行控制、`device_action`（指定一个已注册的手机，把白名单操作送给它确认）和 `device_action_status`。**该入口没有 peer、密钥或 grant 管理命令。** 管理动作只由宿主授权的内嵌工作台执行。宿主「通用配置」负责运行控制，不再设置单独的控制台访问密钥。
