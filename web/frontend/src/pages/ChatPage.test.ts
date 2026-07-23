import { describe, expect, it } from 'vitest'
import { buildHistoryItems, buildScheduledTaskItems, buildSenseDataItems, buildUserMessageMarkers, compactPlanAssistantText, extractPlanSummary, groupConversationItems, isNearScrollBottom, mergeHistoryPages, reduceRunEvent, selectDockedPlan } from './ChatPage'
import type { ChatItem, CronTaskSummary, PlanSummary, SenseSourceSummary } from '../types/api'

describe('reduceRunEvent', () => {
  it('用户消息导航只收集真实用户气泡并保留历史绝对轮次', () => {
    const markers = buildUserMessageMarkers([
      { id: 'history_18_user', kind: 'message', role: 'user', content: '第十八轮问题' },
      { id: 'history_execution_19', kind: 'execution_marker', planId: 'plan_1' },
      { id: 'history_19_assistant_1', kind: 'message', role: 'assistant', content: '执行结果' },
      { id: 'user_live', kind: 'message', role: 'user', content: '最新问题' },
    ], 18)

    expect(markers).toEqual([
      { id: 'history_18_user', content: '第十八轮问题', round: 18 },
      { id: 'user_live', content: '最新问题', round: 20 },
    ])
  })

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
    const items = buildScheduledTaskItems([
      base,
      { ...base, task_id: 'completed-task', title: '已完成单次任务', status: 'completed', type: 'once', next_run_at: '', last_state: 'completed' },
      { ...base, task_id: 'system-task', title: '系统维护', user_defined: false },
    ])
    expect(items.map((item) => item.title)).toEqual(['已完成单次任务', '用户任务'])
    expect(items[0]).toMatchObject({ id: 'completed-task', status: 'completed', nextRun: '—' })
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

  it('历史中的计划执行控制提示只生成执行标记，不显示伪用户气泡', () => {
    const items = buildHistoryItems({
      user: 'kesepain',
      source: 'web',
      session_id: 's1',
      messages: [
        { role: 'user', content: '【任务计划连续执行】\n计划 ID：plan_12345678\n起始步骤：step_1' },
        { role: 'assistant', content: '计划已经执行完成。' },
      ],
      round_metrics: [],
      round_traces: [],
    })

    expect(items).toMatchObject([
      { kind: 'execution_marker', planId: 'plan_12345678' },
      { kind: 'message', role: 'assistant', content: '计划已经执行完成。' },
    ])
    const blocks = groupConversationItems(items)
    expect(blocks).toHaveLength(1)
    expect(blocks[0]).toMatchObject({ kind: 'assistant' })
    expect(blocks.some((block) => block.kind === 'user')).toBe(false)
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

  it('逐条确认运行中引导并在本轮结束时固化全部状态', () => {
    let items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '开始执行' },
      { id: 'g1', kind: 'guidance', content: '先检查目录', status: 'queued' },
      { id: 'g2', kind: 'guidance', content: '结果放入临时区', status: 'queued' },
    ]
    items = reduceRunEvent(items, { type: 'guidance_applied', metadata: { guidance: ['先检查目录'] } })
    expect(items[1]).toMatchObject({ kind: 'guidance', status: 'accepted' })
    expect(items[2]).toMatchObject({ kind: 'guidance', status: 'queued' })

    items = reduceRunEvent(items, { type: 'done', metadata: { guidance_count: 1 } })
    expect(items[1]).toMatchObject({ kind: 'guidance', status: 'completed', finalized: true })
    expect(items[2]).toMatchObject({ kind: 'guidance', status: 'not_applied', finalized: true })
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

  it('合并向上分页结果并保留绝对轮次和稳定消息标识', () => {
    const latest = {
      user: 'kesepain', source: 'web' as const, session_id: 's1',
      messages: [
        { role: 'user' as const, content: '第 21 轮' },
        { role: 'assistant' as const, content: '第 21 轮回复' },
      ],
      round_metrics: [{ round: 21, usage: { total_tokens: 21 }, elapsed_ms: 21, tool_calls: 0, guidance: [] }],
      round_traces: [{ round: 21, reasoning: '思考 21', tools: [] }],
      pagination: { limit: 20, total_rounds: 21, first_round: 21, last_round: 21, has_more_before: true, next_before: 21 },
    }
    const earlier = {
      user: 'kesepain', source: 'web' as const, session_id: 's1',
      messages: [
        { role: 'user' as const, content: '第 1 轮' },
        { role: 'assistant' as const, content: '第 1 轮回复' },
      ],
      round_metrics: [{ round: 1, usage: { total_tokens: 1 }, elapsed_ms: 1, tool_calls: 0, guidance: [] }],
      round_traces: [{ round: 1, reasoning: '思考 1', tools: [] }],
      pagination: { limit: 20, total_rounds: 21, first_round: 1, last_round: 20, has_more_before: false, next_before: null },
    }

    const merged = mergeHistoryPages([latest, earlier])
    expect(merged?.messages.map((message) => message.content)).toEqual([
      '第 1 轮', '第 1 轮回复', '第 21 轮', '第 21 轮回复',
    ])
    expect(merged?.pagination).toMatchObject({ total_rounds: 21, first_round: 1, last_round: 21, has_more_before: false })
    const items = buildHistoryItems(latest)
    expect(items[0]).toMatchObject({ id: 'history_21_user', content: '第 21 轮' })
    expect(items[1]).toMatchObject({ id: 'history_reasoning_21', content: '思考 21' })
    expect(items.at(-1)).toMatchObject({ id: 'history_usage_21', round: 21 })
  })

  it('done 结束流式标记，error 生成错误项', () => {
    let items: ChatItem[] = [{ id: 'a', kind: 'message', role: 'assistant', content: 'ok', streaming: true }]
    items = reduceRunEvent(items, { type: 'done' })
    expect(items[0]).toMatchObject({ streaming: false })
    items = reduceRunEvent(items, { type: 'error', error: { message: 'failed' } })
    expect(items.at(-1)).toMatchObject({ kind: 'error', content: 'failed' })
  })

  it('紧急停止会固化部分正文并结束仍在运行的工具卡片', () => {
    const items = reduceRunEvent([
      { id: 'u1', kind: 'message', role: 'user', content: '开始执行' },
      { id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', status: 'running' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '部分结果', streaming: true },
    ], {
      type: 'done',
      metadata: {
        status: 'cancelled',
        cancelled: true,
        text: '部分结果\n\n[本轮已由用户紧急停止]',
      },
    })

    expect(items[1]).toMatchObject({ kind: 'tool', status: 'error' })
    expect(items[2]).toMatchObject({
      kind: 'message',
      role: 'assistant',
      streaming: false,
      content: '部分结果\n\n[本轮已由用户紧急停止]',
    })
  })

  it('done 生成可持久化的逐轮统计卡片', () => {
    const items = reduceRunEvent([], { type: 'done', usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, provider_request_count: 2 }, metadata: { elapsed_ms: 35, tool_calls: 1 } })
    expect(items[0]).toMatchObject({ kind: 'usage', elapsedMs: 35, toolCalls: 1, providerRequestCount: 2, usage: { total_tokens: 12 } })
  })
})
