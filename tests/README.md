# 正式测试套件

`tests/` 随源代码上传，是每次发布必须通过的发布红线。测试按业务领域组织，新增断言应放入
对应领域，避免把无关系统行为堆入单一“上帝测试文件”。

## 目录职责

- `contracts/`：稳定公共 API、Schema、目录布局和兼容性合同。
- `runtime/`：请求生命周期、状态机、并发、取消、恢复和错误路径。
- `storage/`：SQLite、文件持久化、迁移、事务和幂等行为。
- `support/`：跨多个测试领域复用的夹具和测试辅助代码，不承载独立断言。
- `tests/template_tests/`：插件、子代理、拓展、消息、感知和技能六类模板的独立合同。

现有领域可渐进采用上述子目录，不为目录形式重复迁移仍然清晰且稳定的测试。

## 与开发期系统验收的边界

`开发临时目录/test_kemo/` 是被 `.gitignore` 排除的本机系统/集成补强套件，不进入发布包，
也不替代本目录。需要真实 LLM、外部服务、设备、部署实例或本地临时环境的验收只放在那里；
可以由薄编排脚本统一运行，但不得复制维护 `tests/` 已经覆盖的同一断言。

## 运行

```powershell
python -m pytest tests -q
python -m tests.contracts.kemo_v1 -q
python -m pytest tests/template_tests -q
```

发布前还应运行项目的完整 `开发临时目录/release_check.py`；该脚本只负责编排，不承载业务断言。

## Kemo 1.0 共享契约

`tests/contracts/kemo_v1` 是 kemo-agent 与 kemo-adapter-api 共用的离线线协议基准。两个仓库镜像
相同的 `fixtures/manifest.json` 和 `fixtures/wire.json`，但分别调用自己的生产协议模型、序列化器、
SSE 解析器和顺序守卫；不从另一仓库导入代码，也不访问真实网关或 Provider。

只验证当前 Agent：

```powershell
python -m tests.contracts.kemo_v1 -q
```

同时检出网关时，再核对镜像文件：

```powershell
python -m tests.contracts.kemo_v1 --peer-root E:\code\kemo-adapter-api -q
```

路径应替换为本机实际位置。修改 Kemo 请求、响应、能力声明、Asset、工具、多模态、Usage、
Embedding、Rerank 或 SSE 时，必须同步两边 Fixture、清单摘要和固定摘要；不能靠删除用例、放宽
Schema 或只修改一端让测试变绿。
