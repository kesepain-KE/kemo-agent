import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import type { ShellOutletContext } from '../components/appShellTypes'
import { ReferencePage, loadReferenceData } from './ReferencePage'

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
  it('在运行诊断显示待确认数量和副作用核对提示，不自动恢复任务', async () => {
    const data = await loadReferenceData('/status', 'kesepain', 's1')
    let writes = 0
    server.use(
      http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json({
        ...data.runtime, system_cron: { ...data.runtime.system_cron, pending_confirmation_count: 2 },
      })),
      http.patch('/api/users/kesepain/tasks/crons/:taskId', () => {
        writes++
        return HttpResponse.json({})
      }),
    )
    renderStatus()
    const notice = await screen.findByRole('status', { name: '系统维护任务待确认' })
    expect(notice).toHaveTextContent('有 2 个系统维护任务因中断暂停，等待确认后恢复。')
    expect(notice).toHaveTextContent('请先核对已执行操作，再显式恢复任务。')
    expect(writes).toBe(0)
  })

  it.each([0, undefined])('数量为%s时不显示待确认提示，兼容旧状态接口', async count => {
    const data = await loadReferenceData('/status', 'kesepain', 's1')
    server.use(http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json({
      ...data.runtime, system_cron: { ...data.runtime.system_cron, pending_confirmation_count: count },
    })))
    renderStatus()
    await screen.findByRole('heading', { name: '运行诊断' })
    expect(screen.queryByRole('status', { name: '系统维护任务待确认' })).toBeNull()
  })

  it('刷新后移除已处理的待确认提示', async () => {
    const data = await loadReferenceData('/status', 'kesepain', 's1')
    let count = 1
    server.use(http.get('/api/users/kesepain/runtime/status', () => HttpResponse.json({
      ...data.runtime, system_cron: { ...data.runtime.system_cron, pending_confirmation_count: count },
    })))
    renderStatus()
    await screen.findByRole('status', { name: '系统维护任务待确认' })
    count = 0
    fireEvent.click(screen.getByRole('button', { name: '刷新' }))
    await waitFor(() => expect(screen.queryByRole('status', { name: '系统维护任务待确认' })).toBeNull())
  })
})
