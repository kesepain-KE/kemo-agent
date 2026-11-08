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
4. **控制超时与大小**：未显式提供时，`timeout` 默认来自 `global_config.yaml → tools.timeout`；显式有效值会同时覆盖网络请求期限和框架外层看门狗。`max_chars` 仅供 `read` 使用，默认 20000、上限 100000；`max_bytes` 供其他 action 使用，默认 2MB、上限 10MB。网络响应若连同元数据仍超过统一 100,000 字符回传上限，框架会返回受控超限错误。
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
