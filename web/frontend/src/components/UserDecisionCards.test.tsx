import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UserDecisionCard, UserDecisionCards } from './UserDecisionCards'
import type { UserDecision } from '../api/decisions'
import { buildUserDraft } from '../pages/settingsSupport'

const question: UserDecision = {
  decision_id: 'exec_1', integration_id: 'integration_1', title: '数据库方案', question: '请选择数据库',
  options: [{ id: 'sqlite', label: 'SQLite', recommended: true }, { id: 'pg', label: 'PostgreSQL', description: '独立部署' }],
  mode: 'wait', status: 'pending', created_at: Date.now() / 1000, expires_at: Date.now() / 1000 + 1800,
  answer: null, resolution: '',
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('UserDecisionCard', () => {
  it('submits the selected option and prevents duplicate submissions while pending', async () => {
    let finish!: () => void
    const onAnswer = vi.fn(() => new Promise<void>(resolve => { finish = resolve }))
    render(<UserDecisionCard decision={question} onAnswer={onAnswer} />)
    expect(screen.getByText('等待回答')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交选择' })).toBeDisabled()
    fireEvent.click(screen.getByRole('radio', { name: /PostgreSQL/ }))
    fireEvent.click(screen.getByRole('button', { name: '提交选择' }))
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith({ option_id: 'pg', text: '' })
    expect(screen.getByRole('button', { name: '正在提交…' })).toBeDisabled()
    await act(async () => finish())
  })

  it('keeps a custom reply across collapse and submits only the custom answer', async () => {
    const onAnswer = vi.fn(async () => {})
    render(<UserDecisionCard decision={{ ...question, mode: 'async' }} onAnswer={onAnswer} />)
    fireEvent.change(screen.getByRole('textbox', { name: '自定义回复' }), { target: { value: '使用现有数据库' } })
    fireEvent.click(screen.getByRole('button', { name: '收起决策卡片' }))
    expect(screen.getByText('请选择数据库')).not.toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '展开决策卡片' }))
    expect(screen.getByRole('textbox')).toHaveValue('使用现有数据库')
    fireEvent.click(screen.getByRole('button', { name: '提交选择' }))
    await waitFor(() => expect(onAnswer).toHaveBeenCalledExactlyOnceWith({ option_id: '', text: '使用现有数据库' }))
  })

  it('retains the answer draft when submission fails', async () => {
    render(<UserDecisionCard decision={question} onAnswer={async () => { throw new Error('连接中断') }} />)
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '待提交回复' } })
    fireEvent.click(screen.getByRole('button', { name: '提交选择' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('连接中断')
    expect(screen.getByRole('textbox')).toHaveValue('待提交回复')
    expect(screen.getByRole('button', { name: '提交选择' })).toBeEnabled()
  })

  it('disables answers when the countdown expires without choosing a default', async () => {
    vi.useFakeTimers()
    const onAnswer = vi.fn()
    render(<UserDecisionCard decision={{ ...question, expires_at: Date.now() / 1000 + 1 }} onAnswer={onAnswer} />)
    fireEvent.click(screen.getByRole('radio', { name: /SQLite/ }))
    act(() => { vi.advanceTimersByTime(1100) })
    expect(screen.getByText('等待智能体自主决策')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '提交选择' })).toBeDisabled()
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('reads only the current conversation and drops its cards when switching scope', async () => {
    const fetch = vi.fn(async (path: string) => ({
      ok: true, json: async () => ({
        user: 'alice', source: 'web', session_id: path.includes('/s1/') ? 's1' : 's2',
        enabled: true, revision: 1, decisions: path.includes('/s1/') ? [question] : [],
      }),
    }))
    vi.stubGlobal('fetch', fetch)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const view = render(<QueryClientProvider client={client}><UserDecisionCards key="s1" user="alice" sessionId="s1" /></QueryClientProvider>)
    expect(await screen.findByText('数据库方案')).toBeInTheDocument()
    view.rerender(<QueryClientProvider client={client}><UserDecisionCards key="s2" user="alice" sessionId="s2" /></QueryClientProvider>)
    expect(screen.queryByText('数据库方案')).not.toBeInTheDocument()
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(2))
    expect(fetch.mock.calls[0][0]).toContain('/users/alice/sessions/s1/decisions?source=web')
    expect(fetch.mock.calls[1][0]).toContain('/users/alice/sessions/s2/decisions?source=web')
  })

  it('defaults user configuration off and restores an explicitly enabled setting', () => {
    expect(buildUserDraft({}).interactive_decisions.enabled).toBe(false)
    expect(buildUserDraft({ interactive_decisions: { enabled: true } }).interactive_decisions.enabled).toBe(true)
  })
})
