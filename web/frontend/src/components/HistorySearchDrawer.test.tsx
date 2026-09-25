import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { HistorySearchDrawer } from './HistorySearchDrawer'
import { formatDateTime } from './ModuleUi'

const sessions = [
  { session_id: 's1', window: 'w1', title: '当前工作', state: 'open', rounds: 3, updated_at: '2026-07-22T08:00:00+00:00' },
  { session_id: 's2', window: 'w2', title: '项目复盘', state: 'closed', rounds: 8, updated_at: '2026-07-21T08:00:00+00:00' },
]

describe('HistorySearchDrawer', () => {
  it('默认按更新时间倒序展示历史归档', () => {
    render(<HistorySearchDrawer
      open
      sessions={[
        { session_id: 'older', window: 'older-window', title: '较早对话', state: 'closed', rounds: 1, updated_at: '2026-09-24T08:00:00+00:00' },
        { session_id: 'newer', window: 'newer-window', title: '较新对话', state: 'closed', rounds: 1, updated_at: '2026-09-25T08:00:00+00:00' },
      ]}
      activeSessionId=""
      onClose={() => undefined}
      onSelectSession={() => undefined}
      onDeleteSession={() => undefined}
      onDeleteAllSessions={() => undefined}
      onRetrySummary={() => undefined}
    />)

    expect(screen.getAllByRole('button', { name: /打开对话/ }).map((button) => button.getAttribute('aria-label'))).toEqual([
      '打开对话 较新对话',
      '打开对话 较早对话',
    ])
  })

  it('可在搜索框下方展开月历并选择、清除归档日期', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-09-25T04:00:00+00:00'))
    const onDateChange = vi.fn()
    const props = {
      open: true,
      sessions: [],
      activeSessionId: '',
      onClose: () => undefined,
      onSelectSession: () => undefined,
      onDeleteSession: () => undefined,
      onDeleteAllSessions: () => undefined,
      onRetrySummary: () => undefined,
      onDateChange,
    }
    const { rerender } = render(<HistorySearchDrawer {...props} />)

    const search = screen.getByRole('textbox', { name: '搜索历史对话名称' })
    const dateToggle = screen.getByRole('button', { name: '按日期筛选历史归档' })
    expect(search.compareDocumentPosition(dateToggle) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    fireEvent.click(dateToggle)
    const calendar = screen.getByRole('dialog', { name: '选择历史归档日期' })
    expect(calendar).toHaveTextContent('2026年9月')
    for (const weekday of ['一', '二', '三', '四', '五', '六', '日']) {
      expect(within(calendar).getByText(weekday)).toBeInTheDocument()
    }
    expect(within(calendar).getByRole('button', { name: '上一个月' })).toBeInTheDocument()
    expect(within(calendar).getByRole('button', { name: '下一个月' })).toBeInTheDocument()
    expect(within(calendar).getByRole('button', { name: '2026年9月26日' })).toBeDisabled()

    fireEvent.click(within(calendar).getByRole('button', { name: '2026年9月24日' }))
    expect(onDateChange).toHaveBeenLastCalledWith('2026-09-24')
    expect(screen.queryByRole('dialog', { name: '选择历史归档日期' })).not.toBeInTheDocument()

    rerender(<HistorySearchDrawer {...props} selectedDate="2026-09-24" />)
    expect(screen.getByText('2026年9月24日')).toBeInTheDocument()
    expect(screen.getByText('该日期没有历史对话')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '按日期筛选历史归档' }))
    fireEvent.click(within(screen.getByRole('dialog', { name: '选择历史归档日期' })).getByRole('button', { name: '清除' }))
    expect(onDateChange).toHaveBeenLastCalledWith('')
    vi.useRealTimers()
  })

  it('按历史对话名称过滤卡片并在确认后选择目标会话', () => {
    const onSelectSession = vi.fn()
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" onClose={() => undefined} onSelectSession={onSelectSession} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} onRetrySummary={() => undefined} />)

    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: '项目' } })

    expect(screen.queryByText('当前工作')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '打开对话 项目复盘' }))
    const switchDialog = screen.getByRole('alertdialog', { name: '确认切换历史对话？' })
    expect(switchDialog).toBeInTheDocument()
    expect(switchDialog.parentElement?.parentElement).toBe(document.body)
    expect(onSelectSession).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认切换' }))
    expect(onSelectSession).toHaveBeenCalledWith('s2')
  })

  it('对话运行时显示提示并禁止切换', () => {
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" chatRunning onClose={() => undefined} onSelectSession={() => undefined} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} onRetrySummary={() => undefined} />)

    expect(screen.getByText(/当前对话正在运行/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开对话 项目复盘' })).toBeDisabled()
  })

  it('单条删除和全部删除使用独立按钮及确认层，不触发会话切换', async () => {
    const onSelectSession = vi.fn()
    const onDeleteSession = vi.fn()
    const onDeleteAllSessions = vi.fn()
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" onClose={() => undefined} onSelectSession={onSelectSession} onDeleteSession={onDeleteSession} onDeleteAllSessions={onDeleteAllSessions} onRetrySummary={() => undefined} />)

    fireEvent.click(screen.getByRole('button', { name: '删除对话 项目复盘' }))
    const deleteDialog = screen.getByRole('alertdialog', { name: '确认删除这条历史对话？' })
    expect(deleteDialog).toBeInTheDocument()
    expect(deleteDialog.parentElement?.parentElement).toBe(document.body)
    expect(onSelectSession).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(onDeleteSession).toHaveBeenCalledWith('s2'))

    fireEvent.click(screen.getByRole('button', { name: '全部删除' }))
    const deleteAllDialog = screen.getByRole('alertdialog', { name: '确认删除全部历史对话？' })
    expect(deleteAllDialog).toBeInTheDocument()
    expect(deleteAllDialog.parentElement?.parentElement).toBe(document.body)
    fireEvent.click(screen.getByRole('button', { name: '确认全部删除' }))
    await waitFor(() => expect(onDeleteAllSessions).toHaveBeenCalledTimes(1))
    expect(onSelectSession).not.toHaveBeenCalled()
  })

  it('展示后台摘要状态并允许按摘要内容和 session ID 搜索', () => {
    const summarized = [
      {
        session_id: 'conv_summary_123', window: 'w3', title: '会话隔离改造',
        summary: '实现多标签页租约和后台会话切换。', summary_status: 'completed',
        state: 'closed', rounds: 12, updated_at: '2026-07-23T08:00:00+00:00',
      },
      {
        session_id: 'conv_pending_456', window: 'w4', title: '', summary: '',
        summary_status: 'processing', summary_checkpoint_next_chunk: 1,
        summary_checkpoint_total_chunks: 3, state: 'closed', rounds: 2,
        updated_at: '2026-07-23T09:00:00+00:00',
      },
      {
        session_id: 'conv_failed_789', window: 'w5', title: '', summary: '',
        summary_status: 'retry_wait', summary_attempt_count: 2, summary_max_attempts: 5,
        summary_retry_at: '2026-07-23T09:05:00+00:00', state: 'closed', rounds: 4,
        updated_at: '2026-07-23T09:01:00+00:00',
      },
      {
        session_id: 'conv_exhausted_999', window: 'w6', title: '', summary: '',
        summary_status: 'exhausted', summary_attempt_count: 5, summary_max_attempts: 5,
        state: 'closed', rounds: 5, updated_at: '2026-07-23T09:02:00+00:00',
      },
    ]
    const onRetrySummary = vi.fn()
    render(<HistorySearchDrawer open sessions={summarized} activeSessionId="" onClose={() => undefined} onSelectSession={() => undefined} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} onRetrySummary={onRetrySummary} />)

    const summary = screen.getByText('实现多标签页租约和后台会话切换。')
    fireEvent.mouseEnter(summary)
    const tooltip = screen.getByRole('tooltip')
    expect(tooltip).toHaveTextContent('历史摘要')
    expect(tooltip).toHaveTextContent('实现多标签页租约和后台会话切换。')
    expect(tooltip.parentElement).toBe(document.body)
    fireEvent.mouseLeave(summary)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    const summarizedSession = screen.getByRole('button', { name: '打开对话 会话隔离改造' })
    fireEvent.focus(summarizedSession)
    expect(screen.getByRole('tooltip')).toHaveTextContent('实现多标签页租约和后台会话切换。')
    fireEvent.blur(summarizedSession)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    expect(screen.getByText('正在生成摘要 · 1/3…')).toBeInTheDocument()
    expect(screen.getByText(/摘要生成失败 · 第 2\/5 次/)).toBeInTheDocument()
    expect(screen.getByText('摘要生成失败 · 已停止自动重试')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /重新生成摘要 conv_exhausted_999/ }))
    expect(onRetrySummary).toHaveBeenCalledWith('conv_exhausted_999')
    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: '租约' } })
    expect(screen.getByText('会话隔离改造')).toBeInTheDocument()
    expect(screen.queryByText('正在生成摘要 · 1/3…')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: 'conv_pending_456' } })
    expect(screen.getByText('正在生成摘要 · 1/3…')).toBeInTheDocument()
  })

  it('APP 会话显示 APP版并以只读方式打开', async () => {
    const onSelectSession = vi.fn()
    const history = vi.spyOn(api, 'getHistory').mockResolvedValue({
      user: 'alice',
      source: 'app',
      session_id: 'app-session',
      messages: [
        { role: 'user', content: '来自 APP 的问题' },
        { role: 'assistant', content: '来自智能体的回答' },
      ],
      round_metrics: [],
      round_traces: [],
      pagination: {
        limit: 40,
        total_rounds: 1,
        first_round: 1,
        last_round: 1,
        has_more_before: false,
        next_before: null,
      },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><HistorySearchDrawer
      user="alice"
      open
      sessions={[
        {
          source: 'app', session_id: 'app-session', window: 'app-window',
          title: '手机端对话', state: 'open', chain: 'interactive',
          memory_status: 'completed', memory_processed_round: 2,
          memory_target_round: 2, rounds: 2,
          updated_at: '2026-08-11T12:00:00+08:00',
        },
      ]}
      activeSessionId=""
      onClose={() => undefined}
      onSelectSession={onSelectSession}
      onDeleteSession={() => undefined}
      onDeleteAllSessions={() => undefined}
      onRetrySummary={() => undefined}
    /></QueryClientProvider>)

    expect(screen.getByText(/1 条 APP/)).toBeInTheDocument()
    expect(screen.getByText(/APP版 · 2 轮/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除对话 手机端对话' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '只读查看对话 手机端对话' }))
    expect(await screen.findByText('来自 APP 的问题')).toBeInTheDocument()
    expect(api.getHistory).toHaveBeenCalledWith('alice', 'app-session', expect.objectContaining({ source: 'app' }))
    expect(onSelectSession).not.toHaveBeenCalled()
    history.mockRestore()
  })

  it('外部消息和 CLI 归档按来源展示，并以只读方式打开完整历史和记忆状态', async () => {
    const onSelectSession = vi.fn()
    const updatedAt = '2026-08-08T00:00:00+08:00'
    const history = vi.spyOn(api, 'getHistory').mockResolvedValue({
      user: 'alice',
      source: 'message:telegram',
      session_id: 'tg-1',
      messages: [
        { role: 'user', content: '来自 Telegram 的问题' },
        { role: 'assistant', content: '来自智能体的回答' },
      ],
      round_metrics: [],
      round_traces: [],
      pagination: {
        limit: 40,
        total_rounds: 1,
        first_round: 1,
        last_round: 1,
        has_more_before: false,
        next_before: null,
      },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><HistorySearchDrawer
      user="alice"
      open
      sessions={[
        {
          source: 'message:telegram', bound_platform: 'telegram', session_id: 'tg-1',
          window: 'tg-window', title: 'Telegram 对话', state: 'closed', chain: 'message',
          memory_status: 'queued', memory_processed_round: 2, memory_target_round: 4,
          rounds: 4, updated_at: updatedAt,
        },
      ]}
      activeSessionId=""
      onClose={() => undefined}
      onSelectSession={onSelectSession}
      onDeleteSession={() => undefined}
      onDeleteAllSessions={() => undefined}
      onRetrySummary={() => undefined}
    /></QueryClientProvider>)

    expect(screen.getByText(`telegram · 4 轮 · ${formatDateTime(updatedAt)}`)).toBeInTheDocument()
    expect(screen.getByText('记忆 queued · 2/4')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除对话 Telegram 对话' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '只读查看对话 Telegram 对话' }))

    expect(await screen.findByRole('dialog', { name: '只读历史归档' })).toBeInTheDocument()
    expect(await screen.findByText('来自 Telegram 的问题')).toBeInTheDocument()
    expect(screen.getByText('来自智能体的回答')).toBeInTheDocument()
    expect(screen.getByText('2/4')).toBeInTheDocument()
    expect(onSelectSession).not.toHaveBeenCalled()
    history.mockRestore()
  })

  it('定时任务归档使用可读名称和来源，并通过只读历史接口打开', async () => {
    const history = vi.spyOn(api, 'getHistory').mockResolvedValue({
      user: 'alice', source: 'background:cron:cron_1234abcd', session_id: 'cron-session',
      messages: [{ role: 'user', content: '定时任务输入' }, { role: 'assistant', content: '定时任务结果' }],
      round_metrics: [], round_traces: [],
      pagination: { limit: 40, total_rounds: 1, first_round: 1, last_round: 1, has_more_before: false, next_before: null },
    })
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={queryClient}><HistorySearchDrawer
      user="alice" open activeSessionId="" onClose={() => undefined} onSelectSession={() => undefined}
      onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} onRetrySummary={() => undefined}
      sessions={[{
        source: 'background:cron:cron_1234abcd', session_id: 'cron-session', window: 'cron-window',
        title: '', state: 'closed', memory_status: 'completed', memory_processed_round: 1,
        memory_target_round: 1, rounds: 1, updated_at: '2026-09-25T05:00:00+00:00',
      }]}
    /></QueryClientProvider>)

    expect(screen.getByText('定时任务 · cron_1234abcd')).toBeInTheDocument()
    expect(screen.getByText(/定时任务 · 1 轮/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '只读查看对话 定时任务 · cron_1234abcd' }))
    expect(await screen.findByText('定时任务结果')).toBeInTheDocument()
    expect(api.getHistory).toHaveBeenCalledWith('alice', 'cron-session', expect.objectContaining({ source: 'background:cron:cron_1234abcd' }))
    history.mockRestore()
  })
})
