# 外部消息路由验收标准

```powershell
python -m tests.template_tests.message --target message/out/<platform>
```

## 必须成立

- `message.json`、`state.json`、平台能力和绑定用户符合统一消息合同；
- 输入端提供 `start(config, buffer_path, files_path, state_path)` 与 `stop()`；
- 输出端提供 `send(payload) -> True`，检测端提供 `check(config, state) -> dict`；
- `message.md` 初始为空或已经是可消费的 YAML front matter 消息序列；
- 合成入站消息可转换为框架统一消息对象，Transport 可被发现。

## 安全测试边界

通用验收不会启动长轮询、消费真实平台消息、发送外部消息或调用在线检测。平台 SDK、Token、
Webhook、长连接和真实回复应在模块自己的集成环境验证。平台内部可以使用任意包结构、守护
线程、队列或完整第三方工程，只保留三个公开薄入口即可。
