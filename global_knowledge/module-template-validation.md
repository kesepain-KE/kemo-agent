# 模块创建后的独立验收基准

`tests/template_tests/` 是智能体创建模块后的统一入口协议验收区。它解决的问题不是判断模块内部工程“长得像不像模板”，而是验证智能体创建的模块能否被 kemo-agent 真实发现、加载、调用并产出符合合同的结果。

基准覆盖六类资源：子代理、拓展、外部消息路由、感知、技能和用户包。`task_cron` 与 `task_plan` 不在本批业务基准内；用户包验收只确认它们的初始化目录存在。

## 核心原则

1. 创建什么类型，就调用该类型文件夹里的测试标准和验证器。
2. 只验证框架依赖的发现、入口、出口、生命周期、权限和通信合同。
3. 不限制内部文件数量、目录层级、类名、实现语言边界内的辅助工程或第三方源码。
4. 每类业务规则留在自己的目录，公共层不得成为集中处理所有类型的“上帝模块”。
5. 通用合同验收和真实外部集成测试是两层测试，不能互相冒充。

模块可以小到单文件，也可以在目录内承载完整开源项目、嵌入式工程、浏览器自动化工程、API 客户端、数据库、媒体处理管线或任意嵌套包。大型工程只需在框架声明位置提供薄适配入口；额外内部文件不会因未出现在模板中而被拒绝，也不会因此自动注册、注入 Prompt 或执行。

## 类型与入口映射

在项目根目录执行：

| 创建或修改的资源 | 独立标准 | 验收命令 |
|------------------|----------|----------|
| 子代理 | `tests/template_tests/agent/STANDARD.md` | `python -m tests.template_tests.agent --target users/<user>/agents/<name>` |
| 拓展 | `tests/template_tests/expand/STANDARD.md` | `python -m tests.template_tests.expand --target users/<user>/expand/<name>` |
| 外部消息路由 | `tests/template_tests/message/STANDARD.md` | `python -m tests.template_tests.message --target message/out/<platform>` |
| 感知 | `tests/template_tests/sense/STANDARD.md` | `python -m tests.template_tests.sense --target global_sense/<name>` |
| 技能 | `tests/template_tests/skills/STANDARD.md` | `python -m tests.template_tests.skills --target users/<user>/user_skills/<scope>` |
| 用户包 | `tests/template_tests/user/STANDARD.md` | `python -m tests.template_tests.user --target users/<name>` |

全局、共享或用户作用域的实际路径不同时，只替换 `--target`，不要改变验收类型。例如全局拓展仍调用 `tests.template_tests.expand`，共享技能仍调用 `tests.template_tests.skills`。

候选类型确实未知或外部程序需要统一入口时，可以使用：

```powershell
python -m tests.template_tests --kind auto --target <path>
```

根级入口只根据清单标记识别类型，再延迟导入对应目录的验证器。已知类型时优先直接调用独立入口，使执行意图、错误归属和维护边界保持清楚。

## 每类基准验证什么

### 子代理

- 使用真实发现管线读取 `agent.json`、`AGENT.md`、`trigger.md`、能力配置和可选 Schema。
- 检查输入、输出均为对象 Schema，声明的插件真实存在，公开与内部调用权限一致。
- 自定义执行器必须提供同步 `execute(context, input_data)` 入口。
- 动态闭环使用假 Provider 和 Schema 样例，不请求真实模型，也不消耗模型额度。

### 拓展

- 检查 `expand.json`、模块边界、Markdown 数据出口、Prompt 注册和健康状态。
- 采集端验证同步零参数 `update()` 或 `main()`。
- 操控端验证 `execute(command, params)` 及兼容旧入口的 JSON 子进程协议。
- `input_data.md` 只需保存适合注入 Prompt 的摘要或资源引用；操控结果和大型 artifacts 不要求绕经该文件。

### 外部消息路由

- 检查 `message.json`、`state.json`、平台能力、绑定用户及输入、输出、检测三个入口的签名。
- 检查 `message.md` 为空或包含完整可消费的 YAML front matter 消息。
- 使用合成消息验证统一消息转换与 Transport 发现。
- 通用验收不会启动真实平台、消费积压消息、发送消息、调用在线健康检测或要求真实 Token。

### 感知

- 检查 `sense.json` 和声明路径。
- 验证同步零参数 `update()` 或 `main()`。
- 成功采集后检查 Markdown 出口、时间、健康状态以及真实 Prompt 来源发现。
- 采集内容可以来自传感器、浏览器、数据库、API、文件或媒体管线，测试不限制内部实现。

### 技能

- 在候选目录中递归识别一个或多个大小写兼容的 `SKILL.md`。
- 检查一级标题、发现描述、模板占位符和明确引用但缺失的相对资源。
- 使用真实 Prompt 来源注册器验证嵌套技能可发现。
- 不要求固定章节、`scripts/`、`references/` 或 `assets/` 目录结构。

### 用户包

- 检查用户配置与全局配置合并、用户人格进入 Prompt，以及用户资源目录初始化。
- 检查三层临时记忆索引、永久记忆目录和多用户隔离。
- 报告不得回显 Provider 密钥，也不会请求真实 Provider。
- `task_cron` 与 `task_plan` 仅检查目录存在，不测试其业务状态机。

## 标准执行流程

1. 根据要创建的资源读取 `template/<kind>/` 及对应专题知识文档。
2. 创建最小可发现骨架，再按实际需求自由增加内部模块、目录或迁入已有工程。
3. 读取对应 `tests/template_tests/<kind>/STANDARD.md`，确认该类当前公开合同。
4. 从仓库根目录运行该类独立 CLI，不要用另一类标准替代。
5. 修复全部 `FAIL`，核对 `WARN`，并记录所有 `SKIP` 所代表的未验证外部条件。
6. 对网络、平台账号、浏览器、硬件、设备或真实 Provider 另行运行模块自己的集成测试。
7. 模块或框架合同有后续改动时，重新运行本类验收和相关项目回归。

验证通过只说明框架基础合同成立，不能证明所有业务数据、外部服务和异常场景都已经正确。尤其是依赖真实平台的消息路由、依赖设备的感知以及带外部副作用的拓展，仍需在受控环境进行集成测试。

## 命令选项

六个独立入口和根级薄入口共享以下选项：

- `--format text|json`：选择人类可读文本或结构化 JSON 报告。
- `--report <path>`：除标准输出外，再将报告写入指定文件。
- `--timeout <seconds>`：设置每个隔离子进程的超时。
- `--static-only`：只检查和导入入口，不调用候选采集器、操控器或 executor。
- `--template-mode`：验收仓库参考模板时允许待替换占位符，并把未安装的可选外部 SDK 记为 `SKIP`。
- `--repository-root <path>`：显式指定用于加载真实框架合同的 kemo-agent 根目录。

例如，先对来源尚未完全确认的拓展执行静态检查，并保存 JSON 报告：

```powershell
python -m tests.template_tests.expand `
  --target users/alice/expand/example `
  --static-only `
  --format json `
  --report tmp/example-expand-contract.json
```

Linux Shell 使用相同模块入口，只需按 Shell 语法调整续行符。

## 报告状态

| 状态 | 含义 | 处理要求 |
|------|------|----------|
| `PASS` | 合同已经实际验证 | 可以继续检查其他项 |
| `FAIL` | 基础合同不成立 | 修复后重新运行，不能按成功交付 |
| `WARN` | 合同成立但存在风险或不完整引用 | 核对原因并在交付中说明 |
| `SKIP` | 缺少凭据、网络、设备、可选 SDK，或动态调用被关闭 | 记录为未验证，不能描述成完整通过 |

进程退出码为 `0` 表示报告中没有 `FAIL`，不等于所有检查都已执行。JSON 报告中的 `ok=true` 只表示没有失败；只有 `complete=true` 才表示同时不存在 `SKIP`。

## 隔离与安全边界

动态验收会把候选目录复制到临时项目根，通过真实框架发现器和有超时的子进程检查入口与出口，避免采集器或操控器直接改写原候选目录。外部消息通用验收只做平台无关的合成协议检查，不启动真实长轮询或发送消息。报告不应回显 API Key、Token、Cookie、密码或其他凭据。

临时副本不是完整的恶意代码沙箱。候选代码仍可能主动访问网络、硬件或绝对路径，`--static-only` 也会为了检查合同而在有超时的子进程中导入声明入口，入口的模块级代码仍可能执行。来源不可信时，应先人工审查并在操作系统级隔离环境运行静态验收。自定义子代理执行器由主进程信任加载的生产边界不会因为验收工具而改变。

## 防止测试“上帝模块”

目录职责固定如下：

```text
tests/template_tests/
├── agent/    # 子代理标准、验证器、专属探针和测试
├── expand/   # 拓展标准、验证器和测试
├── message/  # 外部消息标准、验证器和测试
├── sense/    # 感知标准、验证器和测试
├── skills/   # 技能标准、验证器和测试
└── user/     # 用户包标准、验证器和测试
```

根目录只允许存在真正跨类型且无业务判断的基础设施，例如报告数据结构、临时沙箱、通用 Python 入口探测、类型标记识别、CLI 参数和延迟分发。以下做法不允许：

- 在根级验证器持续增加 `if kind == ...` 的具体业务检查。
- 让一个测试文件导入并编排六类所有业务场景。
- 为复用少量代码把不同类型的清单字段、生命周期和输出规则合并成一个巨型抽象。
- 修改一种模块协议时，要求无关类型的验证器同步修改。

如果以后增加新的模板资源类型，应新建 `tests/template_tests/<new-kind>/`，让它拥有自己的 `STANDARD.md`、`validator.py`、`__main__.py` 和回归测试；根级只增加一条最小类型映射。公共辅助只有在至少两个类型拥有完全相同且稳定的技术行为时才可抽取，并且不能携带任一类型的业务字段或判断。

## 维护同步规则

当框架公开合同变化时，按以下顺序维护：

1. 修改真实运行时代码和对应模板。
2. 更新该类型的专题全局知识文档。
3. 更新 `tests/template_tests/<kind>/STANDARD.md` 与本类验证器。
4. 增加或调整本类回归测试。
5. 更新模板说明中的独立验收命令。
6. 运行本类验收测试、相关项目测试和 `git diff --check`。

只有跨所有类型的报告协议或沙箱机制变化时，才修改根级公共文件。这样智能体创建哪类模块，就能直接找到哪类标准；维护者也能在不理解其他五类实现的情况下安全修改一类验收逻辑。
