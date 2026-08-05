# Kemo Graph 外挂文档站

> 最后核对：2026-08-05

Kemo Graph 在 kemo-agent 中的定位是“侧载超级文档站”，不是框架知识库、记忆系统或
System Prompt 的替代层。

框架内置两项互补资源：

- `global_expand/kemo_graph/`：注册外部文档库，执行状态、扫描、同步、构建和查询。
- `plugins/kemo_graph/`：只读取本地注册表并生成规范 `expand_call` 参数；自身不联网、
  不扫盘、不构建图谱。

所有真实操作统一进入：

```text
expand_call(scope="global", module="kemo_graph", command=<operation>, params={...})
```

## 与核心框架的边界

Kemo Graph 外挂必须满足以下不变量：

1. 不替换、不增强、不缩减用户、共享、全局知识库。
2. 不替换、不增强、不缩减永久记忆、临时重要记忆或临时三层记忆。
3. 不参与记忆提取、权重、晋升、过期、热画像或历史归档。
4. 不拥有专用 Prompt 段；激活后的目录摘要只按普通
   `[expand_data][global:kemo_graph]` 注入。
5. 不在每轮对话、工具循环或 Prompt 刷新时发网络请求。
6. 不注册 Cron、RuntimeHost 专用线程或其他自动同步/自动 ingest 链路。
7. 服务不可达、Store 构建中或查询失败时，只影响本次外挂操作，不影响正常对话。

框架级 `global_config.json` 和用户 `user_config.json` 不再包含 `kemo_graph` 开关。访问控制
完全复用普通模块权限：

- `expand.global_whitelist` 控制 `global:kemo_graph` 目录摘要和操控入口；
- `plugins.whitelist` 控制 `kemo_graph` 引导插件与 `expand_call` 执行工具。

空白名单仍表示全部允许；非空白名单必须显式包含对应资源名。

## schema v2 注册表

管理员配置位于：

```text
global_expand/kemo_graph/graph_config.json
```

示例：

```json
{
  "schema_version": 2,
  "base_url": "http://127.0.0.1:8000/api/v1",
  "admin_users": ["kesepain"],
  "allow_remote": false,
  "timeout_seconds": 15,
  "ingest_timeout_seconds": 1800,
  "libraries": [
    {
      "id": "project_docs",
      "kind": "portable",
      "display_name": "项目大型文档库",
      "enabled": true,
      "store_root": "E:\\kemo-graph-stores\\project-docs",
      "source_roots": ["E:\\documents\\project"],
      "scope": "knowledge.user",
      "owner_id": "kesepain",
      "allowed_users": ["kesepain"]
    },
    {
      "id": "kemo_graph_builtin",
      "kind": "service_default",
      "display_name": "Kemo Graph 内置文档库",
      "enabled": true,
      "allowed_users": ["kesepain"]
    }
  ]
}
```

### Library ID

- `id` 必须显式提供，匹配 `^[a-z][a-z0-9_-]{0,63}$`，并在注册表内唯一。
- 工具调用只传 `library_ids`，不能传绝对路径。
- 显示名称可以变化，但稳定 ID 不应随路径、名称或文档哈希临时重算。
- 同一 ID 改绑 Store 或来源路径时，本地状态通过注册配置签名识别变化，不复用旧文件游标。

### `service_default`

指向 kemo-graph 项目自身的默认文档库。上传、文档管理、查询和 ingest 使用默认 API；
Store 物理位置由 kemo-graph 自身配置管理。一个注册表最多声明一个默认库。

### `portable`

指向管理员选择的绝对 Store 位置：

- `store_root`：必填、已存在的绝对目录。kemo-graph 在其内部维护
  `kemo-graph-storage/`。
- `source_roots`：可选的原始文档绝对目录数组。为空时表示只通过上传接口管理。
- `scope`：kemo-graph Store 清单范围，默认 `knowledge.user`。
- `owner_id`：可选稳定所有者标识。
- `admin_users`：可执行激活、同步、构建、上传、修改和删除的框架用户。
- `allowed_users`：Library 读取 ACL；省略时仅管理员可见，`["*"]` 才表示公共库。

全局 Expand 白名单是第一层授权，Library ACL 是第二层授权。`owner_id` 只是 Store
元数据，不等于 kemo-agent 权限。全局 `input_data.md` 仅展示公共库；私有 Library ID
和绝对路径只能通过带调用用户上下文的 `kemo_graph libraries` 获取。

`store_root` 与 `source_roots` 必须分离，不能相同或互相嵌套；路径本身及父路径不能包含
符号链接或目录联接。这样可以避免扫描派生数据库、路径逃逸和 Store 自我导入。

## 本地目录摘要

`data_update.py` 只读取本地注册表和最近一次手动状态快照，生成 `input_data.md`。它不会：

- 访问 kemo-graph；
- 扫描 `source_roots`；
- 初始化 Store；
- 推进同步游标；
- 调用 LLM、Embedding 或 Rerank。

目录摘要包含 Library ID、名称、类型、绝对 Store/服务位置、文档来源和上次已知状态。
状态不是实时轮询结果。注册配置签名与快照不一致时显示“未检查”，避免同一 ID 改绑后
展示旧 Store 状态。

## 手动操作

| command | 网络/写入 | 说明 |
|---|---|---|
| `configuration_status` / `libraries` | 本地只读 | 查看激活状态和注册表 |
| `refresh` | 本地写摘要 | 重建 Prompt 可见目录，不联网 |
| `status` | 联网只读 | 手动检查服务和选定库，保存状态快照 |
| `initialize` | 联网写入 | 幂等创建 portable Store |
| `scan` | 本地扫盘 | 计算已注册来源的增删改，不写 Store |
| `sync` | 联网写入 | 导入哈希变化文件；默认不传播删除、不 ingest |
| `ingest` | 联网高成本 | 逐库构建 Graph/RAG |
| `query` | 联网只读 | graph/rag/hybrid/answer/global 查询 |
| `upload` | 联网写入 | 上传 Markdown，保持 pending |
| `documents` | 联网读写 | list/content/update/delete |
| `jobs` | 联网只读 | 查看 portable Store 维护任务 |
| `deactivate` | 本地写入 | 关闭激活配置，保留游标、状态和外部 Store |

只有用户明确要求查询、使用、更新或维护此外挂文档站时才允许执行。普通问答不自动查询；
“继续”“下一步”“重来”等短指令不构成新的查询授权。同一轮需要多个相关证据时应尽量
合并成一次 query。

## 增量扫描与同步

`scan` / `sync` 只处理管理员注册的 `source_roots`：

- 递归枚举 kemo-graph 支持的文档格式；
- 跳过隐藏目录、符号链接、目录联接和任何 `kemo-graph-storage`；
- 单文件上限 50 MiB；
- 使用 SHA-256 判断正文是否变化；
- 来源消失时只标记待确认，默认不传播删除。

游标位于 `data/library_sync_state.json`，只保存文件指纹、kemo-graph `source_id`、派生相对
路径、Store ID 和注册配置签名，不复制文档正文。

同步语义：

1. 每个 portable 库先幂等调用 `/stores/initialize`。
2. 新增或修改文件逐项调用 `/stores/import-path`，固定
   `ingest_after_import=false`。
3. 单文件失败不写入新哈希，下次手动同步仍会重试；成功文件可独立提交。
4. 删除只有在用户确认并传 `confirm_deletions=true` 时才调用批量删除。
5. HTTP 200 仍必须检查批量删除返回的 `failed/documents/failures`；只有成功删除的来源才从
   游标移除，失败项保留并继续报告。
6. `sync` 永远不自动调用 ingest。

推荐维护流程：

```text
scan → 展示新增/修改/缺失项 → 用户确认 → sync
     → ingest(library_ids=[单库]) → status → 下一库
```

## 就绪与查询

状态分类：

- `not_initialized`：Store 尚未建立；
- `empty`：已初始化但没有活动来源；
- `processing`：至少一篇活动文档正在构建；
- `pending`：Graph/RAG 仍有待构建项；
- `degraded`：活动文档失败或有来源时 FAISS 不健康；
- `ready`：没有 pending/processing/failed，且 FAISS 健康；
- `error`：服务或该库请求失败。

普通检索优先 `hybrid`，让 kemo-agent 主模型根据 Graph 与 RAG 证据组织答案。只有用户
明确要求 kemo-graph 自行生成回答时才使用 `answer`；`global` 要求服务端已经生成群组总结。

多个 portable 库使用 federated 查询。HTTP 200 只代表联合调度成功；若返回
`stores_failed`，拓展会明确标记 `partial=true` 并保留警告。查询结果超过 14,000 字符时
写入模块 artifact，再由 Expand 运行时安全发布到当前用户下载空间。

## 安全与失败边界

- 默认只允许 localhost/回环地址。非回环地址必须 `allow_remote=true` 且使用 HTTPS。
- HTTP 客户端拒绝重定向，响应体上限 16 MiB，不把绝对 Store 路径放进 URL。
- 对话输入不能直接指定 `store_root` 或 `source_root`；只能选择注册的 Library ID。
- `documents delete` 必须携带 `confirm="delete"`；来源批量删除默认关闭。
- ingest 必须检查 HTTP 200 内的 `result.failed/details`，`failed>0` 仍是失败。
- `409 PROCESSING` 先通过 `status/jobs` 观察，不能盲目清库、改表或并发重试。
- `403 STORE_ACCESS_DENIED` 检查 kemo-graph allowed roots、绝对路径和进程权限。
- `422`、内容冲突和注册表错误必须先修正，不能自动重试。
- kemo-agent 更新器保留历史部署中知识目录内的 `kemo-graph-storage/`，并清除旧自动维护
  脚本与旧派生状态；任意外部绝对路径 Store 本来就不在更新器覆盖范围内。
- Web 三层知识库枚举和 CRUD 始终屏蔽 `kemo-graph-storage/`，避免把派生数据误当权威知识。

停用或更新 kemo-agent 都不会删除外部 Store。真正删除 Store、全量重建、节点/关系级删除
仍属于 kemo-graph 项目的独立运维边界，不由本外挂隐式执行。
