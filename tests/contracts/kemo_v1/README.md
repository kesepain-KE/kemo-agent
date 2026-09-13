# Kemo 1.0 共享兼容 Fixture

本目录是网关与 kemo-agent 共同执行的线协议回归基准。两边的 `fixtures/manifest.json` 和
`fixtures/wire.json` 必须逐字节一致；测试实现可以分别适配生产者和消费者，但不能各自维护
另一套请求、响应或 SSE 样例。仓库根目录的 `.gitattributes` 强制 `fixtures/*.json` 以 LF 检出，
避免 Windows 上 `core.autocrlf` 改写换行导致固定摘要失配；镜像此契约的仓库需要同样的规则。

覆盖边界：文本、动态推理档位、多轮工具结果、多模态内容、能力声明、模型目录、Asset、
Embedding、Rerank、终态响应、Usage 和 SSE 顺序/去重。全部样例均为离线假数据，不读取配置、
不访问上游、不包含真实凭据。

本目录锁定的是双方都必须理解的线路对象，不要求两个项目的内部类型完全相同。网关是生产者，
kemo-agent 是消费者；Agent 内部的 `run_id`、`run_sequence` 等运行字段不能反向变成网关必填字段。
消费者可以保留对同一主版本新增字段的宽容解析，但当前发出的请求、Header、共享 Fixture 和网关
路由仍严格使用 `1.0`。时间字段允许协议明确声明的 `null`，ID、Token 计量、校验和与 SSE 顺序继续
严格拒绝非法值。

两个仓库使用相同入口：

```sh
python -m tests.contracts.kemo_v1 -q
```

同时检出两个仓库时，可以显式核对镜像：

```powershell
python -m tests.contracts.kemo_v1 --peer-root E:\code\kemo-agent -q
```

修改规则：先修改同一份 `wire.json`，同步复制到另一仓库，再更新两边 `manifest.json` 的
SHA-256 与计数，并同步修改两个 `fixture_loader.py` 的 `EXPECTED_WIRE_SHA256`。任一侧模型无法接受
有效 Fixture，或接受了明确无效 Fixture，都属于 Kemo 1.0 兼容回归；不能通过删除用例、放宽测试
或仅修改一侧 Fixture 解决。
