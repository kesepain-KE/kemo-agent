# 子智能体创建文档

子智能体适合需要独立 LLM Prompt、独立工具循环、独立权限或可被主智能体/Cron 重复调用的任务。单次整理、普通文件操作、纯说明或已有工具能完成的需求，不应创建重型子智能体。

## 位置与发现

| 类型 | 路径 | 说明 |
|------|------|------|
| 内置 | `agents/<name>/` | 框架受信任子智能体，可携带执行代码 |
| 用户 | `users/<user>/agents/<name>/` | 当前用户热插拔子智能体，不能覆盖内置名称 |

发现管线每次执行前重新扫描，因此用户子智能体新增或修改后通常无需重启。自定义 Python 执行器会被主进程直接导入，没有代码沙箱，只能安装可信代码。

名称必须匹配 `^[A-Za-z][A-Za-z0-9_-]{0,63}$` 并与目录名一致。

## 标准包

```text
<agent-root>/<name>/
├── agent.json
├── agent-config.json
├── AGENT.md
├── trigger.md
├── executor.py          # 可选；不存在时使用 builtin:llm
└── schema.json          # 可选输入/输出 JSON Schema
```

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
| `tools.max_iterations` | 子智能体单次 Provider/工具循环上限，正整数 |
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

## 创建流程

1. 判断是否真的需要独立推理与权限边界。
2. 列出真实可用插件和共享技能，逐项让用户确认授权。
3. 确认是否访问全局/共享知识以及是否继承历史。
4. 确认名称、职责、完整 instruction 和明确触发条件。
5. 使用 `subagent_dispatch action=list` 查重并最终确认。
6. 使用 `action=create` 原子创建用户代理；创建后立即发现校验。
7. 用最小输入试运行，验证 JSON 输出、超时、取消和工具权限。

## 调用规则

主智能体通过 `subagent_dispatch` 的 `list/call/status/cancel` 调用公开代理。同步任务使用 `wait=true`；长任务可后台提交并查询状态。调用前读取目标 `trigger.md`，按照约定构造结构化输入。

子智能体整体期限来自 `agent_runtime.default_timeout`，与其内部每次工具调用的 `tools.timeout` 相互独立。同步调用的 `subagent_dispatch` 使用子智能体期限作为外层看门狗基准，不会在普通工具默认期限到达时提前返回。子智能体整体超时后框架会自动发送取消信号并等待短暂清理：执行线程已经退出时记录 `timed_out`，仍未退出时记录 `timed_out_running`，不得把后者描述成已经强制终止。
