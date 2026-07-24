---
type: component
project: kemo-agent
domain: web
module: web-service
layer: L2
scope: project
status: active
summary: web/service.py — WebRunService 适配层（新增会话压缩/运行状态/上下文窗口/Token 统计/系统 Cron 日志解析）
source: "web/web-service.md"
updated: 2026-07-23
verified: true
tags: [kemo-agent, web, 服务, 适配层, compress, runtime_status, token_statistics, context_window, undo_last_round, memory_ref, memory_backlog, restart]
---
# web/service.py — WebRunService 适配层

`E:\code\kemo-agent\web\service.py`

## 模块职责

- 校验 Web 请求参数
- 管理会话与窗口读取
- 调用 run 核心事件引擎
- 文件树浏览、下载、删除、上传、写入、移动、建目录
- 头像上传与获取
- 用户人格和全局人格读写
- 子代理列表与删除（新增）
- 消息模块：状态/健康检查/删除（新增）
- 拓展模块：库存/刷新/白名单/删除（新增）
- 技能管理：文档读写/删除/白名单/ZIP下载（新增）
- 感知模块：库存/刷新/白名单/删除（新增）
- 任务计划 CRUD
- Cron 任务 CRUD
- 知识正文 CRUD
- 记忆正文 CRUD
- 配置安全编辑
- 外观偏好读写

## 新增方法（2026-07-22）

### 撤销上一轮

| 方法 | 说明 |
|------|------|
| `undo_last_round(user, session_id, expected_round, prompt, source)` | 调用 `run.history.undo_last_round`，精确回滚最近一轮；拒绝运行中的会话；HistoryError 转为 409 Conflict |

### 记忆 CRUD 精确层级化

| 方法 | 变更说明 |
|------|---------|
| `memory_item(user, tier, filename)` | 新增 `tier` 参数，改用 `locate_in_tier` 精确定位；返回包含 `memory_ref` |
| `put_memory(user, filename, content, tier)` | 优先用 `locate_in_tier(tier, ...)` 定位已有文件；跨层晋升后用 `locate_in_tier` 重新定位；返回 `memory_ref` |
| `delete_memory(user, tier, filename)` | 新增 `tier` 参数，改用 `locate_in_tier` + `_delete_location`；返回 `index_removed`/`file_removed`/`repaired_orphan` |

### max_per_round 移除

- `_CONFIG_SOURCE_PATHS` 中删除 `tools.max_per_round`
- `overview` / settings 响应中删除 `tool_max_per_round` 字段

## 新增方法（2026-07-21）

### 临时文件批量操作

| 方法 | 说明 |
|------|------|
| `delete_tmp_files(paths)` | 批量删除（最多 10000 个），含路径校验和空目录清理 |
| `delete_all_tmp_files()` | 删除全部 tmp 文件 |
| `_prune_empty_tmp_directories()` | 删除删除后产生的空目录 |

### 子代理删除

| 方法 | 说明 |
|------|------|
| `delete_user_agent(user, agent)` | 删除用户子代理，tombstone 模式（os.replace → shutil.rmtree），失败回滚 |

### 消息模块管理

| 方法 | 说明 |
|------|------|
| `_message_module_directory(user, module_name)` | 定位消息模块目录，校验名称和绑定用户 |
| `_message_transport_item(config, directory, components, issues)` | 构建完整 Transport 信息（含日志、附件统计） |
| `_message_logs(config)` | 解析 Markdown 日志为结构化条目（入站/出站/附件），最多 500 条 |
| `check_message_module(user, module_name)` | 健康检查，调用 RuntimeHost.check_message_transport |
| `delete_message_module(user, module_name)` | 删除消息模块，先注释 Transport 再 tombstone 删除 |

#### 消息日志解析

使用正则表达式从 `log/*.md` 中提取结构化消息记录：

- `_MESSAGE_LOG_HEADING` — 匹配 `## timestamp | chat_type | chat_id` 标题
- `_MESSAGE_LOG_INBOUND` — 提取入站消息文本
- `_MESSAGE_LOG_OUTBOUND` — 提取出站消息文本
- `_MESSAGE_LOG_ATTACHMENT` — 提取附件信息（名称、MIME、大小）
- `_MESSAGE_LOG_OUTBOUND_ATTACHMENT` — 提取出站附件信息（名称、路径）
- 时区：`_BEIJING = ZoneInfo("Asia/Shanghai")`
- 上限：`_MESSAGE_LOG_LIMIT = 500`

### 拓展模块管理

| 方法 | 说明 |
|------|------|
| `_expand_module_directory(user, scope, module_name)` | 定位拓展模块目录 |
| `refresh_expand_module(user, scope, module_name)` | 执行 data_update.py 刷新数据（120s 超时） |
| `set_expand_module_enabled(user, scope, module_name, enabled)` | 白名单开关（仅 user 层不支持） |
| `delete_expand_module(user, scope, module_name)` | 删除拓展模块（仅用户层允许删除） |
| `_read_expand_text(module_dir, file_name)` | 读取拓展内文件 |
| `_expand_control_sections(content)` | 分离 expand_control.md 的注入层和操作层 |
| `_expand_prompt_piece(...)` | 拼接拓展 Prompt 片段 |

### 技能管理

| 方法 | 说明 |
|------|------|
| `_skill_directory(user, category, skill_name)` | 定位技能目录，按分类校验 |
| `skill_document(user, category, skill_name)` | 读取 SKILL.md |
| `put_skill_document(user, category, skill_name, content)` | 更新 SKILL.md（含验证回滚） |
| `delete_skill(user, category, skill_name)` | 删除技能（仅 agent_generated/user_created） |
| `set_skill_enabled(user, category, skill_name, enabled)` | 白名单开关（仅 builtin/shared） |
| `skill_archive(user, category, skill_name)` | 打包 ZIP 下载 |

### 感知模块管理

| 方法 | 说明 |
|------|------|
| `_sense_module_directory(user, module_name)` | 定位感知模块目录 |
| `refresh_sense_module(user, module_name)` | 执行 data_update.py 刷新数据（120s 超时） |
| `set_sense_module_enabled(user, module_name, enabled)` | 白名单开关 |
| `delete_sense_module(user, module_name)` | 删除感知模块（tombstone 模式） |
| `_sense_markdown(item)` | 读取感知数据 Markdown |

### 配置注入

`WebRunService.__init__` 新增可选回调：

- `message_health_checker: Callable[[str, str], dict] | None` — 注入 RuntimeHost.check_message_transport
- `message_transport_remover: Callable[[str, str], None] | None` — 注入 RuntimeHost.remove_message_transport

## 安全措施

- 文件操作拒绝路径穿越、符号链接、隐藏文件、__pycache__
- 子代理/消息/拓展/感知删除均使用 tombstone 模式（os.replace → shutil.rmtree），失败回滚
- 拓展/感知刷新通过 subprocess.run 执行 data_update.py，有 120s 超时
- 技能更新含验证回滚（load_prompt_source_registry 校验失败恢复旧文件）
- 配置编辑拒绝 *** 占位符
- 消息模块操作校验 bound_user 权限
- 拓展/感知目录检查拒绝符号链接和目录联接

## skill catalog

skills() 新增统一 `items` 列表，把 tools 和 prompt_skills 按 category（builtin/shared/agent_generated/user_created）合并，每项标注 editable/toggleable/downloadable。

## 关键变更（2026-07-22 追加）

### 单用户 Web Chat 并发闸门（_UserChatGate）

新增 `_UserChatGate` 类，按用户隔离聊天并发：

```python
class _UserChatGate:
    def __init__(self, max_concurrent, max_pending, pending_timeout)
    def acquire(self, *, cancel_event=None) -> bool  # 获取槽位，可等待
    def release(self) -> None                         # 释放槽位
    def status(self) -> dict[str, int]                # active_chats/max_chats/pending_chats/max_pending
    def matches(self, max_concurrent, max_pending, pending_timeout) -> bool
```

- `web.max_concurrent_chats`（默认 3）控制单用户并发聊天上限
- `web.max_pending_chats`（默认 5）控制等待队列上限
- `web.pending_chat_timeout`（默认 30s）控制等待超时
- 队列满或超时返回 HTTP 503 + `Retry-After`
- 空闲闸门自动采用新保存的 Web 限制

### TooManyChatsError

```python
class TooManyChatsError(WebServiceError):
    code = "too_many_chats"
    status = 503
    headers = {"Retry-After": str(max(1, int(retry_after)))}
```

### extract_session_memory

```python
def extract_session_memory(self, user, session_id, *, source="web") -> dict
```

从最新完整归档轮次提取记忆候选。调用 `_extract_round_memory`（来自 run.engine），拒绝运行中的会话。

### 拥塞状态（congestion）

`runtime_status()` 新增 `congestion` 字段：
- `congestion.provider`：调用 `provider_semaphore_status()`
- `congestion.web`：调用 `_get_chat_gate(name).status()`
- `congestion.message_router`：调用 `router_ref.queue_status()`

### router_ref

`WebRunService.__init__` 新增 `router_ref` 参数，注入 MessageRouter 引用以查询消息路由队列状态和 Transport 注册表。

### stream_chat 并发闸门

`stream_chat()` 在创建 Run 前先获取 `_UserChatGate` 槽位，Run 结束时释放。队列满或超时抛 `TooManyChatsError`。

### _CONFIG_SOURCE_PATHS 新增

- `provider_runtime.max_concurrent_requests`
- `provider_runtime.request_semaphore_timeout`
- `web.max_concurrent_chats`
- `web.max_pending_chats`
- `web.pending_chat_timeout`
- `message.max_queued_messages`
- `agent_runtime.queue_maxsize`
- `cron.avoid_congestion`
- `cron.congestion_threshold_ratio`

### overview 新增字段

`overview` 响应中新增 `provider_max_concurrent`、`web_max_chats`、`message_max_queued`、`agent_queue_maxsize`。

## 关键变更（2026-07-22 第二批追加）

### 持久化会话管理 API

新增三个端点方法：

| 方法 | 说明 |
|------|------|
| `active_session(user)` | 获取或预约用户的持久交互会话 |
| `create_session(user)` | 创建新会话并绑定到 active key |
| `close_session(user, session_id, *, source)` | 关闭会话（lifecycle=closed） |

### extract_session_memory 改为统一游标管线（2026-07-23）

改用 `_extract_memory_backlog` 替代原有内联游标逻辑，与引擎压缩管线共用同一 `memory_processed_round` 游标实现：

```python
return _extract_memory_backlog(
    root=self.root, user=name, source=normalized_source,
    session_id=normalized_session, directory=directory,
    window=window, config=config, agent_runner=runner,
    cancel_event=None,
)
```

### has_active_runs（2026-07-23 新增）

```python
def has_active_runs(self) -> bool:
    with self._active_runs_lock:
        return bool(self._active_runs)
```

供系统重启端点检查是否有正在运行的对话。

### close_session 排队记忆提取（2026-07-23 新增）

关闭会话前先调用 `queue_memory_extraction` 标记待提取轮次为 `queued`，使 Maintenance 或下次压缩时能继续处理。

### close_session 队列历史摘要（2026-07-23 更新）

关闭会话后调用 `queue_history_summary`，为已关闭的会话自动排队卡片标题与摘要生成。响应中新增 `summary` 字段（`{status, reason, rounds}`）。

### 历史分页（2026-07-23 新增）

`history()` 方法新增 `limit` 和 `before` 参数，支持按轮次翻页：

```python
def history(self, user, session_id, *, source="web", limit=None, before=None) -> dict
```

- `limit`（1-100）：返回最近 N 轮，默认返回全部
- `before`（>=1）：返回此轮次之前的内容（分页游标）
- 响应新增 `pagination` 字段：`{limit, total_rounds, first_round, last_round, has_more_before, next_before}`
- 分页逻辑：按轮次分组消息 → 根据 `end_round = before-1` 截断 → 取最后 `limit` 轮 → 只保留范围内的 `round_metrics`、`round_traces`

### 会话列表新增摘要字段

`_index_session_payload` 和 `list_sessions` 响应新增 `summary_status`、`summary_target_round`、`summary_completed_round`、`summary_retry_at`、`summary_retry_count` 字段。

## 会话租约系统（2026-07-23 新增）

### 背景

多标签页共享同一会话时，删除/关闭/清空操作需要感知其他标签页的存在，避免误删正在使用中的会话。

### 实现

```python
self._session_leases: dict[tuple[str, str, str], dict[str, float]]
```

以 `(user, source, session_id)` 为键，`{client_id: monotonic_timestamp}` 为值的内存字典。租约 TTL 为 `SESSION_LEASE_TTL_SECONDS = 45.0` 秒。

### 核心方法

| 方法 | 说明 |
|------|------|
| `require_client_id(client_id, optional=True)` | 校验 client_id 格式：8–128 位字母/数字/下划线/连字符 |
| `_prune_session_leases_locked(now)` | 清理过期租约（超过 TTL 45s） |
| `_touch_session_lease_locked(user, source, session_id, client_id)` | 更新租约时间戳，返回当前活动客户端数 |
| `_release_session_lease_locked(user, source, session_id, client_id)` | 释放指定客户端租约，返回剩余活动客户端数 |

### 交互 API

| 方法 | 说明 |
|------|------|
| `session_lease(user, session_id, client_id)` | POST `/lease` 续租端点 |
| `release_session_lease(user, session_id, client_id)` | POST `/lease/release` 释放端点 |
| `active_session(user, client_id)` | 增 `client_id` 参数，绑定活跃会话时自动续租 |
| `create_session(user, client_id)` | 增 `client_id` 参数，创建会话时自动续租，移除 running 会话检查 |
| `close_session(user, session_id, *, source, client_id)` | **其他客户端仍持有租约时不关闭**：返回 `deferred: true` 和 `active_clients` 计数，跳过记忆提取 |
| `delete_session(user, session_id, *, source, client_id)` | **其他客户端持有租约时返回 409**：检查 `other_clients` 非空则抛 `ConflictError` |
| `stream_chat(...)` | 开始运行会话时自动续租 |
| `_interactive_active_key(user, client_id)` | active_key 加入 `client_id` 后缀：`interactive:<user>:<client_id>`，实现标签页级会话隔离 |

### 前端集成

- `AppShell` 通过 `getPageClientId()` 获取唯一 `clientId`
- 每 15 秒心跳续租（`setInterval(touch, 15000)`，页面卸载时释放）
- `sessionClient.ts` 使用 `BroadcastChannel` 实现跨标签页通信
- 其他标签页删除会话时通过 channel 通知当前页解绑并报错

### FILE_UPLOAD_MAX_BYTES（2026-07-23 更新）

从 25MB 提升到 80MB。

### stream_plan 连续执行（2026-07-23 新增）

```python
def stream_plan(self, user, session_id, plan_id, *, cancel_event, run_id) -> Iterator[RunEvent]
```

Web 驱动的任务计划连续执行。由前端 `executePlan()` 触发，流程：
1. 原子领取计划状态为 `running`（检查 pending/approved/paused 才允许）
2. 扫描找到第一个可执行步骤（pending + 依赖已完成）
3. 构造 control_prompt 注入「【任务计划连续执行】」前缀，描述起始步骤
4. 调用 `stream_chat` 传入 `task_plan_id` 和 `task_plan_mode=agent_managed`，主智能体在单轮内自主调用 `step_done` 并依据返回的 `next_step` 持续执行
5. 失败时自动将 `running` 回退为 `paused`（通过 `store.update` lambda）

### command_plan（2026-07-23 新增）

```python
def command_plan(self, user, plan_id, action) -> dict
```

支持 `pause` 和 `cancel` 两种状态指令：
- `pause`：调用 `pause_plan`（来自 `run/task_plan_executor`）
- `cancel`：调用 `cancel_plan`
- 计划不存在返回 404，状态不合法返回 409

### 文件操作重构（2026-07-23）

- `delete_files` / `delete_all_files`：文件空间批量删除，复用 `_delete_area_files`
- `_delete_area_files`：从 `delete_tmp_files` 提取的通用方法，接受 `directory` 和 `item_label` 参数
- `_prune_empty_directories`：从 `_prune_empty_tmp_directories` 泛化，接受任意目录
- TmpDeleteManyBody → DeleteManyBody（共用）
- Web API 新增文件空间批量删除端点

### _CONFIG_SOURCE_PATHS 新增（2026-07-23）

- `memory.extraction_mode`：提取模式枚举

### compress_context 联动记忆提取改进（2026-07-23）

手动压缩后，优先使用引擎返回的 `memory` 字段，引擎未提取时才独立调用。记忆提取不再作为压缩的副效应独立触发，统一由引擎游标管线承载。

### 重要记忆 Web API 保护（2026-07-23）

- `update_important_memory` 拒绝空字符串
- `delete_important_memory` 端点已从 `web/app.py` 中移除

### compress_context 联动记忆提取

手动压缩后自动调用 `extract_session_memory`，失败时标记 `retry_pending=true`。

### _current_context_status 重构

实时计算上下文快照，使用 `ContextPolicy` + `select_context` + `build_context_snapshot`。

### _usage_cache_tokens 增强

优先读取规范化字段，保留明确的零值。

### provider_request_count

Token 统计中累计 Provider 请求次数。

### stream_chat 传入 _history_active_key

## 相关笔记

- [[web-总览]]
- [[web-app]]
- [[frontend-chat]]
- [[frontend-client]]
- [[frontend-modules]]
- [[run-runtime_host]]
- [[provider-factory]]
- [[run-engine]]
- [[run-history_index]]
- [[run-context]]
## 关键变更（2026-07-26）

### cancel_run

```python
def cancel_run(self, user, run_id) -> dict[str, Any]
```

新端点，实现用户紧急停止：
1. 通过 `_active_runs_lock` 查找指定 `run_id`
2. 设置 `active.cancel_event.set()` 触发引擎取消
3. 返回 `{run_id, user, session_id, status: "stopping"}`

### 文件避免覆盖（avoid_overwrite）

`_write_area_file` 新增 `avoid_overwrite` 参数：
- 文件已存在时自动追加 `(2)`、`(3)` 等序号
- 使用 `_reject_link_path` 检查每个候选路径
- 返回 `renamed` 字段标识是否已重命名

`sava_file` 调用时固定 `avoid_overwrite=True`，使用 `_file_upload_lock` 保护并发。

### require_uploaded_files

```python
def require_uploaded_files(self, user, uploaded_files) -> list[dict]
```

校验 Web 端上传的文件引用列表：
- 最多 20 个文件
- 每个文件必须在 `users/<user>/file_upload/` 目录内
- 拒绝符号链接、重复路径、不存在的文件
- 返回 `[{name, path, size}]`

注入到 `stream_chat()` 的 `request["uploaded_files"]`。

### reasoning_effort 注入

`overview()` 和 `settings()` 响应中新增 `reasoning_effort` 字段（来自 `normalize_reasoning_effort`）。`_CONFIG_SOURCE_PATHS` 新增。

### memory_extraction_policy=queue

`compress_context()` 调用时传 `memory_extraction_policy: "queue"`，改为登记后台队列而非同步提取。

### 失败时抛 WebServiceError

压缩成功但记忆任务登记失败时抛 `WebServiceError("上下文压缩成功，但后台记忆任务登记失败")`，替代旧版静默构造失败 memory dict。

### round_metrics 状态扩展

`round_metrics` 每个条目新增：
- `status`：`"completed"` 或 `"cancelled"`
- `cancelled`：布尔值
- `cancel_reason`：字符串

### hidden_subprocess_kwargs

sense 和 expand 的手动刷新调用（`subprocess.run`）统一传入 `**hidden_subprocess_kwargs()`，隐藏 Windows 控制台窗口。

### ActiveRun 取消事件

```python
@dataclass
class ActiveRun:
    cancel_event: threading.Event = field(default_factory=threading.Event)
```

`ActiveRun` 自带 `cancel_event`，取代引擎传入的独立 cancel_event。

### stream_chat 输出队列终止优化

`put()` 函数在终端事件（done/error/_WORKER_DONE/BaseException）时依然尝试入队列，非终端事件在取消时丢弃。
