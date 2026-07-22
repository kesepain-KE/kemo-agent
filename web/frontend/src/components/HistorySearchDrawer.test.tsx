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
    expect(screen.getByRole('alertdialog', { name: '确认切换历史对话？' })).toBeInTheDocument()
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
    expect(screen.getByRole('alertdialog', { name: '确认删除这条历史对话？' })).toBeInTheDocument()
    expect(onSelectSession).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    await waitFor(() => expect(onDeleteSession).toHaveBeenCalledWith('s2'))

    fireEvent.click(screen.getByRole('button', { name: '全部删除' }))
    expect(screen.getByRole('alertdialog', { name: '确认删除全部历史对话？' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认全部删除' }))
    await waitFor(() => expect(onDeleteAllSessions).toHaveBeenCalledTimes(1))
    expect(onSelectSession).not.toHaveBeenCalled()
  })
})
