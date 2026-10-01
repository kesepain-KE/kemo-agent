import { render, screen } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import type { SessionSummary } from '../types/api'
import { ReadOnlySessionHistoryDialog } from './ReadOnlySessionHistoryDialog'

const session: SessionSummary = {
  session_id: 'conv_cron_123',
  window: 'conv_cron_123',
  title: '',
  summary: '',
  source: 'background:cron:cron_1234abcd',
  state: 'closed',
  run_state: 'idle',
  rounds: 1,
  updated_at: '2026-09-20T14:00:00+08:00',
  memory_status: 'completed',
  memory_processed_round: 1,
  memory_target_round: 1,
}

function renderDialog(targetSession = session) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ReadOnlySessionHistoryDialog user="alice" session={targetSession} onClose={() => undefined} />
    </QueryClientProvider>,
  )
}

describe('ReadOnlySessionHistoryDialog', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('将已过期的定时任务归档与临时读取失败区分开', async () => {
    vi.spyOn(api, 'getHistory').mockRejectedValue(new api.ApiError('missing', 404))
    renderDialog()

    expect(await screen.findByText('这条历史归档已不存在，可能已经超过定时任务历史保留期。')).toBeInTheDocument()
    expect(screen.getAllByText('定时任务').length).toBeGreaterThan(0)
  })

  it('非定时任务来源的 404 使用通用归档清理说明', async () => {
    vi.spyOn(api, 'getHistory').mockRejectedValue(new api.ApiError('missing', 404))
    renderDialog({ ...session, session_id: 'cli-session', window: 'cli-session', source: 'cli' })

    expect(await screen.findByText('这条历史归档已不存在，可能已被清理或超过历史保留期。')).toBeInTheDocument()
    expect(screen.queryByText(/定时任务历史保留期/)).not.toBeInTheDocument()
    expect(screen.getAllByText('CLI').length).toBeGreaterThan(0)
  })

  it('将不受支持的来源显示为明确的校验错误', async () => {
    vi.spyOn(api, 'getHistory').mockRejectedValue(new api.ApiError('unsupported', 400))
    renderDialog()

    expect(await screen.findByText('该会话来源不支持只读历史访问。')).toBeInTheDocument()
  })

  it('已恢复完成的记忆不再显示历史失败提示', async () => {
    vi.spyOn(api, 'getHistory').mockResolvedValue({
      messages: [],
      pagination: { has_more_before: false },
    } as never)
    renderDialog({
      ...session,
      memory_last_error: { message: '同一执行仍在超时后的后台退出过程中，已拒绝重复启动' },
      memory_status: 'completed',
    })

    await screen.findByText('这条归档没有可显示的消息正文。')
    expect(screen.queryByText(/记忆整理失败/)).not.toBeInTheDocument()
    expect(screen.getByText('1/1')).toBeInTheDocument()
  })

  it('当前记忆失败时显示失败诊断', async () => {
    vi.spyOn(api, 'getHistory').mockResolvedValue({
      messages: [],
      pagination: { has_more_before: false },
    } as never)
    renderDialog({
      ...session,
      memory_last_error: { message: '记忆提取失败' },
      memory_status: 'failed',
    })

    expect(await screen.findByText('记忆整理失败：记忆提取失败')).toBeInTheDocument()
  })
})
