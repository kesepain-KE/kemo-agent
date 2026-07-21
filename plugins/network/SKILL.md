# network

HTTP 网络请求与网页正文读取。支持五种 REST 请求方法和三种网页提取策略。无网络范围限制。

## 使用原则

1. **选对 action**：
   - 查 API、获取原始响应 → `get`
   - 提交数据、创建资源 → `post`
   - 全量替换资源 → `put`
   - 部分更新资源 → `patch`
   - 删除资源 → `delete`
   - 提取网页正文、阅读文章 → `read`（不是 `get`）
2. **选择 read 策略**：一般网页使用默认 `auto`，先本地解析，失败或正文为空时回退 reader；只允许本地解析时使用 `direct`；明确使用第三方服务时使用 `reader`。
3. **选择 reader 服务**：`jina` 最稳定且通用，是 `auto` 的默认服务；`markdown_new` 输出干净 Markdown，适合存档；`defuddle` 擅长去广告和干扰内容。
4. **控制超时与大小**：`timeout` 默认来自 `global_config.json → tools.timeout`；`max_chars` 仅供 `read` 使用，默认 20000、上限 100000；`max_bytes` 供其他 action 使用，默认 2MB、上限 10MB。
5. **处理错误**：失败时读取 `error` 字段，不盲目重复请求。HTTP 错误会返回状态原因，连接错误会返回连接失败原因。

## 输出字段

| 字段 | 适用 action | 说明 |
|------|-------------|------|
| `ok` | 全部 | 操作是否成功 |
| `error` | 全部 | 失败原因，仅 `ok=false` 时出现 |
| `status` | get/post/put/delete/patch | HTTP 状态码；连接失败时为 0 |
| `url` | 全部 | 请求 URL |
| `body` | get/post/put/delete/patch | 响应体；JSON 自动解析为对象，否则为字符串 |
| `text` | read | 提取的网页正文 |
| `chars` | read | 正文字符数 |
| `source` | read | direct 或 reader:jina / reader:markdown_new / reader:defuddle |
| `content_type` | 全部 | 响应 Content-Type；未收到响应时为空字符串 |
| `truncated` | 全部 | 是否因大小限制被截断 |

## Tool

```json
{
  "name": "network",
  "description": "HTTP 网络请求与网页正文读取。get/post/put/delete/patch 收发 REST 请求，read 通过 direct 本地解析或 jina/markdown_new/defuddle reader 提取正文。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["get", "post", "put", "delete", "patch", "read"],
        "description": "操作：get=GET、post=POST、put=PUT 全量更新、delete=DELETE、patch=PATCH 局部更新、read=网页正文提取"
      },
      "url": {"type": "string", "description": "请求 URL，仅支持 http/https"},
      "body": {"type": "string", "description": "POST/PUT/PATCH 请求体"},
      "headers": {"type": "object", "description": "请求头对象，键和值均为字符串"},
      "timeout": {"type": "integer", "minimum": 1, "maximum": 3600, "description": "请求超时秒数；默认来自 global_config.json → tools.timeout"},
      "strategy": {"type": "string", "enum": ["auto", "direct", "reader"], "description": "read 策略：auto=先 direct 后 reader、direct=仅本地解析、reader=仅第三方服务"},
      "reader_service": {"type": "string", "enum": ["auto", "jina", "markdown_new", "defuddle"], "description": "read reader：auto=jina、jina=稳定通用、markdown_new=干净 Markdown、defuddle=去广告"},
      "max_chars": {"type": "integer", "minimum": 1000, "maximum": 100000, "description": "read 返回正文最大字符数，默认 20000"},
      "max_bytes": {"type": "integer", "minimum": 1000, "maximum": 10000000, "description": "get/post/put/delete/patch 响应最大字节数，默认 2000000（2MB）"}
    },
    "required": ["action", "url"],
    "additionalProperties": false
  },
  "version": "1.1.0",
  "enabled": true,
  "entrypoint": "tool.py:run"
}
```
