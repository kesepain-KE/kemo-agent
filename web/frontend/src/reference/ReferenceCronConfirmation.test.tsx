import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { getRuntimeStatus } from '../api/client'
import { server } from '../test/server'
import type { ShellOutletContext } from '../components/appShellTypes'
import { ReferencePage } from './ReferencePage'

function renderStatus() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const context = { user: 'kesepain', sessionId: 's1', chatRunning: false } as ShellOutletContext
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/status']}>
    <Routes><Route element={<Outlet context={context} />}>
      <Route path="*" element={<ReferencePage />} />
    </Route></Routes>
  </MemoryRouter></QueryClientProvider>)
}

describe('中断系统维护任务待确认', () => {
  it('提示词未返回时仍显示待确认数量和副作用核对提示，不自动恢复任务', async () => {
    const runtime = await getRuntimeStatus('kesepain', 's1')
    let writes = 0
    let promptRequested = false
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    server.use(
      http.get('/api/users/kesepain/runtime/status', async ({ request }) => {
        const sections = new URL(request.url).searchParams.get('sections') || ''
        if (sections === 'summary,prompt') { promptRequested = true; await pending }
        return HttpResponse.json({ ...runtime, included_sections: sections.split(','),
          system_cron: { ...runtime.system_cron, pending_confirmation_count: sections === 'maintenance' ? 2 : 0 } })
      }),
      http.patch('/api/users/kesepain/tasks/crons/:taskId', () => {
        writes++
        return HttpResponse.json({})
      }),
    )
    try {
      renderStatus()
      fireEvent.click(screen.getByRole('button', { name: '系统提示词上下文' }))
      await waitFor(() => expect(promptRequested).toBe(true))
      const notice = await screen.findByRole('status', { name: '系统维护任务待确认' })
      expect(notice).toHaveTextContent('有 2 个系统维护任务因中断暂停，等待确认后恢复。')
      expect(notice).toHaveTextContent('请先核对已执行操作，再显式恢复任务。')
      expect(screen.queryByText('系统提示词与拼接状态')).not.toBeInTheDocument()
      expect(writes).toBe(0)
    } finally { release() }
  })

  it.each([0, undefined])('数量为%s时不显示待确认提示，兼容旧状态接口', async count => {
    const runtime = await getRuntimeStatus('kesepain', 's1')
    server.use(http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json({
      ...runtime, system_cron: { ...runtime.system_cron, pending_confirmation_count: count },
    })))
    renderStatus()
    await waitFor(() => expect(screen.queryByText('正在读取系统维护任务状态…')).toBeNull())
    if (count === undefined) expect(screen.getByText('系统维护任务状态未提供')).toBeVisible()
    expect(screen.queryByRole('status', { name: '系统维护任务待确认' })).toBeNull()
  })

  it('刷新后移除已处理的待确认提示', async () => {
    const runtime = await getRuntimeStatus('kesepain', 's1')
    let count = 1
    server.use(http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json({
      ...runtime, system_cron: { ...runtime.system_cron, pending_confirmation_count: count },
    })))
    renderStatus()
    await screen.findByRole('status', { name: '系统维护任务待确认' })
    count = 0
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(screen.queryByRole('status', { name: '系统维护任务待确认' })).toBeNull())
  })
})
