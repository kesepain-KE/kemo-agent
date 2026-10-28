# subagent_dispatch
发现并调用当前用户可用的公开子代理；支持预算披露、同步调用、后台提交、预算停止后续跑、运行中指导、显式拆分、状态查询和取消。

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

- `list`：列出当前用户可由主智能体调用的公开子代理，含有效默认工具额度
  max_iterations、default_timeout、supports_budget_override、supports_resume、
  authorized_plugins（当前授权交集，不是声明的 *）和 knowledge_scopes。
- `call/resume` 的 `max_tool_calls` 是本段工具额度：省略使用默认（resume 沿用上一段），
  正整数覆盖默认，0 显式不限工具次数。仅公开的本地 LLM 非串行业务代理可覆盖，
  不改变插件/技能/知识权限、不取消 timeout、取消、重复调用和连续失败保护。
  timeout 为本段运行预算秒数，排队不消耗；resume 省略沿用上一段时长。
  调整前先评估任务范围，广泛审计优先按可独立交付阶段拆分，不默认使用不限次数。
- 工具额度耗尽时状态为 `tool_limit`，stop_reason 为 `tool_budget_exhausted`；
  已退出、有有效快照且 resumable=true 才能 resume。每段获得新额度，
  例如 80 次停止后再 resume 可再调用 80 次，budget 同时保存本段和累计数。
  模型内部重试不重置本段工具预算；结论收尾不再开放业务工具，也不保证成功。
  旧版 failed + AgentToolLimitError 会提示 resume_candidate；允许显式申请续跑，
  但仍须有匹配定义/轮次/身份的有效私有安全点，不能恢复任意失败或从头补跑。
- `guide`：task_id + guidance，向当前主任务所属的排队/运行中本地业务子代理
  投递指导，在下一请求安全点处理；不是实时打断在途工具，不启动第二个执行器。
- `split`：task_id + tasks（1..8 个完整 input），只拆实际已退出的预算停止或失败
  任务的剩余工作；由主智能体明确编写阶段、输入与验收，保留 origin_task_id。
  不自动重派。批量入队可部分成功，按 accepted_count/task_ids 核对，只补交未接受项。
  已运行的后台 Shell 要先按 job_id 查询，不把原任务整个重新执行。
- status 附有界预算、最后阶段、最后工具名和已确认用量，不附参数或整包日志；
  约 80% 时子代理收到收尾提醒，主代理在请求安全点收到每段一次可确认的短预警。
  多个同根任务同类预算停止时附聚合建议，不自动增加费用。
- `call`：调用公开子代理；普通公开本地 LLM 非串行业务代理省略 `wait` 默认后台提交，显式 `wait=true` 同步等待、`wait=false` 后台提交。`resume` 使用相同缺省策略。
  两者都是当前用户请求的必要依赖；后台提交后可继续其他工作，但不能把 queued 当成完成。
  框架会在安全点自动交付子任务结果；通常不需要反复 status 轮询。主任务等待／取消与
  网页执行详情合同见 `global_knowledge/agent-task-coordination.md`。
- 互不依赖的多个普通任务优先以 `wait=false` 分别提交，调度器会在共享 API 请求上限内并行执行。
  省略 wait 也会后台提交，不要求主模型靠记住额外参数才能获得并行；不要先同步等待一个再提交另一个，也无需自己用 status 轮询实现并行。
  `wait=true` 保留同步语义；串行维护代理及特殊同步规划不强行并行。
  网页按持久提交编号展示；同批结果通知按相同编号返回，同时完成也不会乱序。
- `status`：使用后台任务 ID 查询状态。
- `resume`：继续同一对话空间内已“超时停止”或“工具额度停止”且可恢复的任务；使用原 task_id，
  可附 guidance 限定剩余工作，timeout 为新一轮运行预算，wait 控制是否同步等待。
  沿用原任务编号与提交顺序，run_count 增加，每轮重新计时并分配新段额度；不是重新派发相同任务。
  不能恢复仍在停止中的线程、已完成/取消任务、外部代理、自定义 Python 执行器或维护代理。
  同任务不能并发续跑；快照失效、定义变化或权限不再允许时明确拒绝，不从头重做。
- `cancel`：取消尚未完成的后台任务。
- 主智能体默认只接收结论、必要证据、产物路径和未完成事项；call/status/resume
  与后台通知不附工具记录、卡片或原始 metadata。工具过程保留在网页任务详情。
  超时有结论时返回已有结论，无结论时明确说明；不能把执行轨迹冒充已确认结论。
- 调用前先读取目标子代理的 `trigger.md`，按其中约定构造结构化 `input`。
- `task_plan` 是特殊的同步调用：框架会从当前 Tool Registry 和配置强制补齐规划输入并在返回前持久化计划；调用方不要手工传入工具白名单，也不能对它使用 `wait=false`。
- `time_plan` 的 `current_time_beijing` 由框架按 `Asia/Shanghai` 强制注入；调用方无需先调时间工具，手工提交的同名值也会被覆盖。
- `self_improve` 由主智能体调用时只允许 `manual_review`；压缩提取和记忆晋升模式属于引擎/调度器私有入口。
- `context_manage` 是引擎内部代理，不出现在公开列表中；手动压缩必须走 `/compress` 对应的会话管线。
- `general_worker` 是通用执行助手：明确传入 task、必要背景、约束和验收条件，可用 `wait=false`
  后台执行。插件、技能和知识采用当前用户授权；拓展/感知不自动注入，可通过已有授权工具按需使用。
  它不能继续委派，也不会自动继承主对话历史。调用前读取其 `trigger.md`。
- 其他公开子代理只获得自身 `agent-config.json` 声明的能力以及调用方显式输入，
  不会仅因主智能体拥有某项工具就自动获得授权；显式 `allow=["*"]` 才选择当前用户已授权的动态工具集。
- 同步调用默认遵循 `agent_runtime.default_timeout`，也可以在 `call/resume` 中传入独立的
  `timeout` 秒数；不会被普通 `tools.timeout` 提前截断。同步等待到期时先交回任务 ID，
  实际运行预算从本轮执行开始计算。可恢复的本地 LLM 业务子代理到期进入现有宽限内的单次收尾，
  不再启动业务工具，随后以 timed_out（超时停止）交付部分结论，resumable=true 时可显式续跑。
  若模型/工具尚未真实退出则是 timed_out_running（超时停止中），仍保留执行槽和主任务屏障，
  不得并发恢复。可恢复业务任务一旦进入超时流程，迟到结论仍作为超时成果保存；
  非恢复执行器保留原有 completed_after_timeout 对账，残缺成果不算完整成功。
  用户取消优先，不触发自动收尾或续跑；非协作线程不能被宣称立即强杀。

### 调用外部智能体

`action=list` 也会列出当前用户已授权拓展中的外部智能体。外部条目的名称采用
`external:<scope>:<拓展名>:<代理名>`，例如 `external:user:remote_bridge:researcher`。
调用时继续使用 `action=call`，把该完整句柄放入 `agent`，把业务数据放入 `input`，并保持
`wait=true`。外部调用通过拓展的隔离进程执行，仍受拓展白名单、路径安全、超时、取消和结果大小限制；
当前不支持 `wait=false`，避免在没有统一任务状态合同的情况下遗留无人管理的远程任务。

要绑定外部智能体，在对应拓展目录内增加可选的 `agent_bridge.json`：

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

拓展的 `start_expand.py` 收到的参数为 `{agent, input, protocol}`，必须返回
`{"status":"completed","data":{...}}`（可选 `usage`、`model`）。远程 URL、访问令牌和其他
凭据只能保存在拓展自己的受信配置或环境变量中，不能写入 `agent_bridge.json`、Prompt、回复或日志。

## Tool

```json
{
  "name": "subagent_dispatch",
  "description": "发现和调度当前用户可用的公开子代理。普通本地 LLM 非串行业务代理 call/resume 省略 wait 默认后台提交并行执行，共用 Provider API 请求池；仍是主任务必要依赖，框架按稳定提交序号交付同批结果，无需反复查询状态。显式 wait=true 同步等待；特殊规划、串行维护和外部代理默认同步。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {"type": "string", "enum": ["list", "create", "call", "resume", "guide", "split", "status", "cancel"]},
      "agent": {"type": "string", "description": "call 时使用的公开子代理名称；外部智能体使用 external:<scope>:<拓展名>:<代理名> 句柄"},
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
      "wait": {"type": "boolean", "description": "call/resume 是否同步等待。普通本地 LLM 非串行业务代理省略默认 false；特殊规划、串行维护和外部代理默认 true。显式 true 保留同步等待，显式 false 仅用于支持后台的代理"},
      "timeout": {"type": "number", "description": "call/resume 时可选；本轮子代理运行预算秒数，必须为正数；省略时使用代理默认预算"},
      "guidance": {"type": "string", "maxLength": 8000, "description": "resume 时可选，guide 时必填；指导剩余工作，不新增权限，guide 在下一请求安全点交付"},
      "max_tool_calls": {"type": "integer", "minimum": 0, "description": "call/resume/split 的每段工具调用额度；正整数覆盖默认，0 显式不限工具次数（仍受 timeout/取消/失败保护约束）。resume 省略沿用上一段有效额度。仅公开本地 LLM 非串行业务代理可覆盖"},
      "tasks": {"type": "array", "minItems": 1, "maxItems": 8, "items": {"type": "object"}, "description": "split 时由主智能体明确给出的 1..8 个完整 input；原任务须已停止或失败且实际退出。只拆剩余工作，不重做已完成副作用"},
      "task_id": {"type": "string", "description": "resume/status/cancel 时使用的原任务 ID"}
    },
    "required": ["action"],
    "additionalProperties": false
  },
  "version": "1.0.0",
  "enabled": true,
  "entrypoint": "tool.py:run",
  "timeout_policy": "agent_runtime",
  "timeout_grace_seconds": 5,
  "execution_mode": "thread"
}
```
