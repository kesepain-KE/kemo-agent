import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
})
