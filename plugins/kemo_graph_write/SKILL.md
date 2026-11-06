# kemo_graph_write

按需生成 global:kemo_graph 的操作参数。仅可生成写入操作；删除、清理和维护仍须满足用户确认及管理员权限。

## 使用原则

- 仅在用户明确要求操作 Kemo Graph 文档站时调用。
- 只使用注册表中可见的 Library ID，不传裸 Store 路径。
- 普通检索优先使用 `query`；数据更新按 `scan → 用户确认 → sync → ingest → status` 执行。
- 网关不可用不影响本地知识库与对话。

## Tool

```json
{
  "name": "kemo_graph_write",
  "description": "Kemo Graph 写入工具：生成 global:kemo_graph 的受限操作参数。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["operation_guide"], "description": "生成 global:kemo_graph 的受限操作参数"},
      "operation": {
        "type": "string",
        "enum": [
          "activate",
          "initialize",
          "sync",
          "ingest",
          "upload",
          "import_file",
          "documents",
          "entities",
          "cache",
          "maintenance",
          "projects",
          "deactivate"
        ],
        "description": "仅允许该工具权限等级对应的操作"
      },
      "library_ids": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 100
      },
      "paths": {
        "type": "array",
        "items": {"type": "string", "minLength": 1, "maxLength": 4096},
        "minItems": 1,
        "maxItems": 1000,
        "description": "仅用于 ingest：精确整理或重试的 Markdown 路径；省略时处理普通待处理项"
      },
      "filename": {"type": "string"},
      "content": {"type": "string"},
      "path": {"type": "string"},
      "ingest_after_import": {"type": "boolean"},
      "document_action": {"type": "string", "enum": ["update", "delete", "move"]},
      "source_id": {"type": "string"},
      "source_ids": {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "minItems": 1,
        "maxItems": 1000
      },
      "expected_content_hash": {"type": "string"},
      "expected_relative_path": {"type": "string"},
      "confirm_deletions": {"type": "boolean"},
      "entity_kind": {"type": "string", "enum": ["node", "relation"]},
      "entity_action": {"type": "string", "enum": ["delete"]},
      "cache_action": {"type": "string", "enum": ["clear"]},
      "maintenance_action": {
        "type": "string",
        "enum": ["summarize", "organize_graph", "organize-graph", "cleanup_recycle", "cleanup-recycle"]
      },
      "cache_key": {"type": "string"},
      "stale_only": {"type": "boolean"},
      "page": {"type": "integer", "minimum": 1},
      "page_size": {"type": "integer", "minimum": 1, "maximum": 10000},
      "expected_revision": {"type": "string", "minLength": 64, "maxLength": 64},
      "use_llm": {"type": "boolean"},
      "summarize": {"type": "boolean"},
      "force": {"type": "boolean"},
      "project": {"type": "string", "maxLength": 160},
      "project_action": {"type": "string", "enum": ["create"]},
      "name": {"type": "string", "minLength": 1, "maxLength": 160}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "permission": "write"
}
```
