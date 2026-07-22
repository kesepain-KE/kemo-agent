"""
子代理执行入口模板。

== 用途 ==
复制到 agents/<name>/executor.py 后，框架通过 execute(context, input_data) 调用此文件。
此模板不能直接运行——需要按实际子代理修改 VALID_TRIGGERS 和核心逻辑。

== 修 改 指 南 ==
1. 修改 VALID_TRIGGERS：替换 {"default"} 为实际触发值，如 {"context_compression", "memory_promotion"}
2. 修改 execute() 内部：在 context.run_model() 前后加入预处理/后处理
3. 如需调用其他子代理：使用 context.runner.run_agent("子代理名", data)
4. 如需读写记忆：使用 MemoryStore(context.runner.root, context.runner.user, context.runner.config)

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


# ═══════════════════════════════════════════════════
# 修改此处：定义合法的 trigger 值
# ═══════════════════════════════════════════════════
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

    # ── 在此插入预处理逻辑 ──

    result = context.run_model(input_data)

    # ── 在此插入后处理逻辑（校验输出、写记忆等）──

    return result
