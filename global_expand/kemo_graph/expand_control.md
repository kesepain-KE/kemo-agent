## 注入层

# Kemo Graph 外挂文档站

本拓展把独立的 kemo-graph 项目接成按需调用的“超级文档站”。它只公开管理员注册的
Library ID、绝对 Store 位置、文档来源和最近一次手动检查状态。

授权分为两层：用户先通过全局 Expand 白名单，再由 Library 的 `allowed_users` 决定
是否可见；省略时仅 `admin_users` 可见，`["*"]` 才是公共库。写操作只允许管理员，
`owner_id` 仅是 Store 元数据。私有库不会写入所有用户共享的 Prompt 摘要。

它不属于 kemo-agent 的知识库或记忆运行链路：

- 不替换、不增强、不缩减用户、共享、全局知识库；
- 不替换、不增强、不缩减永久记忆、临时重要记忆或临时三层记忆；
- 不在每轮对话、工具循环或 Prompt 刷新时访问 kemo-graph；
- 不再注册后台同步、定时构建或自动 ingest；
- kemo-graph 不可用时，不影响本地知识、记忆和正常对话。

只有用户明确要求查询、更新或维护此外挂文档站时，才调用
`expand_call(scope="global", module="kemo_graph", ...)`。仅“继续”“下一步”“重来”
等短指令不构成新的查询授权。

## 注册表

权威配置为模块目录内的 `graph_config.json`，使用 `schema_version=2`。工具调用只能传
`library_ids`，不能传用户消息里的裸绝对路径。稳定 ID 必须由管理员显式配置，不能用
显示名称或路径哈希临时生成。

支持两种库：

| kind | 用途 | 必要字段 |
|---|---|---|
| `service_default` | kemo-graph 项目自带文档库 | `id`、`display_name` |
| `portable` | 在管理员指定绝对位置创建或挂载独立 Store | `id`、`display_name`、`store_root`，可选 `source_roots/scope/owner_id` |

`store_root` 与每个 `source_root` 必须是已存在的绝对目录，不能包含符号链接或目录联接，
也不能相同或互相嵌套。`store_root` 是图谱数据库位置；`source_roots` 是显式同步的原始
文档目录，两者不是同一个概念。扫描会忽略隐藏目录和任何 `kemo-graph-storage`。

远程服务只有在 `allow_remote=true` 时允许，并且非回环地址必须使用 HTTPS。kemo-graph
当前没有由本拓展管理的应用层凭据，跨主机部署仍必须由操作者提供外层鉴权、TLS 和审计。

## 操作层

### 本地操作

- `configuration_status` / `libraries`：读取注册表，不联网。
- `refresh`：只重建 `input_data.md` 注册表摘要，不联网。
- `scan`：计算已注册 `source_roots` 的新增、修改和缺失文件，不写 Store、不推进游标。

### 联网只读操作

- `status`：手动检查服务和选定库，写入最近状态快照。只有来源无 pending、活动文档无
  processing/failed 且 FAISS 健康时才是 `ready`；空库显示 `empty`，未初始化显示
  `not_initialized`。
- `query`：按 `library_ids` 查询，默认 `mode=hybrid`。一个 portable 库走单 Store，多个
  portable 库走 federated；内置库走 kemo-graph 默认查询端点。跨库部分失败会显式返回
  `partial=true` 和警告，不会静默伪装成全量成功。结果超过 14,000 字符时写成 artifact，
  避免撑爆主上下文。
- `documents`：列出或读取文档；更新和删除属于写操作，见下文确认规则。
- `jobs`：查看单个 portable Store 的维护任务。

### 显式写操作

- `activate`：保存完整 schema v2 注册表并激活拓展；不初始化、同步或构建。
- `initialize`：幂等初始化选定 portable Store；内置库由 kemo-graph 自身管理。
- `sync`：幂等初始化后，仅导入已注册 `source_roots` 中哈希发生变化的文档。默认
  `confirm_deletions=false`，不传播源文件删除，也不自动 ingest。
- `ingest`：每次必须选择一个 Library ID，`mode` 只允许 `graph/rag/both`。这是可能调用
  LLM、Embedding 和 Rerank 的长耗时、高成本操作，完成后应再次手动 `status`。
- `upload`：向一个库上传 Markdown，上传后保持待整理，不隐式 ingest。
- `import_file`：向一个库上传本地文件并转换导入。需要一个 Library ID 和经过管理员核对的
  绝对普通文件路径 `path`；拒绝符号链接、不支持的扩展名和超过 50 MB 的文件。支持 PDF、
  DOCX、PPTX、XLSX、EPUB、HTML、RTF、Markdown、文本及常见结构化文本格式。默认
  `ingest_after_import=false`，只有用户明确要求立即整理时才能设为 `true`。
- `documents update`：需要 `source_id + content`，可附带 `expected_content_hash` 做并发保护。
- `documents delete`：需要 `source_id + confirm="delete"`。
- `deactivate`：删除本地激活配置并停止 Prompt 注入；保留同步游标、状态快照和全部外部
  Store，绝不删除用户图谱数据。

## 推荐流程

查询：

```text
configuration_status → 用户明确选择 Library ID → status → query
```

更新注册来源：

```text
scan → 向用户展示新增/修改/缺失项 → 用户确认 → sync → ingest（逐库）→ status
```

上传单个本地文件：

```text
configuration_status → 用户明确选择 Library ID 和文件 → import_file → documents/status
```

`import_file` 与 `upload` 的区别：`upload` 直接提交已经存在的 Markdown 正文；
`import_file` 使用 multipart 传输原始文件，并由 kemo-graph 转换成规范 Markdown。

如果只有新增和修改，`sync` 仍不会自动构建；如果存在缺失文件，除非用户明确确认传播
删除，否则必须保持 `confirm_deletions=false`。

## 一致性与失败规则

- 本地游标位于 `data/library_sync_state.json`，按稳定 Library ID 和注册配置签名隔离。
  同一 ID 改绑 Store 或来源路径时不会复用旧文件游标。
- 单文件导入失败时不提交该文件的新哈希，下次手动 `sync` 会继续重试；成功文件可以独立
  提交，避免整批重复转换。
- 批量删除必须检查 HTTP 200 内的 `failed/documents/failures`，仅删除成功项从游标移除。
- ingest 必须检查 HTTP 200 内的 `result.failed/details`；`failed>0` 视为失败。
- `409 PROCESSING` 表示库仍在构建。先用 `status/jobs` 观察，不能盲目清库、改表或并发
  重复 ingest。
- `403 STORE_ACCESS_DENIED` 检查 kemo-graph 的 allowed roots、绝对路径和进程权限。
- `404 STORE_NOT_INITIALIZED` 对已注册 portable 库显式执行 `initialize` 后再重试。
- `422`、内容冲突和注册表错误不能自动重试，必须先修正参数或配置。

`data_update.py` 永远只读取本地注册表和最近一次状态快照，用于生成 Prompt 可见目录；它
不会联网、扫描文档、初始化 Store、推进同步游标或调用模型。
