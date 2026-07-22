import { describe, expect, it } from 'vitest'
import { buildHistoryItems, buildScheduledTaskItems, buildSenseDataItems, compactPlanAssistantText, extractPlanSummary, groupConversationItems, isNearScrollBottom, reduceRunEvent, selectDockedPlan } from './ChatPage'
import type { ChatItem, CronTaskSummary, PlanSummary, SenseSourceSummary } from '../types/api'

describe('reduceRunEvent', () => {
  it('task_plan 子代理结果生成独立任务计划记录项', () => {
    const event = {
      type: 'tool_call_result' as const,
      tool_call_id: 'call-plan',
      tool_name: 'subagent_dispatch',
      result: { ok: true, result: { plan: {
        plan_id: 'plan_12345678', title: '测试任务计划', description: '测试', status: 'pending',
        auto_accept: false, source: 'web', session_id: 's1', revision: 1,
        steps: [{ step_id: 'step_1', title: '第一步', description: '执行', status: 'pending', depends_on: [], critical: true }],
      } } },
    }
    const plan = extractPlanSummary(event.result)
    expect(plan).toMatchObject({ plan_id: 'plan_12345678', title: '测试任务计划' })
    const items = reduceRunEvent([], event)
    expect(items.map((item) => item.kind)).toEqual(['tool', 'task_plan'])
  })

  it('发送框只停靠最新的非终态任务计划', () => {
    const plan = (plan_id: string, status: string): PlanSummary => ({
      plan_id, title: plan_id, description: '', status, auto_accept: false, reminder: '', source: 'web', session_id: 's1',
      current_step: '', revision: 1, created_at: '', updated_at: '', progress: { completed: 0, total: 1, percent: 0 },
      steps: [{ step_id: 'step_1', title: '执行', description: '', status: 'pending', depends_on: [], critical: true, tool_name: '', started_at: '', finished_at: '' }],
    })
    expect(selectDockedPlan([plan('running-old', 'running'), plan('done', 'completed'), plan('pending-new', 'pending')])?.plan_id).toBe('pending-new')
    expect(selectDockedPlan([plan('done', 'completed'), plan('cancelled', 'cancelled')])).toBeUndefined()
  })

  it('消息流中的计划详情压缩为轻量确认文本', () => {
    expect(compactPlanAssistantText('新计划已生成：\n\n| 步骤 | 操作 |\n| --- | --- |', true)).toBe('任务计划已创建，请在发送框上方查看并确认。')
    expect(compactPlanAssistantText('普通回复正文', true)).toBe('普通回复正文')
    expect(compactPlanAssistantText('新计划已生成：完整步骤', false)).toBe('新计划已生成：完整步骤')
  })

  it('最近活动只保留用户定时任务', () => {
    const base: CronTaskSummary = {
      task_id: 'user-task', title: '用户任务', user_defined: true, status: 'enabled', type: 'daily', time: '18:00',
      next_run_at: '2026-07-20T18:00:00+08:00', latest_run_at: '', created_at: '2026-07-20T12:00:00+08:00', last_state: 'never',
    }
    const items = buildScheduledTaskItems([base, { ...base, task_id: 'system-task', title: '系统维护', user_defined: false }])
    expect(items.map((item) => item.title)).toEqual(['用户任务'])
  })

  it('最近活动只保留本轮实际注入的感知来源', () => {
    const base: SenseSourceSummary = {
      id: 'active', name: 'active', display_name: '运行时感知', description: '', layer: 'global', enabled: true,
      whitelisted: true, active_for_main_agent: true, status: 'active', data_md: 'sense.md', recent_update: '2026-07-20 12:00:00',
      health: '正常', valid: true, error: '', start_update: '', files: 1, registered_items: 1, injected_items: 1,
      data_items: ['sense.md'], value_preview: 'CPU 23%', collected_markdown: 'CPU 23%', injected_markdown: '[active]\nCPU 23%', injected_tokens: 5, update_interval: '', updated_at: 1,
    }
    const items = buildSenseDataItems([
      base,
      { ...base, id: 'not-injected', injected_items: 0 },
      { ...base, id: 'filtered', active_for_main_agent: false, status: 'filtered' },
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ id: 'active', value: 'CPU 23%', injected: true })
  })

  it('仅在视口接近底部时自动跟随流式输出', () => {
    expect(isNearScrollBottom({ scrollHeight: 1000, scrollTop: 610, clientHeight: 300 })).toBe(true)
    expect(isNearScrollBottom({ scrollHeight: 1000, scrollTop: 400, clientHeight: 300 })).toBe(false)
  })

  it('把同一轮思考、工具、正文和统计组合到一个稳定的智能体回复容器', () => {
    const items: ChatItem[] = [
      { id: 'user-1', kind: 'message', role: 'user', content: '请检查' },
      { id: 'reasoning-1', kind: 'reasoning', content: '分析中', streaming: true },
      { id: 'tool-1', kind: 'tool', callId: 'call-1', name: 'shell', status: 'running' },
      { id: 'assistant-1', kind: 'message', role: 'assistant', content: '检查完成', streaming: true },
      { id: 'usage-1', kind: 'usage', usage: { total_tokens: 12 } },
    ]
    const blocks = groupConversationItems(items)
    expect(blocks).toHaveLength(2)
    expect(blocks[0]).toMatchObject({ id: 'user-1', kind: 'user' })
    expect(blocks[1]).toMatchObject({
      id: 'assistant_turn_user-1_1',
      kind: 'assistant',
      items: [
        { id: 'reasoning-1' },
        { id: 'tool-1' },
        { id: 'assistant-1' },
        { id: 'usage-1' },
      ],
    })
  })

  it('流式合并正文和思考', () => {
    let items: ChatItem[] = []
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '思' })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '考' })
    items = reduceRunEvent(items, { type: 'text_delta', content: '你' })
    items = reduceRunEvent(items, { type: 'text_delta', content: '好' })
    expect(items).toHaveLength(2)
    expect(items[0]).toMatchObject({ kind: 'reasoning', content: '思考' })
    expect(items[1]).toMatchObject({ kind: 'message', content: '你好' })
  })

  it('按 tool_call_id 配对开始和结果', () => {
    let items: ChatItem[] = reduceRunEvent([], { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'time', arguments: { zone: 'local' } })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'time', result: { ok: false }, metadata: { status: 'failed', elapsed_ms: 12 } })
    expect(items[0]).toMatchObject({ kind: 'tool', callId: 'c1', status: 'error', result: { ok: false }, elapsedMs: 12 })
  })

  it('无论事件到达顺序如何都按思考、工具、正文排列一轮内容', () => {
    let items: ChatItem[] = [{ id: 'u1', kind: 'message', role: 'user', content: '请处理' }]
    items = reduceRunEvent(items, { type: 'text_delta', content: '最终正文' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '先分析问题' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'done', usage: { total_tokens: 12 } })

    expect(items.map((item) => item.kind)).toEqual(['message', 'reasoning', 'tool', 'message', 'usage'])
    expect(items[1]).toMatchObject({ kind: 'reasoning', content: '先分析问题', streaming: false })
    expect(items[2]).toMatchObject({ kind: 'tool', callId: 'c1', status: 'success' })
    expect(items[3]).toMatchObject({ kind: 'message', role: 'assistant', content: '最终正文', streaming: false })
  })

  it('从已提交历史恢复思考按钮和始终可见的工具卡片', () => {
    const items = buildHistoryItems({
      user: 'kesepain',
      source: 'web',
      session_id: 's1',
      messages: [
        { role: 'user', content: '检查状态' },
        { role: 'assistant', content: '检查完成' },
      ],
      round_metrics: [{ round: 1, usage: { total_tokens: 12 }, elapsed_ms: 20, tool_calls: 1, guidance: [] }],
      round_traces: [{
        round: 1,
        reasoning: '先分析',
        tools: [{
          call_id: 'c1', name: 'shell', status: 'success', elapsed_ms: 12,
          arguments_text: '{"command":"status"}', arguments_truncated: false,
          result_text: 'ok', result_truncated: false,
        }],
      }],
    })

    expect(items.map((item) => item.kind)).toEqual(['message', 'reasoning', 'tool', 'message', 'usage'])
    expect(items[1]).toMatchObject({ kind: 'reasoning', content: '先分析', streaming: false })
    expect(items[2]).toMatchObject({ kind: 'tool', callId: 'c1', argumentsText: '{"command":"status"}', resultText: 'ok' })
  })

  it('done 结束流式标记，error 生成错误项', () => {
    let items: ChatItem[] = [{ id: 'a', kind: 'message', role: 'assistant', content: 'ok', streaming: true }]
    items = reduceRunEvent(items, { type: 'done' })
    expect(items[0]).toMatchObject({ streaming: false })
    items = reduceRunEvent(items, { type: 'error', error: { message: 'failed' } })
    expect(items.at(-1)).toMatchObject({ kind: 'error', content: 'failed' })
  })

  it('done 生成可持久化的逐轮统计卡片', () => {
    const items = reduceRunEvent([], { type: 'done', usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, provider_request_count: 2 }, metadata: { elapsed_ms: 35, tool_calls: 1 } })
    expect(items[0]).toMatchObject({ kind: 'usage', elapsedMs: 35, toolCalls: 1, providerRequestCount: 2, usage: { total_tokens: 12 } })
  })
})
