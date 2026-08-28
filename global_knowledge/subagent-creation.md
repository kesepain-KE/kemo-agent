# 子智能体创建文档

子智能体适合需要独立 LLM Prompt、独立工具循环、独立权限或可被主智能体/Cron 重复调用的任务。单次整理、普通文件操作、纯说明或已有工具能完成的需求，不应创建重型子智能体。

## 位置与发现

| 类型 | 路径 | 说明 |
|------|------|------|
| 内置 | `agents/<name>/` | 框架受信任子智能体，可携带执行代码 |
| 用户 | `users/<user>/agents/<name>/` | 当前用户热插拔子智能体，不能覆盖内置名称 |
| 外部绑定 | `global_expand/<name>/agent_bridge.json`、`shared_expand/<name>/agent_bridge.json` 或 `users/<user>/expand/<name>/agent_bridge.json` | 通过已授权拓展隔离进程调用外部智能体；不把远程地址或凭据交给核心 |

发现管线每次执行前重新扫描，因此用户子智能体新增或修改后通常无需重启。自定义 Python 执行器会被主进程直接导入，没有代码沙箱，只能安装可信代码。

名称必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$` 并与目录名一致。

## 最小包合同与自由实现

框架合同由 `agent.json`、`agent-config.json`、`AGENT.md` 和 `trigger.md` 构成；`executor.py` 与 `schema.json` 均可选。它们只是发现、权限、Prompt、调用和输入输出边界，不是子代理内部工程的完整结构。

子代理目录可以自由包含任意模块、包、配置、资源、测试或完整工程。没有 `executor.py` 时使用内置 LLM 执行器；需要自定义逻辑时，根入口 `executor.py` 可以保持很薄并导入目录内任意层级的内部实现。未被入口导入或未被合同引用的文件不会自动加载、注入 Prompt 或执行，也不会仅因模板未列出而被校验器拒绝。

`subagent_dispatch action=create` 只原子建立安全的数据型最小包。创建成功后可以继续使用正常文件或代码工具完善目录、增加 `schema.json` 或可信执行代码；创建接口的 `definition` 不承担传输完整工程的职责。

### agent.json

精简清单必须恰好包含四个字段：

```json
{
  "name": "report_reviewer",
  "version": "1.0.0",
  "description": "独立审查报告结构、证据与遗漏项。",
  "trigger": "trigger.md"
}
```

`trigger` 必须是同目录文件名。精简清单存在 `executor.py` 时自动使用 `executor.py:execute`，否则使用内置 LLM 执行器。

### agent-config.json

```json
{
  "schema_version": 1,
  "internal_mode": false,
  "allowed_callers": ["main_agent"],
  "tools": {
    "plugins": {"allow": ["file"]},
    "shared_skills": {"allow": []},
    "max_iterations": 20
  },
  "global_knowledge": false,
  "shared_knowledge": false,
  "inherit_main_history": false
}
```

| 字段 | 说明 |
|------|------|
| `internal_mode` | `true` 为内部代理，不向主智能体公开；`false` 可作为工具调度 |
| `allowed_callers` | 明确允许的调用方，例如 `main_agent`、`engine` |
| `tools.plugins.allow` | 可调用插件白名单；缺失授权默认拒绝 |
| `tools.shared_skills.allow` | 可注入共享技能白名单 |
| `tools.max_iterations` | 子智能体单次运行允许的工具调用上限，正整数；同时受全局同名配置的硬上限约束 |
| `global_knowledge` | 是否注入全局知识索引 |
| `shared_knowledge` | 是否注入共享知识索引 |
| `inherit_main_history` | 是否继承主会话历史和当前请求；默认关闭 |

主智能体用户配置的白名单不会自动收缩子智能体显式授权。创建时坚持最小权限，不因为“可能有用”扩大工具、知识或历史范围。子智能体不会得到用户技能、三层拓展或 `subagent_dispatch` 递归调度能力。

### AGENT.md

必须清楚写出：核心职责、输入来源、执行流程、输出对象、禁止事项、失败处理和写入边界。不要把用户输入拼成更高优先级系统规则。

### trigger.md

必须含准确标题：

```markdown
# 注册信息

- **名称**: report_reviewer
- **触发**: 当用户要求独立审查报告质量时
- **职责**: 检查证据、结构、矛盾和遗漏
- **模型**: reasoning
- **工具**: file

# 操作信息

## 调用方式

说明输入对象、输出对象、错误和注意事项。
```

运行时只把“注册信息”摘要注入主智能体；操作信息按需读取。

### executor.py 与 schema.json

可选执行器合同：

```python
def execute(context, input_data: dict):
    return context.run_model(input_data)
```

执行器应检查 trigger、取消信号和输出格式。`schema.json` 若存在，必须且只能包含 `input_schema` 与 `output_schema` 两个对象；缺失时使用宽松对象 Schema。

自定义执行器会被主进程直接导入，不具备代码沙箱。内部工程自由不改变这一信任边界：只能放入可信代码，不得在导入阶段启动线程、发起网络请求或产生其他副作用；长期任务仍必须响应 `context.cancel_event` 和整体超时。

## 创建流程

1. 判断是否真的需要独立推理与权限边界。
2. 列出真实可用插件和共享技能，逐项让用户确认授权。
3. 确认是否访问全局/共享知识以及是否继承历史。
4. 确认名称、职责、完整 instruction 和明确触发条件。
5. 使用 `subagent_dispatch action=list` 查重并最终确认。
6. 使用 `action=create` 原子创建最小用户代理包；按需求继续在目录内自由添加内部模块或已有工程，然后重新发现校验。
7. 用最小输入试运行，验证 JSON 输出、超时、取消、工具权限和自定义入口导入行为。

## 调用规则

主智能体通过 `subagent_dispatch` 的 `list/call/status/cancel` 调用公开代理。同步任务使用 `wait=true`；长任务可后台提交并查询状态。调用前读取目标 `trigger.md`，按照约定构造结构化输入。

### 外部智能体绑定

拓展可以额外放置 `agent_bridge.json`，把外部 kemo-agent、其他 Agent 服务或本地代理程序
包装成可调用的同步子代理。文件只声明公开名称、说明、`command`、输入/输出 JSON Schema
和可选超时，不声明 URL、Token、密码或其他凭据：

```json
{
  "schema_version": 1,
  "agents": [
    {
      "name": "researcher",
      "description": "外部研究智能体",
      "command": "external_agent_call",
      "input_schema": {"type": "object", "additionalProperties": true},
      "output_schema": {"type": "object", "additionalProperties": true},
      "timeout": 600
    }
  ]
}
```

调用句柄为 `external:<scope>:<拓展名>:<代理名>`。核心会在调用前重新检查拓展白名单、
清单、符号链接和 Schema，再通过 `start_expand.py` 传递 `{agent, input, protocol}`；拓展返回
`{"status":"completed","data":{...}}` 后，核心再次校验输出。当前只允许 `wait=true` 同步调用，
因为外部服务尚未共享 kemo-agent 的持久任务状态、取消和恢复合同。远程服务的认证信息必须由
拓展从受信环境变量或私有配置读取，不能写入桥接清单、Prompt、回复或日志。

子智能体整体期限来自 `agent_runtime.default_timeout`，与其内部每次工具调用的 `tools.timeout` 相互独立。同步调用的 `subagent_dispatch` 使用调用方传入的 `timeout` 作为本次等待期限，不会在普通工具默认期限到达时提前截断；期限到达后，如果子代理仍在排队或运行，会先返回 `task_id`，继续保留任务用于状态追踪和取消。收尾存活期内自然完成时记录 `completed_after_timeout`；如果调用方已经收到超时结果、底层线程之后才完成，状态会从 `timed_out_running` 收敛到 `completed`，并在结果元数据中标记 `completed_after_detach`。收尾后执行线程仍未退出时记录 `timed_out_running`，该状态仍允许发起取消请求，不能描述成已经强制终止。
