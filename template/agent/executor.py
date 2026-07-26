"""子代理自定义执行入口示例。

本文件可删除；没有 ``executor.py`` 时框架使用内置 LLM 执行器。保留时可以完全
重写，或让 ``execute()`` 作为适配器导入代理目录内任意层级模块或完整工程。
框架只要求最终函数合同，不要求保留 ``VALID_TRIGGERS`` 或本示例的组织方式。

== context 对象 ==
  - context.run_model(input_data) → AgentRunResult：调用 LLM 生成回复
  - context.runner：AgentRunner 实例，可访问 root/user/config
  - context.cancel_event：threading.Event，检测取消信号

== 返回值 ==
必须返回 AgentRunResult 对象。可通过 context.run_model() 直接获得，或自行构造。
"""

from __future__ import annotations

from typing import Any

from run.agent_runner import AgentOutputError, AgentRunResult


# 可选样例校验；允许删除并采用其他内部路由方式。
VALID_TRIGGERS = frozenset({"default"})


def execute(context, input_data: dict[str, Any]) -> AgentRunResult:
    """
    框架调用的唯一入口。

    context: 包含 run_model() / runner / cancel_event
    input_data: 主智能体传入的数据，至少包含 "trigger" 字段
    """
    trigger = input_data.get("trigger")
    if trigger not in VALID_TRIGGERS:
        raise AgentOutputError(
            f"my_agent trigger 必须是 {', '.join(sorted(VALID_TRIGGERS))}"
        )

    # 可直接运行模型，也可转交当前代理目录内的任意可信内部工程。

    result = context.run_model(input_data)

    return result
