# subagent_dispatch
发现并调用当前用户可用的公开子代理；支持列出、同步调用、后台提交、状态查询和取消。

## 创建子代理四步流程（必须遵守）

收到创建子代理的请求后，不得立即调用 `action=create`。必须依次完成以下四步；
只要仍有关键配置未经用户确认，就暂停创建并继续沟通。

### 第一步：批判性判断

先判断需求是否真的需要一个独立子代理。

适合创建子代理的场景：

- 任务需要独立的 LLM 推理、独立 prompt 或独立工具调用循环。
- 能力需要被定时任务、主智能体或多个入口反复复用。
- 任务需要与主对话隔离上下文、模型档位或权限边界。

不适合创建子代理的场景：

- 单次查询、一次性整理或纯文件增删改。
- 用户只是询问问题，主智能体可以直接回答或执行。
- 一个现有插件、技能或已注册子代理已经覆盖需求。

子代理是有独立提示词、权限和运行成本的重型能力。如果没有稳定复用价值，明确说明
不建议创建的原因，并给出直接执行、插件或技能等更简单的替代方案。

### 第二步：确认工具权限

确认确实需要子代理后，必须向用户确认以下权限：

1. 需要开放哪些插件工具。
2. 需要开放哪些共享技能。
3. 是否访问全局知识库或共享知识库。
4. 是否继承主对话历史。

应从主智能体当前 Tool Registry、已注册插件和共享技能清单中列出真实可用名称供用户选择。
`subagent_dispatch action=list` 只列出公开子代理，不能用来获取工具列表。

不要替用户扩大权限。用户选择“不开放”或明确接受默认值时，使用空工具、空技能白名单，
关闭知识库访问和历史继承；未确认时不得把“默认最小权限”视为已经获得用户同意。

### 第三步：确认触发提示词

询问用户：“主智能体在什么情况下应该调用这个子代理？”

- 引导用户用自然语言描述明确的触发条件，例如“当用户要求搜索并对比多个来源时”。
- 将用户描述精炼为一至两句话的 `trigger_condition`，并回显给用户确认。
- 不得直接使用“当任务符合描述且需要独立处理时”这类默认文本，除非用户明确表示无所谓。
- 同时确认写入 `AGENT.md` 的 instruction 能清楚描述职责、输入、输出和禁止事项。

### 第四步：检查冲突与创建

调用 `action=create` 前必须完成：

1. 调用 `action=list`，检查拟用名称是否与当前可见子代理冲突。
2. 检查名称符合 `^[A-Za-z][A-Za-z0-9_-]{0,63}$`。
3. 汇总并让用户最终确认：名称、描述、instruction、工具与技能白名单、知识权限、
   历史继承设置、触发条件。
4. 用户明确确认后，调用 `action=create` 并传入完整 `definition`；名称冲突仍以创建接口的
   原子校验结果为准，不能绕过隐藏或内置名称保护。
5. 创建成功后报告名称、路径、触发条件和实际授权；创建失败时报告原始校验错误，不得
   偷偷改名或放宽权限后重试。

### 子代理目录不是固定模板

`action=create` 只建立安全的数据型最小包，不代表子代理只能包含生成的合同文件。创建成功后，可以使用正常文件或代码工具在 `users/<user>/agents/<name>/` 内自由增加任意模块、目录、资源、Schema 或完整工程；复杂逻辑可由可选的根 `executor.py` 作为薄适配入口导入。额外文件不会仅因存在而自动注入或执行，也不得因模板未列出而被删除或强行合并。

自定义执行器会被主进程直接导入，没有代码沙箱。只有在确实需要可信自定义代码时才添加，并重新发现校验、测试取消与超时；简单 LLM 子代理可以保留创建器生成的最小包，不必为了匹配模板增加执行器或内部目录。

## 调用已有子代理

- `list`：列出当前用户可由主智能体调用的公开子代理。
- `call`：调用公开子代理；同步等待时设置 `wait=true`，后台提交时设置 `wait=false`。
- `status`：使用后台任务 ID 查询状态。
- `cancel`：取消尚未完成的后台任务。
- 调用前先读取目标子代理的 `trigger.md`，按其中约定构造结构化 `input`。
- `task_plan` 是特殊的同步调用：框架会从当前 Tool Registry 和配置强制补齐规划输入并在返回前持久化计划；调用方不要手工传入工具白名单，也不能对它使用 `wait=false`。
- `time_plan` 的 `current_time_beijing` 由框架按 `Asia/Shanghai` 强制注入；调用方无需先调时间工具，手工提交的同名值也会被覆盖。
- `self_improve` 由主智能体调用时只允许 `manual_review`；压缩提取和记忆晋升模式属于引擎/调度器私有入口。
- `context_manage` 是引擎内部代理，不出现在公开列表中；手动压缩必须走 `/compress` 对应的会话管线。
- 其他公开子代理只获得自身 `agent-config.json` 声明的能力以及调用方显式输入，不会继承主智能体的工具权限。
- 同步调用默认遵循 `agent_runtime.default_timeout`，也可以在 `call` 中传入独立的 `timeout` 秒数；不会被普通 `tools.timeout` 提前截断。达到期限后，框架默认再保留 `agent_runtime.timeout_survival_seconds` 秒的收尾存活期；期间自然完成会返回结果并标记 `completed_after_timeout`，仍未完成才返回 `timed_out` 或 `timed_out_running`。存活期内仍可取消。

## Tool

```json
{
  "name": "subagent_dispatch",
  "description": "发现和调度当前用户可用的公开子代理。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "create", "call", "status", "cancel"]},
      "agent": {"type": "string", "description": "call 时使用的公开子代理名称"},
      "input": {"type": "object", "description": "call 时传给子代理的结构化输入"},
      "definition": {
        "type": "object",
        "description": "create 时使用的数据型用户代理定义；建立可继续自由扩展的最小包合同",
        "properties": {
          "name": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9_-]{0,63}$"},
          "description": {"type": "string"},
          "instruction": {"type": "string", "description": "写入 AGENT.md 的完整代理指令"},
          "trigger_condition": {"type": "string", "description": "写入 trigger.md 注册信息的触发条件"},
          "version": {"type": "string", "default": "1.0.0"},
          "input_schema": {"type": "object", "description": "可选，仅作为 trigger.md 中的输入参考"},
          "output_schema": {"type": "object", "description": "可选，仅作为 trigger.md 中的输出参考"},
          "agent_config": {
            "type": "object",
            "description": "可选运行时授权；未提供时创建可由 main_agent 调用、无工具和知识权限的代理",
            "properties": {
              "schema_version": {"type": "integer", "enum": [1]},
              "internal_mode": {"type": "boolean"},
              "allowed_callers": {"type": "array", "items": {"type": "string"}},
              "tools": {
                "type": "object",
                "properties": {
                  "plugins": {
                    "type": "object",
                    "properties": {
                      "allow": {"type": "array", "items": {"type": "string"}}
                    }
                  },
                  "shared_skills": {
                    "type": "object",
                    "properties": {
                      "allow": {"type": "array", "items": {"type": "string"}}
                    }
                  },
                  "max_iterations": {"type": "integer", "minimum": 1}
                }
              },
              "global_knowledge": {"type": "boolean"},
              "shared_knowledge": {"type": "boolean"},
              "inherit_main_history": {"type": "boolean"}
            },
            "required": ["schema_version", "internal_mode", "allowed_callers", "tools", "global_knowledge", "shared_knowledge", "inherit_main_history"],
            "additionalProperties": false
          }
        },
        "required": ["name", "description", "instruction"],
        "additionalProperties": false
      },
      "wait": {"type": "boolean", "description": "call 时是否同步等待；默认 true", "default": true},
      "timeout": {"type": "number", "description": "call 时可选；本次子代理整体超时秒数，必须为正数；省略时使用 agent_runtime.default_timeout（默认 600 秒）"},
      "task_id": {"type": "string", "description": "status 或 cancel 时使用的后台任务 ID"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "timeout_policy": "agent_runtime"
}
```
