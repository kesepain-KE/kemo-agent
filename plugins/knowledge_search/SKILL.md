# knowledge_search
在当前授权范围内检索用户、共享和全局知识正文，返回匹配文档的短片段。

## Tool

```json
{
  "name": "knowledge_search",
  "description": "在当前授权范围内检索知识正文。",
  "input_schema": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "检索文本"},
      "scopes": {
        "type": "array",
        "items": {"type": "string"},
        "description": "可选范围：user、shared、global"
      },
      "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}
    },
    "required": ["query"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
