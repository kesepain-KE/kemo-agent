import { cardHistory } from '../test/cardHistory'
import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { archiveTerminalPlansInConversation, buildHistoryItems, buildScheduledTaskItems, buildSenseDataItems, buildUserMessageMarkers, compactPlanAssistantText, createDeltaEventBatcher, executeStopRequest, extractPlanSummary, finalizeCurrentRoundItems, formatSenseUpdateInterval, groupConversationItems, isFailedRunCompletion, isNearScrollBottom, isRetryAttemptProgress, isSuccessfulRunCompletion, mediaArtifactUrl, mergeHistoryPages, partitionAssistantTurnItems, prepareRunUserMessage, reduceRunEvent, removeSubmittedUploads, resetCurrentRoundItemsForRetry, resolveHistoryUserMessages, selectDockedPlan } from './ChatPage'
import type { ChatItem, CronTaskSummary, HistoryResponse, MediaArtifact, PlanSummary, RunEvent, SenseSourceSummary } from '../types/api'
import { resetProviderResponseForRetry } from './chatState'
import { splitInlineWidgets } from '../components/Chat/inlineWidgetProtocol'
import { shouldPrefetchEarlierHistory } from './chatHistory'

describe('正文内联组件消息链路', () => {
  const incompleteWidget = '分析如下：\n\n```kemo-widget\n{"schema_version":1,"id":"retry-widget"'

  it('历史恢复原样保留组件声明，由正文渲染层重新解析', () => {
    const widget = '```kemo-widget\n{"schema_version":1,"id":"history-widget","component":"metric-grid","props":{"items":[{"label":"通过","value":12}]}}\n```'
    const items = buildHistoryItems(cardHistory({
      user: 'alice', source: 'web', session_id: 's1',
      messages: [{ role: 'user', content: '查看摘要' }, { role: 'assistant', content: widget }],
      integrations: [], integration_traces: [],
    }))

    expect(items.find((item) => item.kind === 'message' && item.role === 'assistant')).toMatchObject({ content: widget })
    expect(splitInlineWidgets(widget)).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'widget', complete: true })]))
  })

  it('重试快照不会把未闭合组件与下一次尝试拼接', () => {
    const retried = resetCurrentRoundItemsForRetry([
      { id: 'u1', kind: 'message', role: 'user', content: '生成组件' },
      { id: 'a1', kind: 'message', role: 'assistant', content: incompleteWidget, streaming: true },
    ], 1, 2)
    const snapshot = retried.find((item) => item.kind === 'message' && item.role === 'assistant')

    expect(snapshot).toMatchObject({ content: incompleteWidget, streaming: false })
    if (!snapshot || snapshot.kind !== 'message') throw new Error('retry snapshot missing')
    expect(splitInlineWidgets(snapshot.content)).toEqual(expect.arrayContaining([expect.objectContaining({ type: 'widget', complete: false })]))
    expect(retried).toEqual(expect.arrayContaining([expect.objectContaining({ kind: 'retry_boundary', phase: 'active', attempt: 2 })]))
  })

  it('编辑重发后从聊天历史中排除整轮已撤回的原始卡片', () => {
    const history = cardHistory({
      user: 'alice', source: 'web', session_id: 's1',
      messages: [
        { role: 'user', content: '保留的问题' },
        { role: 'assistant', content: '保留的回答' },
        { role: 'user', content: '已编辑的问题' },
        { role: 'assistant', content: '旧回答' },
      ],
      integrations: [
        { integration_id: 'i_1', integration_number: 1 },
        { integration_id: 'i_2', integration_number: 2, withdrawn: true },
      ],
    })
    const messages = buildHistoryItems(history).filter((item) => item.kind === 'message')

    expect(messages).toEqual([
      expect.objectContaining({ role: 'user', content: '保留的问题' }),
      expect.objectContaining({ role: 'assistant', content: '保留的回答' }),
    ])
  })
})

describe('历史轮次预加载', () => {
  it('视口进入已加载最早轮次前两轮时自动预取更早历史', () => {
    expect(shouldPrefetchEarlierHistory(18, 20, true)).toBe(true)
    expect(shouldPrefetchEarlierHistory(17, 20, true)).toBe(true)
    expect(shouldPrefetchEarlierHistory(23, 20, true)).toBe(false)
    expect(shouldPrefetchEarlierHistory(20, 20, false)).toBe(false)
  })
})

describe('reduceRunEvent', () => {
  it('同一响应中的 commentary 和 final 消息分别收集，只把后端确认的最终正文放到正文区', () => {
    let items = reduceRunEvent([], {
      type: 'text_delta', response_id: 'resp_one', item_id: 'msg_commentary', content: '正在核对',
    })
    items = reduceRunEvent(items, {
      type: 'text_delta', response_id: 'resp_one', item_id: 'msg_final', content: '最终结果',
    })
    items = reduceRunEvent(items, { type: 'done', metadata: {
      status: 'completed', text: '最终结果', final_item_ids: ['msg_final'],
    } })
    expect(items.filter((item) => item.kind === 'message')).toEqual([
      expect.objectContaining({ content: '正在核对', level: 'interim', streaming: false }),
      expect.objectContaining({ content: '最终结果', level: 'final', streaming: false }),
    ])
  })

  it('不同响应复用 Item ID 也不会把两次正文拼接', () => {
    let items = reduceRunEvent([], {
      type: 'text_delta', response_id: 'resp_first', item_id: 'msg_body', content: '阶段性说明',
    })
    items = reduceRunEvent(items, {
      type: 'text_delta', response_id: 'resp_second', item_id: 'msg_body', content: '最终结果',
    })
    items = reduceRunEvent(items, { type: 'done', metadata: {
      status: 'completed', text: '最终结果', final_item_ids: ['msg_body'],
    } })
    expect(items.filter((item) => item.kind === 'message')).toHaveLength(2)
    expect(items.at(-1)).toMatchObject({ content: '最终结果', level: 'final' })
  })

  it.each(['partial', 'failed', 'cancelled', 'limited'])('%s 收尾不把阶段性正文标成最终正文', (status) => {
    const items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '执行任务' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '开始执行', streaming: true },
      { id: 't1', kind: 'tool', callId: 'tool_one', name: 'shell', status: 'running' },
    ]
    const result = reduceRunEvent(items, { type: 'done', metadata: {
      status, unfinished_tasks: [{ tool_call_id: 'tool_one', execution_live: true }],
    } })
    expect(result.some((item) => item.kind === 'message' && item.level === 'final')).toBe(false)
    expect(result.find((item) => item.kind === 'tool')).toMatchObject({ status: 'still_running' })
  })

  it('正常完成时从终态补齐未通过正文增量发送的拒绝答复，不把旧正文当成最终交付', () => {
    const items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '执行任务' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '正在检查', providerItemId: 'msg_old' },
    ]
    const result = reduceRunEvent(items, { type: 'done', metadata: {
      status: 'completed', text: '无法执行该操作', final_item_ids: ['msg_refusal'],
    } })
    expect(result[1]).toMatchObject({ content: '正在检查', level: 'interim' })
    expect(result.at(-1)).toMatchObject({ content: '无法执行该操作', level: 'final', streaming: false })
  })

  it('重试成功收到下一次尝试的首个事件后清除重试气泡', () => {
    expect(isRetryAttemptProgress({ type: 'reasoning_delta', content: '新尝试' })).toBe(true)
    expect(isRetryAttemptProgress({ type: 'tool_call_start', tool_name: 'file' })).toBe(true)
    expect(isRetryAttemptProgress({ type: 'retrying', content: '等待重试' })).toBe(false)
    expect(isRetryAttemptProgress({ type: 'error', metadata: { retryable: true, committed: false } })).toBe(false)
  })

  it('批量合并高频流式增量，并允许非增量事件到达前同步冲刷', () => {
    vi.useFakeTimers()
    try {
      const batches: RunEvent[][] = []
      const batcher = createDeltaEventBatcher((events) => batches.push(events), 80)
      for (let index = 0; index < 100; index += 1) {
        batcher.push({ type: 'text_delta', content: String(index) })
      }
      expect(batches).toHaveLength(0)
      vi.advanceTimersByTime(79)
      expect(batches).toHaveLength(0)
      vi.advanceTimersByTime(1)
      expect(batches).toHaveLength(1)
      expect(batches[0]).toHaveLength(100)

      batcher.push({ type: 'reasoning_delta', content: '等待工具前冲刷' })
      batcher.flush()
      expect(batches).toHaveLength(2)
      expect(batches[1][0].type).toBe('reasoning_delta')
      batcher.dispose()
    } finally {
      vi.useRealTimers()
    }
  })

  it('上下文压缩事件更新运行中的压缩状态记录', () => {
    let items = reduceRunEvent([], {
      type: 'context_compression',
      content: '正在压缩对话上下文',
      metadata: {
        status: 'started', run_id: 'run-1', trigger: 'occupancy',
        history_tokens_before: 80, absorbed_cards: 60, history_tokens_after: 20,
        memory_mode: 'background',
      },
    })
    items = reduceRunEvent(items, {
      type: 'context_compression',
      content: '对话上下文摘要已就绪',
      metadata: {
        status: 'ready', run_id: 'run-1', trigger: 'occupancy',
        history_tokens_before: 80, absorbed_cards: 60, history_tokens_after: 20,
        memory_mode: 'background', memory_status: 'queued_after_commit',
      },
    })
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({
      kind: 'context_compression', status: 'ready', runId: 'run-1',
      historyTokensBefore: 80, absorbedCards: 60, historyTokensAfter: 20,
    })
  })

  it('确认撤回后沿用服务器返回的历史轮次基线', () => {
    const undone = { sessionId: 'session-1', remainingRounds: 4 }
    expect(resolveHistoryUserMessages('session-1', 'session-1', 5, undefined, null, undone)).toBe(4)
    expect(resolveHistoryUserMessages('session-1', 'session-1', 1, undefined, null, { sessionId: 'session-1', remainingRounds: 0 })).toBe(0)
    expect(resolveHistoryUserMessages('session-1', 'session-1', 5, undefined, { sessionId: 'session-1', remainingRounds: 4 }, undone)).toBe(4)
    expect(resolveHistoryUserMessages('session-2', 'session-1', 5, undefined, null, undone)).toBe(0)
    expect(resolveHistoryUserMessages('session-1', 'session-1', 5, 3, null, undone)).toBe(3)
  })

  it('停止请求返回成功状态，并把失败交给统一处理器', async () => {
    const onFailure = vi.fn()
    await expect(executeStopRequest(() => Promise.resolve(), onFailure)).resolves.toBe(true)
    expect(onFailure).not.toHaveBeenCalled()

    const error = new Error('cancel failed')
    await expect(executeStopRequest(() => Promise.reject(error), onFailure)).resolves.toBe(false)
    expect(onFailure).toHaveBeenCalledWith(error)
  })

  it('成功终态只清除本轮实际发送的附件，并保留运行期间新上传的文件', () => {
    const sent = { path: 'accounts.json', name: 'accounts.json', size: 12 }
    const uploadedDuringRun = { path: 'next.zip', name: 'next.zip', size: 24 }
    expect(removeSubmittedUploads([sent, uploadedDuringRun], [sent])).toEqual([uploadedDuringRun])
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'completed' } })).toBe(true)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'limited' } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'cancelled' } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'paused' } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'rejected' } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'done', metadata: { committed: true, status: 'stopped' } })).toBe(false)
    expect(isSuccessfulRunCompletion({ type: 'error' })).toBe(false)
  })

  it('只把最终失败终态判定为失败音效，取消、暂停和受限停止不播放', () => {
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'failed' } })).toBe(true)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'error' } })).toBe(true)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'cancelled' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'cancelling' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'paused' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'pausing' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'done', metadata: { status: 'limited' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'error', metadata: { status: 'interrupted' } })).toBe(false)
    expect(isFailedRunCompletion({ type: 'error', error: { message: 'Provider error' } })).toBe(true)
    expect(isFailedRunCompletion({ type: 'error', metadata: { status: 'failed' }, error: { message: 'Provider error' } })).toBe(true)
  })

  it('用户消息导航只收集真实用户气泡并保留历史绝对轮次', () => {
    const markers = buildUserMessageMarkers([
      { id: 'history_18_user', kind: 'message', role: 'user', content: '第十八轮问题' },
      { id: 'history_execution_19', kind: 'execution_marker', planId: 'plan_1' },
      { id: 'history_19_assistant_1', kind: 'message', role: 'assistant', content: '执行结果' },
      { id: 'user_live', kind: 'message', role: 'user', content: '最新问题' },
    ], 18)

    expect(markers).toEqual([
      { id: 'history_18_user', content: '第十八轮问题', integration_number: 18 },
      { id: 'user_live', content: '最新问题', integration_number: 20 },
    ])
  })

  it('完整发言索引与正文页按卡片去重，保留重复文本、纯附件和最新实时发言', () => {
    const markers = buildUserMessageMarkers([
      { id: 'history_new_user', kind: 'message', role: 'user', content: '重复问题', integrationNumber: 18 },
      { id: 'history_execution_19', kind: 'execution_marker', planId: 'plan' },
      { id: 'guide', kind: 'guidance', content: '过程引导', status: 'completed' },
      { id: 'live', kind: 'message', role: 'user', content: '最新问题' },
    ], 18, [
      { card_id: 'old', seq: 1, integration_number: 1, role: 'user', content: '重复问题' },
      { card_id: 'attachment', seq: 3, integration_number: 2, role: 'user', content: '',
        attachments: [{ asset_id: 'doc', name: 'notes.txt', media_kind: 'file', mime_type: 'text/plain',
          size: 0, checksum_sha256: '', scope: 'external', relative_path: '', available: false }] },
      { card_id: 'new', seq: 35, integration_number: 18, role: 'user', content: '重复问题' },
    ])
    expect(markers).toEqual([
      { id: 'history_old_user', content: '重复问题', integration_number: 1 },
      { id: 'history_attachment_user', content: '[附件] notes.txt', integration_number: 2 },
      { id: 'history_new_user', content: '重复问题', integration_number: 18 },
      { id: 'live', content: '最新问题', integration_number: 20 },
    ])
  })

  it('历史用户消息保留附件元数据，纯附件消息导航使用文件名', () => {
    const attachment = {
      asset_id: 'asset_history_image',
      name: 'board.png',
      media_kind: 'image' as const,
      mime_type: 'image/png',
      size: 128,
      checksum_sha256: 'abc',
      scope: 'file_upload' as const,
      relative_path: 'board.png',
      available: true,
    }
    const items = buildHistoryItems(cardHistory({
      user: 'kesepain', source: 'web', session_id: 's1',
      messages: [
        { role: 'user', content: '', attachments: [attachment] },
        { role: 'assistant', content: '已识别图片' },
      ],
      integrations: [], integration_traces: [],
    }))

    expect(items[0]).toMatchObject({
      id: 'history_card_s1_1_user', kind: 'message', role: 'user', content: '',
      attachments: [attachment],
    })
    expect(buildUserMessageMarkers(items)).toEqual([
      { id: 'history_card_s1_1_user', content: '[附件] board.png', integration_number: 1 },
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
    expect(selectDockedPlan([plan('failed', 'failed')])).toBeUndefined()
  })

  it('终态计划迁移到对应执行轮次并保持统计、计划、引导顺序', () => {
    const pendingPlan: PlanSummary = {
      plan_id: 'plan_12345678', title: '验收计划', description: '', status: 'pending', auto_accept: false, reminder: '', source: 'web', session_id: 's1',
      current_step: 'step_1', revision: 1, created_at: '', updated_at: '', progress: { completed: 0, total: 1, percent: 0 },
      steps: [{ step_id: 'step_1', title: '执行', description: '', status: 'pending', depends_on: [], critical: true, tool_name: '', started_at: '', finished_at: '' }],
    }
    const completedPlan: PlanSummary = {
      ...pendingPlan,
      status: 'completed',
      revision: 4,
      progress: { completed: 1, total: 1, percent: 100 },
      steps: [{ ...pendingPlan.steps[0], status: 'completed', finished_at: '2026-07-26T12:00:00+08:00' }],
    }
    const items: ChatItem[] = [
      { id: 'user-create', kind: 'message', role: 'user', content: '创建计划' },
      { id: 'plan-create', kind: 'task_plan', plan: pendingPlan },
      { id: 'assistant-create', kind: 'message', role: 'assistant', content: '新计划已生成：完整步骤' },
      { id: 'usage-create', kind: 'usage', usage: { total_tokens: 10 } },
      { id: 'execute-plan', kind: 'execution_marker', planId: pendingPlan.plan_id },
      { id: 'assistant-result', kind: 'message', role: 'assistant', content: '计划执行完成' },
      { id: 'guidance-result', kind: 'guidance', content: '继续检查结果', status: 'completed', finalized: true },
      { id: 'usage-result', kind: 'usage', usage: { total_tokens: 20 } },
    ]

    const archived = archiveTerminalPlansInConversation(items, [pendingPlan, completedPlan])
    const blocks = groupConversationItems(archived)
    const creationTurn = blocks[1]
    const executionTurn = blocks[2]
    expect(creationTurn.kind).toBe('assistant')
    expect(executionTurn.kind).toBe('assistant')
    if (creationTurn.kind !== 'assistant' || executionTurn.kind !== 'assistant') throw new Error('expected assistant turns')
    expect(creationTurn.items.find((item) => item.kind === 'task_plan')).toMatchObject({ presentation: 'reference' })
    expect(partitionAssistantTurnItems(creationTurn.items).planItems).toHaveLength(0)
    expect(creationTurn.items.some((item) => item.kind === 'task_plan')).toBe(true)

    const sections = partitionAssistantTurnItems(executionTurn.items)
    expect(sections.usageItems.map((item) => item.id)).toEqual(['usage-result'])
    expect(sections.planItems).toHaveLength(1)
    expect(sections.planItems[0]).toMatchObject({ plan: { plan_id: pendingPlan.plan_id, status: 'completed' }, presentation: 'record' })
    expect(sections.finalizedGuidance.map((item) => item.id)).toEqual(['guidance-result'])
  })

  it('消息流中的计划详情压缩为轻量确认文本', () => {
    expect(compactPlanAssistantText('新计划已生成：\n\n| 步骤 | 操作 |\n| --- | --- |', true)).toBe('任务计划已创建，请在左侧计划卡片中查看并确认。')
    expect(compactPlanAssistantText('普通回复正文', true)).toBe('普通回复正文')
    expect(compactPlanAssistantText('新计划已生成：完整步骤', false)).toBe('新计划已生成：完整步骤')
  })

  it('最近活动只保留用户未完成定时任务', () => {
    const base: CronTaskSummary = {
      task_id: 'user-task', title: '用户任务', user_defined: true, status: 'enabled', type: 'daily', time: '18:00',
      next_run_at: '2026-07-20T18:00:00+08:00', latest_run_at: '', created_at: '2026-07-20T12:00:00+08:00', last_state: 'never',
    }
    const items = buildScheduledTaskItems([
      base,
      { ...base, task_id: 'completed-task', title: '已完成单次任务', status: 'completed', type: 'once', next_run_at: '', last_state: 'completed' },
      { ...base, task_id: 'cancelled-task', title: '已取消任务', status: 'cancelled' },
      { ...base, task_id: 'disabled-task', title: '已停用任务', status: 'disabled' },
      { ...base, task_id: 'system-task', title: '系统维护', user_defined: false },
    ])
    expect(items.map((item) => item.title)).toEqual(['用户任务'])
    expect(items[0]).toMatchObject({ id: 'user-task', status: 'enabled' })
  })

  it('最近活动只保留本轮实际注入的感知来源', () => {
    const base: SenseSourceSummary = {
      id: 'active', name: 'active', display_name: '运行时感知', description: '', layer: 'global', enabled: true,
      whitelisted: true, active_for_main_agent: true, status: 'active', data_md: 'sense.md', recent_update: '2026-07-20 12:00:00',
      health: '正常', valid: true, error: '', start_update: '', files: 1, registered_items: 1, injected_items: 1,
      data_items: ['sense.md'], value_preview: 'CPU 23%', collected_markdown: 'CPU 23%', injected_markdown: '[active]\nCPU 23%', injected_tokens: 5, update_interval: '旧字段', update_interval_seconds: 5, updated_at: 1,
    }
    const items = buildSenseDataItems([
      base,
      { ...base, id: 'not-injected', injected_items: 0 },
      { ...base, id: 'filtered', active_for_main_agent: false, status: 'filtered' },
    ])
    expect(items).toHaveLength(1)
    expect(items[0]).toMatchObject({ id: 'active', value: 'CPU 23%', updateInterval: '每 5 秒', injected: true })
    expect(formatSenseUpdateInterval(3600)).toBe('每 1 小时')
    expect(formatSenseUpdateInterval(90)).toBe('每 90 秒')
    expect(formatSenseUpdateInterval(0)).toBe('')
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
    const items = buildHistoryItems(cardHistory({
      user: 'kesepain',
      source: 'web',
      session_id: 's1',
      messages: [
        { role: 'user', content: '【任务计划连续执行】\n计划 ID：plan_12345678\n起始步骤：step_1' },
        { role: 'assistant', content: '计划已经执行完成。' },
      ],
      integrations: [],
      integration_traces: [],
    }))

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

  it('连续工具调用期间复用同一个思考卡片，最终正文结束该卡片', () => {
    let items: ChatItem[] = []
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '先检查。' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '再判断。' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c2', tool_name: 'shell' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c2', tool_name: 'shell', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '最后核对。' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c3', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c3', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'text_delta', content: '最终正文' })
    items = reduceRunEvent(items, { type: 'done', metadata: { status: 'completed' }, usage: { total_tokens: 1 } })

    expect(items.map((item) => item.kind)).toEqual([
      'reasoning', 'tool', 'tool', 'tool', 'message', 'usage',
    ])
    expect(items[0]).toMatchObject({
      kind: 'reasoning',
      content: '先检查。再判断。最后核对。',
      streaming: false,
    })
    expect(items[4]).toMatchObject({ kind: 'message', content: '最终正文', streaming: false, level: 'final' })
  })

  it('临时正文结束前一思考组，后续工具共享新的思考组', () => {
    let items: ChatItem[] = []
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '第一段思考' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'text_delta', content: '临时正文' })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '第二段思考' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c2', tool_name: 'shell' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c2', tool_name: 'shell', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c3', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c3', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'text_delta', content: '最终正文' })
    items = reduceRunEvent(items, { type: 'done', metadata: { status: 'completed' }, usage: { total_tokens: 1 } })

    expect(items.map((item) => item.kind)).toEqual([
      'reasoning', 'tool', 'message', 'reasoning', 'tool', 'tool', 'message', 'usage',
    ])
    expect(items[0]).toMatchObject({
      kind: 'reasoning', content: '第一段思考', streaming: false,
    })
    expect(items[2]).toMatchObject({
      kind: 'message', content: '临时正文', streaming: false, level: 'interim',
    })
    expect(items[3]).toMatchObject({
      kind: 'reasoning', content: '第二段思考', streaming: false,
    })
    expect(items[6]).toMatchObject({ kind: 'message', content: '最终正文', streaming: false, level: 'final' })
  })

  it('按 tool_call_id 配对开始和结果', () => {
    let items: ChatItem[] = reduceRunEvent([], { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'time', arguments: { zone: 'local' } })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'time', result: { ok: false }, metadata: { status: 'failed', elapsed_ms: 12 } })
    expect(items[0]).toMatchObject({ kind: 'tool', callId: 'c1', status: 'error', result: { ok: false }, elapsedMs: 12 })
  })

  it.each(['timed_out_running', 'failed'])('保留超时后未确认退出的工具回执，不随响应结束改成失败：%s', (status) => {
    const receipt = { ok: false, error: { message: '线程尚未退出', still_running: true } }
    const items = reduceRunEvent(
      reduceRunEvent([], { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'file' }),
      { type: 'tool_call_result', tool_call_id: 'c1', result: receipt, metadata: { status, elapsed_ms: 300000 } },
    )
    expect(items[0]).toMatchObject({ status: 'still_running', result: receipt, elapsedMs: 300000 })
    for (const event of [
      { type: 'done', metadata: { status: 'limited' } },
      { type: 'error', error: { message: '连接断开' } },
    ] satisfies RunEvent[]) {
      expect(reduceRunEvent(items, event)[0]).toMatchObject({ status: 'still_running', result: receipt })
    }
    expect(finalizeCurrentRoundItems(items, { message: '响应结束' })[0]).toMatchObject({
      status: 'still_running', result: receipt,
    })
  })

  it('历史恢复保留超时后未确认退出，已确认终止的超时仍为失败', () => {
    const items = buildHistoryItems(cardHistory({
      user: 'alice', source: 'web', session_id: 's1',
      messages: [{ role: 'user', content: '执行工具' }, { role: 'assistant', content: '部分完成' }],
      integrations: [],
      integration_traces: [{
        integration_number: 1, reasoning: '', tools: [
          { call_id: 'c1', name: 'file', status: 'timed_out_running', arguments_text: '{}', arguments_truncated: false, result_text: '{"ok":false}', result_truncated: false, elapsed_ms: 300000 },
          { call_id: 'c2', name: 'subagent_dispatch', status: 'failed', arguments_text: '{}', arguments_truncated: false, result_text: '{"ok":false,"error":{"timeout":true}}', result_truncated: false, elapsed_ms: 1000 },
        ],
      }],
    }))
    expect(items.filter((item) => item.kind === 'tool')).toMatchObject([
      { name: 'file', status: 'still_running' }, { name: 'subagent_dispatch', status: 'error' },
    ])
  })

  it('新 Run 的正文、思考和同名工具结果不会写回旧边界', () => {
    let items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '第一次请求' },
      { id: 'r1', kind: 'reasoning', content: '旧思考', streaming: true },
      { id: 't1', kind: 'tool', callId: 'shared-call', name: 'file', status: 'running' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '旧正文', streaming: true },
      { id: 'e1', kind: 'error', content: '连接中断' },
      { id: 'u2', kind: 'message', role: 'user', content: '重新发送' },
    ]

    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '新思考' })
    items = reduceRunEvent(items, { type: 'text_delta', content: '新正文' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'shared-call', tool_name: 'shell' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'shared-call', tool_name: 'shell', result: { ok: true } })

    expect(items.find((item) => item.id === 'r1')).toMatchObject({ content: '旧思考', streaming: true })
    expect(items.find((item) => item.id === 'a1')).toMatchObject({ content: '旧正文', streaming: true })
    expect(items.find((item) => item.id === 't1')).toMatchObject({ status: 'running', name: 'file' })
    const currentItems = items.slice(items.findIndex((item) => item.id === 'u2') + 1)
    expect(currentItems).toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: 'reasoning', content: '新思考' }),
      expect.objectContaining({ kind: 'tool', callId: 'shared-call', name: 'shell', status: 'success' }),
      expect.objectContaining({ kind: 'message', role: 'assistant', content: '新正文' }),
    ]))
  })

  it('异常终态只固化当前边界并结束当前运行中工具', () => {
    const items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '旧请求' },
      { id: 'r1', kind: 'reasoning', content: '旧残留', streaming: true },
      { id: 'u2', kind: 'message', role: 'user', content: '当前请求' },
      { id: 'r2', kind: 'reasoning', content: '当前思考', streaming: true },
      { id: 't2', kind: 'tool', callId: 'call-2', name: 'shell', status: 'running' },
      { id: 'a2', kind: 'message', role: 'assistant', content: '当前正文', streaming: true },
    ]

    const finalized = finalizeCurrentRoundItems(items, {
      message: '响应中断',
      exception_type: 'ClientStreamError',
    })

    expect(finalized.find((item) => item.id === 'r1')).toMatchObject({ streaming: true })
    expect(finalized.find((item) => item.id === 'r2')).toMatchObject({ streaming: false })
    expect(finalized.find((item) => item.id === 'a2')).toMatchObject({ streaming: false })
    expect(finalized.find((item) => item.id === 't2')).toMatchObject({
      status: 'error',
      result: { ok: false, error: { exception_type: 'ClientStreamError' } },
    })
  })

  it('自动重试时保留失败尝试快照并开启新的尝试边界', () => {
    const userItem: Extract<ChatItem, { kind: 'message' }> = {
      id: 'u1', kind: 'message', role: 'user', content: '执行任务',
    }
    const items: ChatItem[] = [
      userItem,
      { id: 'r1', kind: 'reasoning', content: '旧思考', streaming: true },
      { id: 't1', kind: 'tool', callId: 'c1', name: 'shell', status: 'error' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '旧正文', streaming: false },
      { id: 'e1', kind: 'error', content: '旧错误' },
    ]

    const retried = resetCurrentRoundItemsForRetry(items, 1, 2, 7)
    expect(retried).toEqual([
      userItem,
      { id: 'retry_boundary_u1_7_snapshot', kind: 'retry_boundary', attempt: 1, phase: 'snapshot' },
      { id: 'r1', kind: 'reasoning', content: '旧思考', streaming: false },
      { id: 't1', kind: 'tool', callId: 'c1', name: 'shell', status: 'error' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '旧正文', streaming: false },
      { id: 'e1', kind: 'error', content: '旧错误' },
      { id: 'retry_boundary_u1_7_active', kind: 'retry_boundary', attempt: 2, phase: 'active' },
    ])

    const nextAttempt = reduceRunEvent(retried, {
      type: 'tool_call_start',
      tool_call_id: 'c1',
      tool_name: 'shell',
      arguments: { value: 'new' },
    })
    expect(nextAttempt.filter((item) => item.kind === 'tool')).toHaveLength(2)
    expect(nextAttempt.at(-1)).toMatchObject({ kind: 'tool', callId: 'c1', status: 'running' })
  })

  it('模型请求恢复只封存中断正文，保留仍在执行的工具和子任务', () => {
    const runningTool: ChatItem = { id: 'tool-live', kind: 'tool', callId: 'call-live', name: 'shell', status: 'running' }
    const progress: ChatItem = { id: 'child-live', kind: 'subagent_progress', callId: 'child', agent: 'worker', status: 'running', iteration: 1, toolName: '', toolCount: 0, nextAttempt: 0 }
    const items: ChatItem[] = [
      { id: 'u1', kind: 'message', role: 'user', content: '继续任务' },
      runningTool,
      progress,
      { id: 'partial', kind: 'message', role: 'assistant', content: '中断的片段', streaming: true },
    ]
    const retried = resetProviderResponseForRetry(items, {
      type: 'retrying', metadata: { phase: 'provider', partial_response: true,
        failed_request_id: 'req_failed', failed_attempt: 2, next_attempt: 3 },
    })
    expect(retried.find(item => item.id === runningTool.id)).toBe(runningTool)
    expect(retried.find(item => item.id === progress.id)).toBe(progress)
    expect(retried.find(item => item.id === 'partial')).toMatchObject({ streaming: false })
    const next = reduceRunEvent(retried, { type: 'text_delta', content: '恢复后的正文' })
    expect(next.at(-1)).toMatchObject({ content: '恢复后的正文', streaming: true })
    expect(next.find(item => item.id === 'partial')).toMatchObject({ content: '中断的片段' })
  })

  it('没有部分输出的模型重试不改动现有对话项', () => {
    const items: ChatItem[] = [{ id: 'u1', kind: 'message', role: 'user', content: '继续任务' }]
    expect(resetProviderResponseForRetry(items, {
      type: 'retrying', metadata: { phase: 'provider', partial_response: false },
    })).toBe(items)
  })

  it('Provider error 事件统一收束当前思考、正文和工具', () => {
    const items = reduceRunEvent([
      { id: 'u1', kind: 'message', role: 'user', content: '执行任务' },
      { id: 'r1', kind: 'reasoning', content: '正在分析', streaming: true },
      { id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', status: 'running' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '部分正文', streaming: true },
    ], {
      type: 'error',
      error: { message: 'Provider 连接失败', exception_type: 'ProviderConnectionError' },
    })

    expect(items.find((item) => item.id === 'r1')).toMatchObject({ streaming: false })
    expect(items.find((item) => item.id === 'a1')).toMatchObject({ streaming: false })
    expect(items.find((item) => item.id === 't1')).toMatchObject({
      status: 'error',
      result: { ok: false, error: { exception_type: 'ProviderConnectionError' } },
    })
    expect(items.at(-1)).toMatchObject({ kind: 'error', content: 'Provider 连接失败' })
  })

  it('同一排队消息重试时清除该边界后的失败尝试', () => {
    const userItem: Extract<ChatItem, { kind: 'message' }> = {
      id: 'next_turn_1',
      kind: 'message',
      role: 'user',
      content: '停止后继续',
    }
    const failedAttempt: ChatItem[] = [
      userItem,
      { id: 'r1', kind: 'reasoning', content: '失败思考', streaming: false },
      { id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', status: 'error' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '失败正文', streaming: false },
      { id: 'e1', kind: 'error', content: '发送失败' },
    ]

    expect(prepareRunUserMessage(failedAttempt, userItem, true)).toEqual([userItem])
    expect(prepareRunUserMessage(failedAttempt, userItem, false)).toEqual(failedAttempt)
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

  it('历史中的未投递引导不能在刷新后显示为成功', () => {
    const items = buildHistoryItems(cardHistory({
      user: 'alice', source: 'web', session_id: 'scope',
      messages: [{ role: 'user', content: 'start' },
        { role: 'user', level: 'guidance', integration_number: 1, guidance_id: 'late', content: 'late revision' },
        { role: 'assistant', content: 'done' }],
      integrations: [{
        integration_number: 1, usage: {}, elapsed_ms: 1, tool_calls: 0, guidance: [],
        guidance_details: [{ id: 'late', text: 'late revision', status: 'not_applied', ok: false }],
      }], integration_traces: [],
    }))
    expect(items.find(item => item.kind === 'guidance')).toMatchObject({
      guidanceId: 'late', status: 'not_applied', finalized: true,
    })
  })

  it('终态按 id 区分重复文本的成功与未投递引导', () => {
    const items: ChatItem[] = [
      { id: 'late', guidanceId: 'late', kind: 'guidance', content: 'same', status: 'queued' },
      { id: 'applied', guidanceId: 'applied', kind: 'guidance', content: 'same', status: 'queued' },
    ]
    const result = reduceRunEvent(items, { type: 'done', metadata: {
      guidance_count: 1, guidance_details: [
        { id: 'applied', status: 'applied', ok: true },
        { id: 'late', status: 'not_applied', ok: false },
      ],
    } })
    expect(result[0]).toMatchObject({ status: 'not_applied' })
    expect(result[1]).toMatchObject({ status: 'completed' })
  })

  it('终态有精确 id 明细时不能用计数确认另一个未匹配的引导', () => {
    const items: ChatItem[] = [
      { id: 'late', guidanceId: 'late', kind: 'guidance', content: 'same', status: 'queued' },
      { id: 'used', guidanceId: 'used', kind: 'guidance', content: 'same', status: 'queued' },
    ]
    const result = reduceRunEvent(items, { type: 'done', metadata: {
      guidance_count: 1, guidance_details: [{ id: 'used', status: 'applied', ok: true }],
    } })
    expect(result[0]).toMatchObject({ status: 'not_applied' })
    expect(result[1]).toMatchObject({ status: 'completed' })
  })

  it('最终 error 中明确未投递的明细优先于前一次尝试的 accepted 状态', () => {
    const items: ChatItem[] = [
      { id: 'late', guidanceId: 'late', kind: 'guidance', content: 'revision', status: 'accepted' },
    ]
    const result = reduceRunEvent(items, { type: 'error', error: { message: 'offline' }, metadata: {
      committed: true, guidance_details: [{ id: 'late', status: 'not_applied', ok: false }],
    } })
    expect(result[0]).toMatchObject({ status: 'not_applied', finalized: true })
  })

  it('按 guidance id 确认重复文本的多媒体引导并保留附件', () => {
    const attachment = {
      asset_id: 'asset_video', name: 'clip.mp4', media_kind: 'video' as const,
      mime_type: 'video/mp4', size: 12, checksum_sha256: '',
      scope: 'file_upload' as const, relative_path: 'clip.mp4', available: true,
    }
    const items: ChatItem[] = [
      { id: 'one', guidanceId: 'guidance_one', kind: 'guidance', content: '继续', status: 'queued' },
      { id: 'two', guidanceId: 'guidance_two', kind: 'guidance', content: '继续', status: 'queued' },
    ]

    const reduced = reduceRunEvent(items, {
      type: 'guidance_applied',
      metadata: {
        guidance: ['继续'],
        guidance_details: [{ id: 'guidance_two', text: '继续', uploaded_files: [attachment] }],
      },
    })

    expect(reduced[0]).toMatchObject({ status: 'queued' })
    expect(reduced[1]).toMatchObject({ status: 'accepted', attachments: [attachment] })
  })

  it('按到达顺序保留阶段正文、工具和后续思考', () => {
    let items: ChatItem[] = [{ id: 'u1', kind: 'message', role: 'user', content: '请处理' }]
    items = reduceRunEvent(items, { type: 'text_delta', content: '最终正文' })
    items = reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: 'c1', tool_name: 'file' })
    items = reduceRunEvent(items, { type: 'reasoning_delta', content: '先分析问题' })
    items = reduceRunEvent(items, { type: 'tool_call_result', tool_call_id: 'c1', tool_name: 'file', result: { ok: true } })
    items = reduceRunEvent(items, { type: 'done', usage: { total_tokens: 12 } })

    expect(items.map((item) => item.kind)).toEqual(['message', 'message', 'tool', 'reasoning', 'usage'])
    expect(items[1]).toMatchObject({ kind: 'message', role: 'assistant', content: '最终正文', streaming: false })
    expect(items[2]).toMatchObject({ kind: 'tool', callId: 'c1', status: 'success' })
    expect(items[3]).toMatchObject({ kind: 'reasoning', content: '先分析问题', streaming: false })
  })

  it('从已提交历史恢复思考按钮和始终可见的工具卡片', () => {
    const items = buildHistoryItems(cardHistory({
      user: 'kesepain',
      source: 'web',
      session_id: 's1',
      messages: [
        { role: 'user', content: '检查状态' },
        { role: 'assistant', content: '检查完成' },
      ],
      integrations: [{ integration_number: 1, usage: { total_tokens: 12 }, elapsed_ms: 20, tool_calls: 1, guidance: [] }],
      integration_traces: [{
        integration_number: 1,
        reasoning: '先分析',
        tools: [{
          call_id: 'c1', name: 'shell', status: 'success', elapsed_ms: 12,
          arguments_text: '{"command":"status"}', arguments_truncated: false,
          result_text: 'ok', result_truncated: false,
        }],
      }],
    }))

    expect(items.map((item) => item.kind)).toEqual(['message', 'reasoning', 'tool', 'message', 'usage'])
    expect(items[1]).toMatchObject({ kind: 'reasoning', content: '先分析', streaming: false })
    expect(items[2]).toMatchObject({ kind: 'tool', callId: 'c1', argumentsText: '{"command":"status"}', resultText: 'ok' })
  })

  it('历史恢复时仅在助手正文出现后分割思考组', () => {
    const integrationId = 'i_1'
    const createdAt = '2026-11-04T00:00:00Z'
    const assistantCard = (
      seq: number,
      content: string,
      reasoning: string,
      level: 'interim' | 'final' = 'interim',
    ): HistoryResponse['cards'][number] => ({
      card_id: `card_${seq}`,
      integration_id: integrationId,
      seq,
      raw_kind: 'agent_text',
      raw_level: level,
      created_at: createdAt,
      message: { role: 'assistant', content, reasoning },
    })
    const toolCard = (seq: number, callId: string): HistoryResponse['cards'][number] => ({
      card_id: `card_${seq}`,
      integration_id: integrationId,
      seq,
      raw_kind: 'tool_result',
      raw_level: null,
      created_at: createdAt,
      tool: {
        call_id: callId,
        execution_id: `exec_${callId}`,
        seq,
        name: 'file',
        status: 'success',
        elapsed_ms: 10,
        arguments_text: '{}',
        arguments_truncated: false,
        result_text: '{"ok":true}',
        result_truncated: false,
      },
    })
    const cards: HistoryResponse['cards'] = [
      {
        card_id: 'card_1', integration_id: integrationId, seq: 1,
        raw_kind: 'user_text', raw_level: 'initial', created_at: createdAt,
        message: { role: 'user', content: '处理任务' },
      },
      assistantCard(2, '', '第一段思考'),
      toolCard(3, 'c1'),
      assistantCard(4, '', '继续思考'),
      toolCard(5, 'c2'),
      assistantCard(6, '临时正文', ''),
      assistantCard(7, '', '正文后的思考'),
      toolCard(8, 'c3'),
      assistantCard(9, '最终正文', '', 'final'),
    ]
    const items = buildHistoryItems(cardHistory({
      session_id: 'history-thinking-groups',
      cards,
      messages: [],
      integrations: [{
        integration_id: integrationId,
        integration_number: 1,
        response_closed: true,
        last_seq: 9,
        usage: { total_tokens: 3 },
        elapsed_ms: 100,
        tool_calls: 3,
        guidance: [],
      }],
    }))

    expect(items.map((item) => item.kind)).toEqual([
      'message', 'reasoning', 'tool', 'tool', 'message', 'reasoning', 'tool', 'message', 'usage',
    ])
    expect(items[1]).toMatchObject({
      kind: 'reasoning', content: '第一段思考继续思考', streaming: false,
    })
    expect(items[4]).toMatchObject({ kind: 'message', content: '临时正文' })
    expect(items[5]).toMatchObject({ kind: 'reasoning', content: '正文后的思考', streaming: false })
    expect(items[7]).toMatchObject({ kind: 'message', content: '最终正文', level: 'final' })
  })

  it('合并向上分页结果并保留绝对轮次和稳定消息标识', () => {
    const latest = cardHistory({
      user: 'kesepain', source: 'web' as const, session_id: 's1',
      messages: [
        { role: 'user' as const, content: '第 21 轮' },
        { role: 'assistant' as const, content: '第 21 轮回复' },
      ],
      integrations: [{ integration_number: 21, usage: { total_tokens: 21 }, elapsed_ms: 21, tool_calls: 0, guidance: [] }],
      integration_traces: [{ integration_number: 21, reasoning: '思考 21', tools: [] }],
      pagination: { limit: 20, integration_count: 21, first_seq: 61, first_integration_number: 21, last_integration_number: 21, has_more_before: true, next_before: 21 },
    })
    const earlier = cardHistory({
      user: 'kesepain', source: 'web' as const, session_id: 's1',
      messages: [
        { role: 'user' as const, content: '第 1 轮' },
        { role: 'assistant' as const, content: '第 1 轮回复' },
      ],
      integrations: [{ integration_number: 1, usage: { total_tokens: 1 }, elapsed_ms: 1, tool_calls: 0, guidance: [] }],
      integration_traces: [{ integration_number: 1, reasoning: '思考 1', tools: [] }],
      pagination: { limit: 20, integration_count: 21, first_integration_number: 1, last_integration_number: 20, has_more_before: false, next_before: null },
    })

    const merged = mergeHistoryPages([latest, earlier])
    expect(merged?.messages.filter((message) => message.content).map((message) => message.content)).toEqual([
      '第 1 轮', '第 1 轮回复', '第 21 轮', '第 21 轮回复',
    ])
    expect(merged?.pagination).toMatchObject({ integration_count: 21, first_integration_number: 1, last_integration_number: 21, has_more_before: false })
    const items = buildHistoryItems(latest)
    expect(items[0]).toMatchObject({ id: 'history_card_s1_61_user', content: '第 21 轮' })
    expect(items[1]).toMatchObject({ id: 'history_reasoning_card_s1_62', content: '思考 21' })
    expect(items.at(-1)).toMatchObject({ id: 'history_usage_i_21', round: 21 })
  })

  it.each(['required', 'detached'])('迟到的 %s 回执在分页恢复后仍归原轮次统计', (mode) => {
    const history = cardHistory({
      messages: [
        { role: 'user', content: '启动旧任务' },
        { role: 'assistant', content: '旧任务交付' },
        { role: 'user', content: '处理新任务' },
        { role: 'assistant', content: '新任务交付' },
      ],
      integrations: [
        { integration_number: 1, usage: { input_tokens: 11 } },
        { integration_number: 2, usage: { input_tokens: 22 } },
      ],
    })
    const late: HistoryResponse['cards'][number] = {
      card_id: 'late-receipt', integration_id: 'i_1', seq: 5,
      raw_kind: 'tool_result', created_at: '2026-11-08T13:00:00Z',
      // Historical detached exits retain their RAW cursor, without a tool projection.
      ...(mode === 'required' ? { tool: {
        execution_id: 'late-job', call_id: 'old-call', name: 'shell', seq: 5,
        status: 'error', elapsed_ms: 0, arguments_text: '{}', arguments_truncated: false,
        result_text: '{"ok":false}', result_truncated: false,
      } } : {}),
    }
    history.integrations[0] = { ...history.integrations[0], last_seq: 5, initial_card: history.cards[0] }
    const latest = { ...history, cards: [...history.cards.slice(2), late] }
    const earlier = { ...history, cards: history.cards.slice(0, 2) }

    for (const page of [latest, mergeHistoryPages([latest, earlier])]) {
      const replies = groupConversationItems(buildHistoryItems(page))
        .filter((block) => block.kind === 'assistant')
      expect(replies.map((block) => partitionAssistantTurnItems(block.items).usageItems.map((item) => item.round)))
        .toEqual([[1], [2]])
      expect(replies.map((block) => block.items.filter((item) => item.kind === 'tool').map((item) => item.id)))
        .toEqual([mode === 'required' ? ['history_tool_late-job'] : [], []])
    }
  })

  it('长任务只加载末页时仍恢复原始提问，合并包含原始卡片的前页时不重复', () => {
    const initial: HistoryResponse['cards'][number] = {
      card_id: 'long-input', integration_id: 'long', seq: 3, raw_kind: 'user_text', raw_level: 'initial',
      created_at: '2026-11-04T00:00:00Z',
      message: { role: 'user', content: '长任务的原始提问', integration_number: 2 },
    }
    const latest = cardHistory({
      cards: [{ card_id: 'long-final', integration_id: 'long', seq: 426, raw_kind: 'agent_text',
        raw_level: 'final', created_at: '2026-11-04T01:00:00Z',
        message: { role: 'assistant', content: '最终结果' } }],
      integrations: [{ integration_id: 'long', integration_number: 2, last_seq: 426, initial_card: initial }],
      pagination: { first_seq: 426, last_seq: 426, next_before: 426, has_more_before: true },
    })
    const latestItems = buildHistoryItems(latest)
    expect(latestItems.filter((item) => item.kind === 'message')).toMatchObject([
      { id: 'history_long-input_user', role: 'user', content: '长任务的原始提问', integrationNumber: 2 },
      { role: 'assistant', content: '最终结果', level: 'final' },
    ])
    const earlier = cardHistory({ cards: [initial],
      integrations: latest.integrations, pagination: { first_seq: 3, last_seq: 3 } })
    const mergedItems = buildHistoryItems(mergeHistoryPages([latest, earlier]))
    expect(mergedItems.filter((item) => item.kind === 'message' && item.role === 'user')).toHaveLength(1)
    expect(groupConversationItems(mergedItems).at(-1)?.id).toBe(groupConversationItems(latestItems).at(-1)?.id)
  })

  it('加载更早轮次不会改变已显示智能体容器的标识', () => {
    const recent: ChatItem[] = [
      { id: 'recent-user', kind: 'message', role: 'user', content: '当前问题' },
      { id: 'recent-answer', kind: 'message', role: 'assistant', content: '当前回复' },
    ]
    const earlier: ChatItem[] = [
      { id: 'earlier-user', kind: 'message', role: 'user', content: '更早问题' },
      { id: 'earlier-answer', kind: 'message', role: 'assistant', content: '更早回复' },
    ]
    expect(groupConversationItems([...earlier, ...recent]).at(-1)?.id)
      .toBe(groupConversationItems(recent).at(-1)?.id)
  })

  it('done 结束流式标记，error 生成错误项', () => {
    let items: ChatItem[] = [{ id: 'a', kind: 'message', role: 'assistant', content: 'ok', streaming: true }]
    items = reduceRunEvent(items, { type: 'done' })
    expect(items[0]).toMatchObject({ streaming: false })
    items = reduceRunEvent(items, { type: 'error', error: { message: 'failed' } })
    expect(items.at(-1)).toMatchObject({ kind: 'error', content: 'failed' })
  })

  it('任务计划批准边界按成功终态收束，并保留计划卡片和未执行工具结果', () => {
    const planEvent = {
      type: 'tool_call_result' as const,
      tool_call_id: 'create-plan',
      tool_name: 'subagent_dispatch',
      result: {
        ok: true,
        result: {
          status: 'completed',
          agent: 'task_plan',
          data: { action: 'create' },
          plan: {
            plan_id: 'plan_12345678', title: '等待批准', description: '', status: 'pending',
            auto_accept: false, reminder: '', source: 'web', session_id: 'conversation-a',
            current_step: 'step_1', revision: 1, created_at: '', updated_at: '',
            steps: [{ step_id: 'step_1', title: '执行', description: '', status: 'pending', depends_on: [], critical: true }],
          },
        },
      },
      metadata: { status: 'completed' },
    }
    let items = reduceRunEvent([], planEvent)
    items = reduceRunEvent(items, {
      type: 'tool_call_result',
      tool_call_id: 'blocked-shell',
      tool_name: 'shell',
      result: { ok: false, error: { exception_type: 'TaskPlanCreationBoundary' } },
      metadata: { status: 'not_executed' },
    })
    items = reduceRunEvent(items, {
      type: 'text_delta',
      content: '任务计划已创建并等待用户批准；当前运行已在计划边界停止。',
    })
    items = reduceRunEvent(items, {
      type: 'done',
      metadata: {
        committed: true,
        status: 'completed',
        stop_reason: 'task_plan_approval_required',
        awaiting_user_approval: true,
        plan_id: 'plan_12345678',
      },
    })

    expect(items.some((item) => item.kind === 'task_plan' && item.plan.plan_id === 'plan_12345678')).toBe(true)
    expect(items.find((item) => item.kind === 'tool' && item.callId === 'blocked-shell')).toMatchObject({ status: 'error' })
    expect(items.find((item) => item.kind === 'message' && item.role === 'assistant')).toMatchObject({ streaming: false })
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

  it('工具循环受限会保留终止正文并结束尚未执行的工具卡片', () => {
    const items = reduceRunEvent([
      { id: 'u1', kind: 'message', role: 'user', content: '继续读取' },
      { id: 't1', kind: 'tool', callId: 'call-1', name: 'file', status: 'running' },
      { id: 'a1', kind: 'message', role: 'assistant', content: '读取中', streaming: true },
    ], {
      type: 'done',
      metadata: {
        committed: true,
        status: 'limited',
        stop_reason: 'max_tool_iterations',
        text: '读取中\n\n[本轮工具循环已达到最大次数 80，本轮已停止]',
      },
    })

    expect(items[1]).toMatchObject({
      kind: 'tool',
      status: 'error',
      result: { ok: false, error: { exception_type: 'ToolLoopLimitExceeded' } },
    })
    expect(items[2]).toMatchObject({
      kind: 'message',
      role: 'assistant',
      streaming: false,
      content: '读取中\n\n[本轮工具循环已达到最大次数 80，本轮已停止]',
    })
  })

  it('历史中的未执行和拦截工具按错误状态恢复', () => {
    const items = buildHistoryItems(cardHistory({
      user: 'kesepain', source: 'web', session_id: 'limited',
      messages: [
        { role: 'user', content: '开始' },
        { role: 'assistant', content: '[本轮工具循环已达到最大次数 80，本轮已停止]' },
      ],
      integrations: [{ integration_number: 1, usage: {}, elapsed_ms: 1, tool_calls: 2, guidance: [] }],
      integration_traces: [{ integration_number: 1, reasoning: '', tools: [
        { call_id: 'one', name: 'file', status: 'not_executed', elapsed_ms: 0, arguments_text: '{}', arguments_truncated: false, result_text: '{}', result_truncated: false, artifacts: [] },
        { call_id: 'two', name: 'file', status: 'identical_call_blocked', elapsed_ms: 0, arguments_text: '{}', arguments_truncated: false, result_text: '{}', result_truncated: false, artifacts: [] },
      ] }],
      pagination: { limit: 20, integration_count: 1, first_integration_number: 1, last_integration_number: 1, has_more_before: false, next_before: null },
    }))
    expect(items.filter((item) => item.kind === 'tool')).toEqual([
      expect.objectContaining({ callId: 'one', status: 'error' }),
      expect.objectContaining({ callId: 'two', status: 'error' }),
    ])
  })

  it('done 生成可持久化的逐轮统计卡片', () => {
    const items = reduceRunEvent([], { type: 'done', usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12, provider_request_count: 2 }, metadata: { elapsed_ms: 35, tool_calls: 1 } })
    expect(items[0]).toMatchObject({ kind: 'usage', elapsedMs: 35, toolCalls: 1, providerRequestCount: 2, usage: { total_tokens: 12 } })
  })

  it('媒体事件生成稳定产物卡片并按资产与路径去重', () => {
    const event = {
      type: 'media_output' as const,
      result: {
        asset_id: 'asset_output_1', type: 'audio', name: 'answer.mp3',
        scope: 'download', path: 'answer.mp3', mime_type: 'audio/mpeg',
        size: 128, checksum_sha256: 'a'.repeat(64),
      },
    }
    const once = reduceRunEvent([], event)
    const twice = reduceRunEvent(once, event)
    expect(once[0]).toMatchObject({ kind: 'media', artifact: { path: 'answer.mp3', type: 'audio' } })
    expect(twice).toHaveLength(1)
  })

  it('媒体卡片使用校验和路由并保留嵌套路径作为快速定位提示', () => {
    const artifact: MediaArtifact = {
      asset_id: 'asset_output_2',
      type: 'image',
      name: 'generated.png',
      scope: 'download',
      path: 'projects/report/generated.png',
      mime_type: 'image/png',
      size: 1137,
      checksum_sha256: 'b'.repeat(64),
    }

    expect(mediaArtifactUrl('test user', artifact)).toBe(
      `/api/users/test%20user/artifacts/${'b'.repeat(64)}?path=projects%2Freport%2Fgenerated.png&size=1137`,
    )
  })
})
