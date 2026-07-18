import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from './AppShell'
import { ChatPage } from '../pages/ChatPage'
import { server } from '../test/server'
import type { SessionSummary } from '../types/api'

function renderApp(path = '/chat') {
  let currentSearch = new URL(path, 'http://test').search
  function LocationProbe() {
    currentSearch = useLocation().search
    return null
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route path="chat" element={<ChatPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { getSearch: () => currentSearch, client }
}

function session(id: string, title: string, index: number): SessionSummary {
  return {
    session_id: id,
    window: `window-${index}`,
    title,
    rounds: index,
    updated_at: `2026-07-${String(index).padStart(2, '0')}T08:00:00+00:00`,
  }
}

describe('AppShell navigation', () => {
  it('加载真实用户并展示空聊天入口', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    expect(screen.getByText(/当前用户的配置、历史、知识/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '切换当前用户' })).toBeInTheDocument()
  })

  it('从 URL 恢复用户与会话并加载历史空状态', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getAllByText('s1').length).toBeGreaterThan(0))
    expect((await screen.findAllByText('kemo-agent')).length).toBeGreaterThan(0)
  })

  it('顶部栏包含上下文窗口、字号、主题和运行状态控件', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    expect(screen.getAllByTitle('查看上下文与运行状态').length).toBeGreaterThan(0)
    expect(screen.getAllByTitle('调整界面字号').length).toBeGreaterThan(0)
    expect(screen.getByTitle(/切换为高级/)).toBeInTheDocument()
    expect(screen.getByTitle('运行状态')).toBeInTheDocument()
    expect(screen.getByTitle('命令面板')).toBeInTheDocument()
    expect(await screen.findByTitle('查看当前 Provider')).toBeInTheDocument()
  })

  it('字号切换会同步更新文字与顶部布局比例', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))

    fireEvent.click(screen.getByTitle('调整界面字号'))
    expect(screen.getByText('文字与顶部布局同步适配')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: /大.*舒适/ }))
    await waitFor(() => expect(document.documentElement.dataset.fontSize).toBe('large'))

    fireEvent.click(screen.getByTitle('调整界面字号'))
    fireEvent.click(screen.getByRole('menuitem', { name: /中.*默认/ }))
    await waitFor(() => expect(document.documentElement.dataset.fontSize).toBe('medium'))
  })

  it('命令面板可以打开并展示标准页面入口', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    fireEvent.click(screen.getByTitle('命令面板'))
    expect(screen.getByRole('dialog', { name: '全局搜索与命令' })).toBeInTheDocument()
    expect(screen.getByText('查看任务中枢')).toBeInTheDocument()
  })

  it('历史超过侧栏上限时可以展开全部并搜索', async () => {
    const sessions = Array.from({ length: 10 }, (_, index) => (
      session(`session-${index + 1}`, index === 9 ? '年度规划十' : `普通对话 ${index + 1}`, index + 1)
    ))
    server.use(http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
      user: 'kesepain', source: 'web', sessions,
    })))
    renderApp('/chat?user=kesepain')

    fireEvent.click(await screen.findByRole('button', { name: /展开全部/ }))
    expect(screen.getByRole('dialog', { name: '全部历史对话' })).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '搜索历史对话' }), {
      target: { value: '年度规划' },
    })

    expect(screen.getByText('年度规划十')).toBeInTheDocument()
    expect(screen.queryByText('普通对话 9')).not.toBeInTheDocument()
  })

  it('侧栏历史右键可以编辑名字并持久化', async () => {
    let sessions = [session('s1', '旧名字', 1)]
    let receivedTitle = ''
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
        user: 'kesepain', source: 'web', sessions,
      })),
      http.patch('/api/users/kesepain/sessions/:sessionId', async ({ request }) => {
        const body = await request.json() as { title: string }
        receivedTitle = body.title
        sessions = [{ ...sessions[0], title: body.title }]
        return HttpResponse.json({ user: 'kesepain', source: 'web', session: sessions[0] })
      }),
    )
    renderApp('/chat?user=kesepain')

    fireEvent.contextMenu((await screen.findByText('旧名字')).closest('button')!)
    fireEvent.click(screen.getByRole('menuitem', { name: '编辑名字' }))
    fireEvent.change(screen.getByLabelText('对话名称'), { target: { value: '新的对话名字' } })
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }))

    await waitFor(() => expect(receivedTitle).toBe('新的对话名字'))
    expect(await screen.findByText('新的对话名字')).toBeInTheDocument()
  })

  it('展开列表右键删除当前会话后清除 URL session', async () => {
    let sessions = Array.from({ length: 9 }, (_, index) => session(`s${index + 1}`, `历史 ${index + 1}`, index + 1))
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
        user: 'kesepain', source: 'web', sessions,
      })),
      http.delete('/api/users/kesepain/sessions/:sessionId', ({ params }) => {
        sessions = sessions.filter((item) => item.session_id !== params.sessionId)
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: params.sessionId, deleted: true,
        })
      }),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')

    fireEvent.click(await screen.findByRole('button', { name: /展开全部/ }))
    const dialog = screen.getByRole('dialog', { name: '全部历史对话' })
    fireEvent.contextMenu(Array.from(dialog.querySelectorAll('button')).find((item) => item.textContent?.includes('历史 1'))!)
    fireEvent.click(screen.getByRole('menuitem', { name: '删除' }))
    expect(screen.getByRole('alertdialog', { name: '删除历史对话' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))

    await waitFor(() => expect(getSearch()).toBe('?user=kesepain'))
    expect(screen.queryByRole('alertdialog', { name: '删除历史对话' })).not.toBeInTheDocument()
  })

  it('全部删除按钮位于标题下方并通过确认后清空当前用户历史', async () => {
    let sessions = [session('s1', '历史 1', 1), session('s2', '历史 2', 2)]
    let deleteCalled = false
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
        user: 'kesepain', source: 'web', sessions,
      })),
      http.delete('/api/users/kesepain/sessions', () => {
        deleteCalled = true
        const deletedSessions = sessions.length
        sessions = []
        return HttpResponse.json({
          user: 'kesepain', source: 'web', deleted: true,
          deleted_sessions: deletedSessions, deleted_windows: deletedSessions,
        })
      }),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')

    const deleteAllButton = await screen.findByRole('button', { name: /全部删除/ })
    await waitFor(() => expect(deleteAllButton).toBeEnabled())
    expect(screen.getByText('最近对话').nextElementSibling).toBe(deleteAllButton)
    fireEvent.click(deleteAllButton)
    expect(screen.getByRole('alertdialog', { name: '删除全部历史对话' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认全部删除' }))

    await waitFor(() => expect(deleteCalled).toBe(true))
    await waitFor(() => expect(getSearch()).toBe('?user=kesepain'))
    expect(await screen.findByText('暂无 Web 会话')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '全部删除' })).toBeDisabled()
  })

  it('侧边栏收缩时不渲染全部删除按钮', async () => {
    server.use(http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
      user: 'kesepain', source: 'web', sessions: [session('s1', '历史 1', 1)],
    })))
    renderApp('/chat?user=kesepain')

    expect(await screen.findByRole('button', { name: /全部删除/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '收缩侧边栏' }))

    await waitFor(() => expect(screen.queryByRole('button', { name: /全部删除/ })).not.toBeInTheDocument())
  })
})
