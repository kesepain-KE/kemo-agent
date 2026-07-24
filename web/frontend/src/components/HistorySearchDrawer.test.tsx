import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HistorySearchDrawer } from './HistorySearchDrawer'

const sessions = [
  { session_id: 's1', window: 'w1', title: '当前工作', state: 'open', rounds: 3, updated_at: '2026-07-22T08:00:00+00:00' },
  { session_id: 's2', window: 'w2', title: '项目复盘', state: 'closed', rounds: 8, updated_at: '2026-07-21T08:00:00+00:00' },
]

describe('HistorySearchDrawer', () => {
  it('按历史对话名称过滤卡片并在确认后选择目标会话', () => {
    const onSelectSession = vi.fn()
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" onClose={() => undefined} onSelectSession={onSelectSession} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} />)

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
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" chatRunning onClose={() => undefined} onSelectSession={() => undefined} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} />)

    expect(screen.getByText(/当前对话正在运行/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开对话 项目复盘' })).toBeDisabled()
  })

  it('单条删除和全部删除使用独立按钮及确认层，不触发会话切换', async () => {
    const onSelectSession = vi.fn()
    const onDeleteSession = vi.fn()
    const onDeleteAllSessions = vi.fn()
    render(<HistorySearchDrawer open sessions={sessions} activeSessionId="s1" onClose={() => undefined} onSelectSession={onSelectSession} onDeleteSession={onDeleteSession} onDeleteAllSessions={onDeleteAllSessions} />)

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
        summary_status: 'processing', state: 'closed', rounds: 2,
        updated_at: '2026-07-23T09:00:00+00:00',
      },
      {
        session_id: 'conv_failed_789', window: 'w5', title: '', summary: '',
        summary_status: 'failed', summary_retry_count: 1,
        summary_retry_at: '2026-07-23T09:05:00+00:00', state: 'closed', rounds: 4,
        updated_at: '2026-07-23T09:01:00+00:00',
      },
    ]
    render(<HistorySearchDrawer open sessions={summarized} activeSessionId="" onClose={() => undefined} onSelectSession={() => undefined} onDeleteSession={() => undefined} onDeleteAllSessions={() => undefined} />)

    expect(screen.getByText('实现多标签页租约和后台会话切换。')).toBeInTheDocument()
    expect(screen.getByText('正在生成摘要…')).toBeInTheDocument()
    expect(screen.getByText('摘要生成失败，后台将自动重试')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: '租约' } })
    expect(screen.getByText('会话隔离改造')).toBeInTheDocument()
    expect(screen.queryByText('正在生成摘要…')).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: 'conv_pending_456' } })
    expect(screen.getByText('正在生成摘要…')).toBeInTheDocument()
  })
})
