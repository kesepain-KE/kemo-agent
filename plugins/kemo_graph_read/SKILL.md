# kemo_graph_read

按需生成 global:kemo_graph 的操作参数。仅可生成读取与状态查询操作，不会生成写入、删除或维护指令。

## 使用原则

- 仅在用户明确要求操作 Kemo Graph 文档站时调用。
- 只使用注册表中可见的 Library ID，不传裸 Store 路径。
- 普通检索优先使用 `query`；数据更新按 `scan → 用户确认 → sync → ingest → status` 执行。
- 网关不可用不影响本地知识库与对话。

## Tool

```json
{
  "name": "kemo_graph_read",
  "description": "Kemo Graph 只读工具：生成 global:kemo_graph 的受限操作参数。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["overview", "libraries", "configuration_status", "operation_guide"], "description": "读取注册表信息或生成只读操作参数"},
      "operation": {
        "type": "string",
        "enum": [
          "configuration_status",
          "libraries",
          "refresh",
          "status",
          "scan",
          "query",
          "documents",
          "jobs",
          "graph",
          "entities",
          "cache",
          "logs",
          "config",
          "update_status",
          "projects"
        ],
        "description": "仅允许该工具权限等级对应的操作"
      },
      "library_ids": {
        "type": "array",
        "items": {"type": "string"},
        "maxItems": 100
      },
      "query": {"type": "string"},
      "mode": {"type": "string", "enum": ["graph", "rag", "hybrid", "answer", "global", "both"]},
      "document_action": {"type": "string", "enum": ["list", "content"]},
      "source_id": {"type": "string"},
      "job_id": {"type": "string"},
      "limit": {"type": "integer", "minimum": 1, "maximum": 10000},
      "graph_action": {
        "type": "string",
        "enum": ["full", "visualization_meta", "visualization_nodes", "visualization_edges", "neighborhood"]
      },
      "entity_kind": {"type": "string", "enum": ["node", "relation"]},
      "entity_action": {"type": "string", "enum": ["get"]},
      "cache_action": {"type": "string", "enum": ["list", "show"]},
      "log_category": {"type": "string", "enum": ["internal", "query", "terminal"]},
      "log_date": {"type": "string", "description": "YYYY-MM-DD"},
      "node_id": {"type": "string", "maxLength": 255},
      "edge_id": {"type": "string", "maxLength": 255},
      "cache_key": {"type": "string"},
      "page": {"type": "integer", "minimum": 1},
      "page_size": {"type": "integer", "minimum": 1, "maximum": 10000},
      "expected_revision": {"type": "string", "minLength": 64, "maxLength": 64},
      "depth": {"type": "integer", "minimum": 1, "maximum": 10},
      "direction": {"type": "string", "enum": ["forward", "backward", "both"]},
      "edge_limit": {"type": "integer", "minimum": 1, "maximum": 50000},
      "nodes_page": {"type": "integer", "minimum": 1},
      "nodes_page_size": {"type": "integer", "minimum": 1, "maximum": 1000},
      "project": {"type": "string", "maxLength": 160},
      "search": {"type": "string", "maxLength": 200},
      "graph_status": {"type": "string", "maxLength": 20},
      "rag_status": {"type": "string", "maxLength": 20},
      "include_summary": {"type": "boolean"},
      "project_action": {"type": "string", "enum": ["list"]}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "permission": "read"
}
```
