import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getRuntimeLogs } from '../api/client'
import type { RuntimeLogCategory, RuntimeLogsResponse } from '../types/api'
import { RuntimeLogsPanel } from './RuntimeLogsPanel'

vi.mock('../api/client', () => ({ getRuntimeLogs: vi.fn() }))
const getLogs = vi.mocked(getRuntimeLogs)
function response(user = 'alice', category: RuntimeLogCategory = 'all', page = 1): RuntimeLogsResponse {
  return {
    user, category, generated_at: '2026-09-13T00:00:00Z', source_errors: [], cache: { hit: true, ttl_seconds: 5 },
    counts: { all: 26, backend: 1, terminal: 1, threads: 1, message: 1 },
    entries: [{ id: `${user}:${category}:${page}`, category: category === 'all' ? 'backend' : category,
      title: `${user}-${category}-${page}`, occurred_at: '2026-09-13T00:00:00Z', status: 'failed',
      duration_ms: 12, exit_code: 1, detail: '简短状态说明', source: category === 'threads' ? 'snapshot' : 'memory' }],
    pagination: { page, page_size: 25, total_items: 26, total_pages: 2, has_previous: page > 1, has_next: page < 2 },
  }
}
function mount(user = 'alice') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><RuntimeLogsPanel user={user} /></QueryClientProvider>)
  return { ...view, changeUser: (next: string) => view.rerender(<QueryClientProvider client={client}><RuntimeLogsPanel user={next} /></QueryClientProvider>) }
}

describe('执行记录分类日志', () => {
  beforeEach(() => { getLogs.mockReset(); getLogs.mockImplementation(async (user, category = 'all', page = 1) => response(user, category, page)) })

  it('五种分类、分页复位与强制刷新均独立查询日志', async () => {
    mount()
    expect(await screen.findByText('alice-all-1')).toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toHaveLength(5)
    fireEvent.click(screen.getByRole('button', { name: '下一页' }))
    expect(await screen.findByText('alice-all-2')).toBeInTheDocument()
    for (const [label, category] of [['后端日志', 'backend'], ['后端线程', 'threads'], ['终端日志', 'terminal'], ['消息日志', 'message']] as const) {
      fireEvent.click(screen.getByRole('tab', { name: label }))
      expect(await screen.findByText(`alice-${category}-1`)).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: label })).toHaveAttribute('aria-selected', 'true')
    }
    expect(screen.getByText('12 ms · 退出码 1')).toBeInTheDocument()
    expect(screen.getByText('失败')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '刷新日志' }))
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith('alice', 'message', 1, true))
  })

  it('部分来源失败与请求错误明确提示，不冒充没有日志', async () => {
    getLogs.mockResolvedValue({ ...response(), entries: [], source_errors: ['持久化日志暂时不可用，请刷新重试。'] })
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('持久化日志暂时不可用')
    expect(screen.getByText('部分日志源不可用，暂时没有可显示的记录。')).toBeInTheDocument()
    getLogs.mockRejectedValue(new Error('offline'))
    fireEvent.click(screen.getByRole('button', { name: '刷新日志' }))
    expect(await screen.findByText(/日志读取失败，请刷新重试/)).toBeInTheDocument()
  })

  it('终端日志以只读终端输出展示，不复用普通日志卡片', async () => {
    mount()
    fireEvent.click(screen.getByRole('tab', { name: '终端日志' }))
    expect(await screen.findByRole('region', { name: 'kemo-agent 启动终端只读输出' })).toBeInTheDocument()
    expect(screen.getByText('kemo-agent 启动终端')).toBeInTheDocument()
    expect(screen.getByText('只读')).toBeInTheDocument()
    expect(screen.getByText('ERR')).toBeInTheDocument()
    expect(screen.getByText('alice-terminal-1')).toBeInTheDocument()
    expect(screen.queryByText('发生时间')).not.toBeInTheDocument()
    expect(screen.getByText('当前载入 1 行 · 1 行标准错误')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '上一页' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '下一页' })).not.toBeInTheDocument()
  })

  it('切换用户立即清除旧记录并复位分类，不展示上个用户的缓存', async () => {
    const view = mount()
    expect(await screen.findByText('alice-all-1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '消息日志' }))
    expect(await screen.findByText('alice-message-1')).toBeInTheDocument()
    getLogs.mockImplementation(() => new Promise(() => {}))
    view.changeUser('bob')
    expect(screen.queryByText('alice-message-1')).not.toBeInTheDocument()
    expect(screen.queryByText('alice-all-1')).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '全部' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(getLogs).toHaveBeenLastCalledWith('bob', 'all', 1, false))
  })
})
