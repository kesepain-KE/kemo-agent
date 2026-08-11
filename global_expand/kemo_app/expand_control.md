## 注入层

# kemo app 桥接服务

kemo-agent 与 Android App 之间的常驻 FastAPI 桥接服务。监听配置指定的地址与 `8742` 端口，
提供设备/用户两级认证、SSE 对话、会话、任务、定时、状态、拓展感知、文件、知识、
模型、脱敏配置、WebSocket 推送与在线设备统计。公网访问由 frp/nginx 反代 + TLS 终止，本服务
不直接暴露公网。

当前桥接协议实现版本：**1.1.1**。

源码与首次部署默认为**未初始化、未激活**：`open_input=false`、没有最近成功采集时间、
不包含 `config.json`、`users.json`、设备 Token、用户密码或运行状态。克隆或更新源码不会
自动启动服务。`open_control=true` 只用于查询初始化状态和执行管理员明确要求的激活操作。

部署端一旦被管理员显式激活，常规框架更新会保留该 APP 拓展的本地激活状态；更新期间即使
凭据暂时无法校验，也不会把 `open_input=true` 重置为 `false`。首次安装仍保持未激活，管理员
显式执行 `stop` / `deactivate` 后也仍保持未激活，本地配置与凭据文件不会被更新器覆盖。

显示名称为“kemo app 桥接服务”；稳定模块标识仍为 `kemo_app`。
调用入口：`expand_call(scope="global", module="kemo_app", ...)`。

安全边界：`config.json` 只存 `token_sha256`（哈希），不存明文 Token；请求日志静默
（不记录路径/凭据）；注入层只含服务状态摘要，不含用户数据。

## 操作层

# 可用命令

## `status`

查询桥接服务运行状态（进程、端口、日志路径）。只读，无副作用。

无参数。返回 `initialized` / `configured` / `active` / `running` / `pid` / `port` / `log`，
以及仍缺少的配置项名称；不返回任何凭据值。

## `configuration_status`

查询初始化、凭据配置和激活状态，是 `status` 的安全别名。只读，无副作用。

无参数。未初始化时不会探测 `8742` 端口，因此不会把同一台机器上的其他部署实例误认为
当前源码副本已经激活。

## `start`

显式激活并启动桥接服务（守护进程模式：detached 子进程，主调用立即返回）。若已在运行则
返回 `already running`。初始化、设备 Token、会话密钥或 App 用户任一未配置时拒绝启动，
不会创建一个立即崩溃的空配置进程。

无参数。返回 `pid` / `port` / `log`。启动后可用 `status` 或 `curl http://127.0.0.1:8742/v1/health` 验证。

`activate` 是 `start` 的别名。

## `stop`

停止桥接服务（终止守护进程并清理 pid 文件）。

无参数。返回停止后的状态。

`deactivate` 是 `stop` 的别名；停止服务不会删除本地凭据，后续仍可再次显式激活。

## `restart`

重启桥接服务（stop + start）。

无参数。返回新进程状态。

# 设备 Token、用户密码与核对文件

在本拓展目录执行以下命令：

1. `python initialize_config.py`
   - 从无凭据的 `config.example.json` 创建被 Git 忽略的本地 `config.json` 与 `users.json`。
   - 自动生成随机 `session_secret`，但不会生成或保存设备 Token、用户密码。
   - 只完成本地文件初始化，不启动服务、不开放端口。
2. `python manage_device_token.py`
   - 按隐藏输入提示输入并确认设备 Token；长度至少 32 字符。
   - 明文只存在于本次终端输入中；磁盘上的 `config.json` 只保存 SHA-256。
   - 修改后必须执行 `python start_expand.py restart`。
3. `python manage_user.py <username>`
   - 将 `<username>` 替换为指定 App 用户，例如 `python manage_user.py kesepain`。
   - 按隐藏输入提示输入并确认密码；密码至少 10 字符。
   - `users.json` 只保存随机盐与 PBKDF2-HMAC-SHA256 校验值。
   - 禁用指定用户可执行 `python manage_user.py <username> --disable`。
4. `python credential_registry.py`
   - 重新生成 `credential_registry.json` 核对摘要。
   - 两个管理脚本成功后也会自动刷新该文件。
5. `python credential_registry.py --check`
   - 核对摘要是否与当前 `config.json`、`users.json` 一致；一致时返回 `ok=true`。

`credential_registry.json` 只记录设备 Token 是否已配置、截断后的哈希指纹、已配置用户名、
启用状态、PBKDF2 迭代次数和记录指纹；不保存设备 Token、用户密码、盐或完整密码哈希。

# 配置与验证指引

1. 运行 `initialize_config.py`，再在本地 `config.json` 配置实际 Web 上游地址与上游认证；
   真实凭据不得提交。
2. 配置设备 Token 和至少一个 App 用户后，运行 `configuration_status`；仅当
   `configured=true` 时才允许激活。
3. `start` 或 `activate` 显式启动服务。
4. `curl http://127.0.0.1:8742/v1/health` 应返回 `kemo_app` v1.1.1 健康状态，并包含
   `websocket_connections` 与 `connected_devices`。
5. 设备认证成功后调用 `/v1/auth/user` 获取短期会话，并通过 `X-Kemo-Session` 访问业务端点。
6. App 的 WebSocket 请求应携带 `X-Kemo-Device-Id`；拓展状态会显示在线用户、设备 ID
   与连接数，但不会显示设备 Token 或会话 Token。
7. 错误 Token 应返回 401；连续失败达到限流阈值返回 429；`stop` 后端口应释放。

# 模型列表协议边界

- `/v1/models` 只服务于用户配置中已经保存为 `provider.type="kemo"` 的 Kemo 私有协议。
- 桥接服务会先读取脱敏后的用户配置并确认协议，再使用框架侧已保存的 Kemo 凭证查询模型；
  设备端不会获得 API Key、Token 或其他上游凭证。
- Chat 兼容协议不提供模型发现服务；调用 `/v1/models` 会返回 404
  `model_catalog_unavailable_for_chat_protocol`。Chat 模型名仍由用户手动配置。
- App 在 Kemo 协议下显示可选择的模型列表，在 Chat 协议下隐藏模型列表入口并使用文本输入。

# App 文件上传

- App 调用 `POST /v1/upload`，使用 `multipart/form-data` 的 `file` 字段上传单个文件。
- `path` 参数表示当前上传目录；桥接服务会安全组合为“目录/原始文件名”，并交给用户
  `file_upload` 数据域保存。空目录表示上传到根目录。
- 单文件最大 80 MB；同名文件由框架文件服务自动生成不冲突的新名称，不直接覆盖。
- App 上传成功后会刷新当前目录，嵌套目录中的上传文件可立即查看和预览。

# 1.1.1 APP 会话来源隔离

- `/v1/chat` 固定向框架提交 `source=app`；设备请求体不能指定、覆盖或冒充其他来源。
- 会话列表、历史读取、关闭、压缩、删除、全部删除和撤销上一轮均固定访问 `source=app`。
- APP 与 Web 即使使用相同 `session_id`，其活动运行、客户端租约和历史窗口仍互相隔离。
- Web 历史页只读展示 APP 归档并标记“APP版”，不会把 APP 会话接管为网页当前对话。
- 核心更新器只覆盖本模块的公开代码和说明，部署端的 `config.json`、`users.json`、
  `credential_registry.json`、激活状态、采集摘要、PID、连接状态与日志均保留。

# 1.1.0 传输与运行控制

- `/v1/chat` 使用 SSE 专用传输：响应体读取不设置 30 秒截止时间，并每 15 秒发送一次
  心跳。多模态、绘图等长时间工具调用不会因为短暂无正文事件而被桥接层误判为超时。
- 普通 REST 请求继续使用配置中的有限超时，避免非流式请求无限占用连接。
- 正在运行的对话可通过 `/v1/guidance` 追加引导，也可通过
  `/v1/runs/{run_id}/cancel` 请求中断；运行任务不依赖某个 Android 页面是否仍在前台。
- 上游已经开始返回 SSE 后发生的错误会转换为可读的流事件，而不是向客户端暴露异常堆栈。

# 多模态与文件预览边界

- App 可上传拍照图片、图库媒体和普通文件；桥接服务负责鉴权、安全路径组合和字节转发，
  实际多模态识别能力由框架与当前模型共同决定。
- 框架生成或文件栏取得的图片、音频和视频可经文件下载接口交给 App 预览；其他文件保留
  文件气泡、下载和受支持格式预览能力。
- 桥接层不转码音视频，也不绕过框架的文件域权限和大小限制；设备端应对不受系统解码器
  支持的媒体格式提供可读错误提示。

> `config.json` 当前哈希仅用于本机迁移验证。正式使用前必须通过管理脚本替换，并重启服务。
