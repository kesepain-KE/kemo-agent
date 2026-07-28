# 模块模板统一验收基准

本目录用于验收智能体创建的子代理、拓展、外部消息路由、感知、技能和用户包。
它不规定模块内部怎么组织：单文件、任意嵌套包、嵌入式工程、浏览器工程、API
客户端、第三方源码或完整开源项目都可以存在。验收器只检查 kemo-agent 必须依赖的
发现、入口、出口、生命周期、数据格式和跨模块通信合同。

`task_cron` 与 `task_plan` 不在本基准范围内；用户模板只检查这两个目录已经初始化，
不测试其业务逻辑。

## 快速使用

优先直接调用对应类型目录。比如创建了拓展：

```powershell
python -m tests.template_tests.expand --target users/<user>/expand/<name>
```

六个独立入口分别是：

- `python -m tests.template_tests.agent --target <path>`
- `python -m tests.template_tests.expand --target <path>`
- `python -m tests.template_tests.message --target <path>`
- `python -m tests.template_tests.sense --target <path>`
- `python -m tests.template_tests.skills --target <path>`
- `python -m tests.template_tests.user --target <path>`

需要批处理或类型未知时，才使用薄分发入口：

```powershell
python -m tests.template_tests --kind auto --target <path>
```

常用选项：

- `--format text|json`：控制标准输出格式；
- `--report <path>`：同时落盘报告；
- `--timeout <seconds>`：限制每个隔离子进程；
- `--static-only`：只检查和导入入口，不调用候选采集器、操控器或 executor；
- `--template-mode`：验收仓库内参考模板，允许待替换占位符，并把未安装的可选外部
  SDK 记为 `SKIP`。

退出码为 `0` 表示没有 `FAIL`。`SKIP` 表示需要外部凭据、设备、网络或可选 SDK
才能完成的检查，因此报告的 `ok` 可以为 `true`，但 `complete` 为 `false`。

## 执行边界

默认动态模式会先把候选目录复制进临时项目根，再通过框架真实发现器和有超时的
子进程检查入口/出口。原候选文件不会被更新脚本改写。`message` 验收永远不会自动
启动平台、发送消息或执行在线健康检测；它只导入生命周期入口并用合成缓冲消息验证
平台无关协议。

候选代码本身仍可能访问网络、硬件或绝对路径。来源不可信或只想先看静态合同的时候，
应使用 `--static-only`。需要真实平台联调时，再在明确准备好测试凭据和测试设备的环境
单独执行模块自己的集成测试。

## 报告状态

- `PASS`：该合同已真实验证；
- `FAIL`：框架基本合同不成立，模块大概率无法工作；
- `WARN`：合同成立，但存在部署或维护风险；
- `SKIP`：缺少外部条件或调用被显式关闭，不能声称已经验证。

## 目录边界

`agent/`、`expand/`、`message/`、`sense/`、`skills/`、`user/` 都是独立验收包，
各自拥有 `validator.py`、`STANDARD.md`、`__main__.py` 和本类回归测试。根目录只有报告、
临时沙箱、类型识别和薄分发等跨类型基础设施，不集中承载六类业务判断。

```text
template_tests/
├─ agent/    ─ validator + probes + standard + tests
├─ expand/   ─ validator + standard + tests
├─ message/  ─ validator + standard + tests
├─ sense/    ─ validator + standard + tests
├─ skills/   ─ validator + standard + tests
└─ user/     ─ validator + standard + tests
```

新增内部文件或改变模块内部架构不需要修改验收器；只有对应类型的框架公开入口/出口协议
变化时，才更新该类型目录，避免形成新的测试“上帝模块”。
