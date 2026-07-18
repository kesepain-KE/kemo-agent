# network

HTTP 网络请求与网页正文读取。支持 GET/POST 原始请求和网页正文提取。
无网络范围限制。

## Tool

```json
{
  "name": "network",
  "description": "网络请求与网页读取 — http_get/http_post 原始请求，web_read 网页正文读取。通过 action 参数选择操作。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["get", "post", "read"],
        "description": "操作: get=HTTP GET, post=HTTP POST, read=网页正文"
      },
      "url": {"type": "string", "description": "请求 URL"},
      "body": {"type": "string", "description": "POST 请求体"},
      "headers": {"type": "object", "description": "请求头对象，键和值均为字符串"},
      "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "请求超时秒数；默认读取 context.tool_timeout"},
      "strategy": {"type": "string", "enum": ["auto", "direct", "reader"], "description": "web_read 策略: auto=先 direct 后 reader, direct=本地解析, reader=第三方服务"},
      "reader_service": {"type": "string", "enum": ["auto", "jina", "markdown_new", "defuddle"], "description": "web_read 第三方 reader 服务"},
      "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "description": "返回正文最大字符数，默认 20000"}
    },
    "required": ["action", "url"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
