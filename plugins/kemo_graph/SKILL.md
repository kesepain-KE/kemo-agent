# kemo_graph

Kemo Graph 外挂文档站引导插件。它只读取本地注册表并生成规范 `expand_call` 参数；插件自身不联网、不扫描目录、不构建图谱。真正操作由 `global:kemo_graph` 全局拓展执行。

插件按当前调用用户过滤 Library：模块白名单是第一层授权，`allowed_users` 是第二层
读取 ACL，写操作仅 `admin_users` 可生成。私有库不会进入全局 Prompt，`owner_id`
也不代表框架授权。

## 使用原则

1. 知识图谱不替换、不增强、也不缩减本地知识库、永久记忆、临时重要记忆或临时三层记忆。
2. 第一次使用先调用 `configuration_status` 或 `libraries` 查看管理员注册的 Library ID。
3. Store 选择只允许使用注册表中的 `library_id`，不得传裸 `store_root`。只有管理员明确要求 `import_file` 时，才允许提交一个已经核对的本地文件绝对路径。
4. 普通问答默认不查询图谱；只有用户明确要求查询、使用或核对此外挂文档站时才调用。
5. 同一轮默认合并为一次 `query`；“继续、下一步、重来”等短指令不得自行触发新查询。
6. 普通检索优先 `hybrid`，让主智能体根据 Graph 与 RAG 证据回答。只有用户明确要求 kemo-graph 自行生成回答时才使用 `answer`。
7. 更新流程固定为 `scan → 用户确认 → sync → ingest → status`。`sync` 不自动 ingest，删除默认不传播。
8. `ingest` 是长耗时、高成本操作，每次只能选择一个 Library ID。
9. `upload` 只上传 Markdown 正文；`import_file` 上传 PDF、Office、EPUB 等受支持文件。两者默认只产生待整理文档，不隐式 ingest。
10. kemo-graph 或网关不可用只会令外挂操作失败，不影响主智能体本地知识、记忆和正常对话。

## Action

| action | 用途 |
|---|---|
| `overview` | 返回外挂定位、边界和推荐流程 |
| `libraries` | 读取本地注册表，不联网 |
| `configuration_status` | 查看拓展是否激活及注册配置，不联网 |
| `operation_guide` | 生成规范的 `expand_call` 参数 |

`operation_guide.operation` 支持：`activate/configuration_status/libraries/refresh/status/initialize/scan/sync/ingest/query/upload/import_file/documents/jobs/deactivate`。

## Tool

```json
{
  "name": "kemo_graph",
  "description": "解释 Kemo Graph 外挂文档站的注册表与安全边界，并生成 global:kemo_graph 的规范按需调用参数。插件自身不访问图谱服务。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["overview", "libraries", "configuration_status", "operation_guide"]
      },
      "operation": {
        "type": "string",
        "enum": ["activate", "configuration_status", "libraries", "refresh", "status", "initialize", "scan", "sync", "ingest", "query", "upload", "import_file", "documents", "jobs", "deactivate"]
      },
      "library_ids": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 100
      },
      "query": {"type": "string"},
      "mode": {
        "type": "string",
        "enum": ["graph", "rag", "hybrid", "answer", "global", "both"]
      },
      "filename": {"type": "string"},
      "content": {"type": "string"},
      "path": {"type": "string"},
      "ingest_after_import": {"type": "boolean"},
      "document_action": {
        "type": "string",
        "enum": ["list", "content", "update", "delete"]
      },
      "source_id": {"type": "string"},
      "expected_content_hash": {"type": "string"},
      "confirm_deletions": {"type": "boolean"},
      "job_id": {"type": "string"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 1000}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "2.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
