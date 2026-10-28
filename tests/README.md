# 正式测试套件

`tests/` 随源代码上传，是每次发布必须通过的发布红线。测试按业务领域组织，新增断言应放入
对应领域，避免把无关系统行为堆入单一“上帝测试文件”。

当前源码定档基线为 **1.5.2（2026-10-26）**，所有主框架版本仍以根目录 `version.json` 为准。
本次重点回归包括空间公平调度/共享 API 池、子代理完成屏障与超时续跑、Chat 自动兼容、
原生媒体装载、历史归档与标记、迁移备份及网页工作面板；测试中的旧版升级输入不是待替换的当前版本。
`contracts/test_release_versions.py` 用隔离副本验证版本门禁能拒绝遗漏的版本入口；
`deploy/test_documentation.py` 继续保证中英文安装命令、最新更新说明及发布前提一致。

## 目录职责

- `contracts/`：稳定公共 API、Schema、目录布局和兼容性合同。
- `runtime/`：请求生命周期、状态机、并发、取消、恢复和错误路径。
- `storage/`：SQLite、文件持久化、迁移、事务和幂等行为。
- `deploy/`：独立部署器、Release 归档、四渠道命令、故障恢复和发布文档合同。
- `support/`：跨多个测试领域复用的夹具和测试辅助代码，不承载独立断言。
- `tests/template_tests/`：插件、子代理、拓展、消息、感知和技能六类模板的独立合同。

现有领域可渐进采用上述子目录，不为目录形式重复迁移仍然清晰且稳定的测试。

正式前端测试位于 `web/frontend/src/` 的 `*.test.ts` / `*.test.tsx`，通过 Vitest 运行。
长引导与任务计划覆盖完整文本保留、折叠恢复、取消／重试／批准操作，以及当前生产样式的
限高和滚动合同；源码断言应检查 `reference/styles/production.css`，不使用停用的旧样式代替。
实际滚动和视口内可操作性仍需浏览器验收，不能由 jsdom 的布局数据推断。

## 与开发期系统验收的边界

1.5.2 统一整体上下文压缩专项（2026-10-28）：

- `core/test_unified_compression.py` 覆盖手动/轮数/Token/API 统一轮数优先、Token 后备、
  API 最终响应实际输入量与跨轮失败恢复、基线隔离/失效及不误用累计成本。
- 摘要专项覆盖完整预算、超大单轮无丢字分片、已有摘要预算、输入/413 自适应缩小、
  片段中途失败保留旧摘要；所有测试使用临时用户与假 Provider。
- 进一步覆盖主请求 413 的字节下降验证、两次恢复不被外层重试放大、摘要模型实际容量
  与格式修复的 native 输入闸门、取消/宿主中断、已完成请求计费及摘要落盘失败分类。

1.5.2 页面租约与子代理默认并行专项（2026-10-27）：

- `history/test_ordered_presence.py` 验证刷新释放、真实多窗口、跨进程乱序、
  BFCache 再激活、旧客户端保守兼容、Web/App 来源隔离及 history 7→8 备份/回滚。
- `agents/test_dispatch_defaults.py` 使用真实主工具循环、调度器和隔离假 Provider：
  同批五次缺省调用在首项完成前全部登记，容量三时真实重叠，不提前越过完成屏障；
  同时验证显式同步、特殊入口缺省、resume 和 list.default_wait。
- 前端 `sessionClient`、API、AppShell、子代理卡片及 i18n 测试覆盖每文档身份、
  有序 keepalive、页面恢复、关闭失败保留草稿、等待提交/排队区分和九语言完整性。
- 所有存储验收用临时用户，不初始化真实源码或部署用户库，不访问真实模型；
  最终必须构建 reference 生产入口并运行 UI 防回退检查。

1.5.2 预览态修复专项：

- `core/test_preview_runtime_safety.py` 以真实工具执行器和临时用户覆盖四类宿主异常、
  主/子代理出口、后台终态与 wait、压缩回滚、动态查询刷新及成功副作用防重。
- `core/test_runtime_import_boundaries.py` 每次启动独立解释器，检查 CLI/engine/
  conversation/history 及历史子模块的单独导入和正逆序导入，不依据复用解释器下的缓存。
- `provider/test_chat_profiles.py` 覆盖自动基础兼容及未知媒体/高级 reasoning 在 HTTP 前拒绝。
- 前端 `api/transport.test.ts`、`hooks/useSpeechRecognition.test.ts`、
  `utils/sessionDisplayName.test.ts` 覆盖真实请求错误边界、识别实例回收与共享命名；
  同时回归真实历史抽屉、只读归档和输入框。不通过测试删除生产 UI 或放宽断言。

1.5.2 子代理分段预算专项位于 `tests/agents/test_subagent_budgets.py`：
假模型/临时用户验证预算覆盖、不限次数仍受时长限制、额度停止保留用量与结论、
同任务多段续跑、内部重试不重置计数、软预警、guide/split 作用域与关联。
Shell 接管与跨 root 的 required 屏障继续在 `tests/core/test_shell_completion_notifications.py`
维护；前端预算停止及九语言合同在现有工作面板和 i18n 测试维护，不复制旧 UI。

`开发临时目录/test_kemo/` 是被 `.gitignore` 排除的本机系统/集成补强套件，不进入发布包，
也不替代本目录。需要真实 LLM、外部服务、设备、部署实例或本地临时环境的验收只放在那里；
可以由薄编排脚本统一运行，但不得复制维护 `tests/` 已经覆盖的同一断言。

## 运行

```powershell
python -m pytest tests -q
python -m pytest tests/deploy -q
python -m pytest tests/contracts/test_release_versions.py -q
python -m tests.contracts.kemo_v2 -q
python -m pytest tests/template_tests -q
npm --prefix web/frontend test -- --maxWorkers=2
```

发布前还应运行项目的完整 `开发临时目录/release_check.py`；该脚本只负责编排，不承载业务断言。
1.5.2 定档使用 `--timeout 3600 --report tmp/release-check-1.5.2.json`；
报告属于本机源码验收，不表示部署预览版、远程渠道或真实厂商模型已经升级。
开发期分发验收会进一步检查 Release 内由前端入口引用的 CSS，确认滚动修复实际进入发布包。

## Kemo 2.0 固定协议制品

`tests/contracts/kemo_v2` 验证 Agent 权威协议模型、Schema、规则清单和最小 Fixture。Gateway
不复制一套 Python 模型，而是加载 Agent 构建的 `vendor/kemo_protocol-2.0.zip`，通过其
`tests/contracts/kemo_v2/test_artifact_contract.py` 校验 lock、Schema 和 Fixture 摘要。
当前只支持精确 `protocol_version="2.0"`，不提供 1.x shim。

Agent：

```powershell
python -m pytest tests/contracts/kemo_v2 -q
python -m provider.protocol.spec.build_artifact E:\code\kemo-adapter-api\vendor
```

Gateway：

```powershell
python -m pytest tests/contracts/kemo_v2 -q
python -m tests --suite kemo-contract -q
```

协议模型、SSE、Asset、工具、多模态、Usage、Embedding、Rerank 或能力声明发生变化时，必须先
重建固定制品并让双仓摘要一致；禁止只改一侧、降低校验或恢复旧协议测试作为默认门禁。
