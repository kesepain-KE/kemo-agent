import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { delay, http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell } from './AppShell'
import { ChatPage } from '../pages/ChatPage'
import { SettingsPage } from '../pages/SettingsPage'
import { server } from '../test/server'
import type { SessionSummary } from '../types/api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

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
            <Route path="settings" element={<SettingsPage />} />
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

  it('对话运行期间同时锁定配置页与侧栏的用户切换', async () => {
    let releaseChat!: () => void
    let markChatStarted!: () => void
    let chatSignal: AbortSignal | undefined
    const chatGate = new Promise<void>((resolve) => { releaseChat = resolve })
    const chatStarted = new Promise<void>((resolve) => { markChatStarted = resolve })
    const interceptedFetch = globalThis.fetch
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatSignal = init?.signal || undefined
      markChatStarted()
      await chatGate
      return new Response('event: done\ndata: {"type":"done"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    })
    renderApp('/chat?user=kesepain')

    await screen.findByText(/当前用户的配置、历史、知识/)
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '保持运行' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByRole('button', { name: '停止生成' })
    await chatStarted

    fireEvent.click(screen.getByRole('link', { name: /^配置 配置$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '用户切换 ›' }))
    const userRow = await screen.findByRole('button', { name: '切换到用户 reviewer' })
    expect(userRow).toBeDisabled()
    expect(screen.getByText('对话运行中，暂不可切换')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '切换当前用户' }))
    expect(screen.getByRole('menuitem', { name: /reviewer/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('link', { name: /^对话 对话$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '停止生成' }))
    expect(chatSignal?.aborted).toBe(true)

    releaseChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('link', { name: /^配置 配置$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '用户切换 ›' }))
    expect(await screen.findByRole('button', { name: '切换到用户 reviewer' })).toBeEnabled()
  })

  it('从 URL 恢复用户与会话并加载历史空状态', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getAllByText('s1').length).toBeGreaterThan(0))
    expect((await screen.findAllByText('kemo-agent')).length).toBeGreaterThan(0)
  })

  it('未提交的会话不会请求 history API', async () => {
    let historyRequests = 0
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({ user: 'kesepain', source: 'web', sessions: [] })),
      http.get('/api/users/kesepain/sessions/:sessionId/history', () => {
        historyRequests += 1
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: 'web_uncommitted', messages: [], round_metrics: [], round_traces: [] })
      }),
    )
    renderApp('/chat?user=kesepain&session=web_uncommitted')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    expect(historyRequests).toBe(0)
  })

  it('首轮提交在会话列表延迟刷新时不会回退欢迎页', async () => {
    let committedSessionId = ''
    let sessionRequests = 0
    let chatRequests = 0
    const interceptedFetch = globalThis.fetch
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequests += 1
      const body = JSON.parse(String(init?.body)) as { session_id: string }
      committedSessionId = body.session_id
      return new Response(
        'event: text_delta\ndata: {"type":"text_delta","content":"流式回复"}\n\n'
        + 'event: done\ndata: {"type":"done"}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    })
    server.use(
      http.get('/api/users/kesepain/sessions', async () => {
        sessionRequests += 1
        if (committedSessionId) await delay(120)
        return HttpResponse.json({
          user: 'kesepain',
          source: 'web',
          sessions: committedSessionId ? [session(committedSessionId, '首轮会话', 1)] : [],
        })
      }),
      http.get('/api/users/kesepain/sessions/:sessionId/history', ({ params }) => HttpResponse.json({
        user: 'kesepain',
        source: 'web',
        session_id: params.sessionId,
        messages: [
          { role: 'user', content: '首轮测试', round: 1 },
          { role: 'assistant', content: '历史回复', round: 1 },
        ],
        round_metrics: [],
        round_traces: [],
      })),
    )
    const { getSearch } = renderApp('/chat?user=kesepain')
    const welcomeText = /当前用户的配置、历史、知识、任务与技能运行态已载入/
    await screen.findByText(welcomeText)
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '首轮测试' } })
    const sendButton = screen.getByRole('button', { name: '发送' })
    expect(sendButton).toBeEnabled()
    fireEvent.click(sendButton)
    await waitFor(() => expect(screen.queryByText(welcomeText)).not.toBeInTheDocument())

    let welcomeReappeared = false
    const observer = new MutationObserver(() => {
      if (screen.queryByText(welcomeText)) welcomeReappeared = true
    })
    observer.observe(document.body, { childList: true, subtree: true })
    await waitFor(() => expect(chatRequests).toBe(1), { timeout: 2_000 })
    await waitFor(() => expect(committedSessionId).toMatch(/^web_/), { timeout: 2_000 })
    await waitFor(() => expect(sessionRequests).toBeGreaterThanOrEqual(2), { timeout: 2_000 })
    await waitFor(() => expect(getSearch()).toContain('session=web_'), { timeout: 2_000 })
    expect(await screen.findByText('历史回复', undefined, { timeout: 5_000 })).toBeInTheDocument()
    observer.disconnect()

    expect(welcomeReappeared).toBe(false)
    expect(sessionRequests).toBeGreaterThanOrEqual(2)
  }, 15_000)

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

  it('对话操作菜单提供保存新建、清空、压缩和重新生成', async () => {
    let compressionCalled = false
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '上一条问题' }, { role: 'assistant', content: '上一条回答' }],
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/compress', () => {
        compressionCalled = true
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', requested: true,
          compressed: true, rounds_removed: 2, summary_cache_exists: true,
          context: { rounds_removed: 2 },
        })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('上一条回答')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    expect(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /清空此对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /重新发送一次消息/ })).toBeInTheDocument()
    expect(screen.getByText('每次打开新网页都会创建新对话，可通过打开历史对话接续对话。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ }))
    await waitFor(() => expect(compressionCalled).toBe(true))
    expect(await screen.findByText('上下文压缩完成，已整理 2 轮历史。')).toBeInTheDocument()

    const regenerate = screen.getByRole('menuitem', { name: /重新发送一次消息/ })
    await waitFor(() => expect(regenerate).toBeEnabled())
    fireEvent.click(regenerate)
    await waitFor(() => expect(screen.getAllByText('上一条问题').length).toBeGreaterThan(1))
  })

  it('清空当前对话会删除归档并进入新对话', async () => {
    let deletedSession = ''
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '待清空问题' }, { role: 'assistant', content: '待清空回答' }],
        round_metrics: [], round_traces: [],
      })),
      http.delete('/api/users/kesepain/sessions/:sessionId', ({ params }) => {
        deletedSession = String(params.sessionId)
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: params.sessionId, deleted: true })
      }),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('待清空回答')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))

    await waitFor(() => expect(deletedSession).toBe('s1'))
    await waitFor(() => expect(getSearch()).toBe('?user=kesepain'))
  })

  it('上下文窗口抽屉展示七组真实聚合统计', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByTitle('查看上下文与运行状态'))

    expect(screen.getByText('Token 占用概览')).toBeInTheDocument()
    expect(screen.getByText('对话统计')).toBeInTheDocument()
    expect(screen.getByText('任务与定时')).toBeInTheDocument()
    expect(screen.getByText('工具与子智能体')).toBeInTheDocument()
    expect(screen.getByText('知识库状态')).toBeInTheDocument()
    expect(screen.getAllByText('外部消息').length).toBeGreaterThan(0)
    expect(screen.getByText('拓展与感知')).toBeInTheDocument()
    expect(screen.getAllByText('18.67%').length).toBeGreaterThan(0)
    expect(screen.getByText('已启动')).toBeInTheDocument()
    expect(document.querySelector('.context-drawer-body')).toHaveClass('drawer-body')
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
