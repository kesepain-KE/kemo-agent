import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { delay, http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell, injectionPolicyPresentation, persistLastActiveUser, readLastActiveUser, resolveCurrentUser } from './AppShell'
import { ChatPage } from '../pages/ChatPage'
import { SettingsPage } from '../pages/SettingsPage'
import { server } from '../test/server'
import { chatDraftKey, useChatDraftStore } from '../store/chatDrafts'
import { useScratchSender } from '../store/scratchNotes'
import type { SessionSummary } from '../types/api'
import { forgetSession, rememberSession } from '../sessionNavigation'
import type { ShellOutletContext } from './appShellTypes'

afterEach(() => {
  useScratchSender.setState({ handlers: {} })
  useChatDraftStore.getState().clearAll()
  localStorage.removeItem('kemo-last-active-user')
  forgetSession('kesepain')
  forgetSession('reviewer')
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('scratchpad sends through the existing chat endpoint without clearing the composer', async () => {
  const interceptedFetch = globalThis.fetch.bind(globalThis)
  const requests: Array<{ prompt: string; session_id: string }> = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
    requests.push(JSON.parse(String(init?.body)))
    return new Response('event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"completed"}}\n\n',
      { headers: { 'Content-Type': 'text/event-stream' } })
  }))
  renderApp('/chat?user=kesepain&session=s1')
  const composer = await screen.findByRole('textbox', { name: '消息内容' })
  fireEvent.change(composer, { target: { value: 'keep composer text' } })
  let accepted = false
  await act(async () => {
    accepted = await useScratchSender.getState().handlers[chatDraftKey('kesepain', 's1')]('scratch submission')
  })
  expect(accepted).toBe(true)
  expect(requests).toHaveLength(1)
  expect(requests[0]).toMatchObject({ prompt: 'scratch submission', session_id: 's1' })
  expect(composer).toHaveValue('keep composer text')
})

it('scratchpad queues while running and the later queued send preserves newly typed composer text', async () => {
  const interceptedFetch = globalThis.fetch.bind(globalThis)
  const prompts: string[] = []
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
    prompts.push(JSON.parse(String(init?.body)).prompt)
    const done = 'event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"completed"}}\n\n'
    return new Response(prompts.length === 1 ? new ReadableStream({
      start(value) { controller = value },
    }) : done, { headers: { 'Content-Type': 'text/event-stream' } })
  }))
  renderApp('/chat?user=kesepain&session=s1')
  const composer = await screen.findByRole('textbox', { name: '消息内容' })
  fireEvent.change(composer, { target: { value: 'first run' } })
  fireEvent.click(screen.getByRole('button', { name: '发送' }))
  await waitFor(() => expect(prompts).toHaveLength(1))
  try {
    await act(async () => {
      expect(await useScratchSender.getState().handlers[chatDraftKey('kesepain', 's1')]('queued scratch')).toBe(true)
    })
    fireEvent.change(composer, { target: { value: 'new composer text' } })
  } finally {
    controller?.enqueue(new TextEncoder().encode('event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"completed"}}\n\n'))
    controller?.close()
  }
  await waitFor(() => expect(prompts).toEqual(['first run', 'queued scratch']))
  expect(composer).toHaveValue('new composer text')
})

describe('AppShell user persistence', () => {
  it('pagehide releases only this document; hidden visibility cannot revive it and pageshow resumes in order', async () => {
    const transitions: Array<{ release: boolean; client_id: string; lease_sequence: number }> = []
    server.use(http.post('/api/users/kesepain/sessions/s1/lease*', async ({ request }) => {
      const body = await request.json() as { client_id: string; lease_sequence: number }
      transitions.push({ release: new URL(request.url).pathname.endsWith('/release'), ...body })
      return HttpResponse.json({ leased: true })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(transitions).toHaveLength(1))
    fireEvent(window, new Event('pagehide'))
    await waitFor(() => expect(transitions).toHaveLength(2))
    fireEvent(document, new Event('visibilitychange'))
    await act(async () => { await delay(50) })
    expect(transitions).toHaveLength(2)
    fireEvent(window, new Event('pageshow'))
    await waitFor(() => expect(transitions).toHaveLength(3))
    expect(transitions.map(item => item.release)).toEqual([false, true, false])
    expect(new Set(transitions.map(item => item.client_id)).size).toBe(1)
    expect(transitions[1].lease_sequence).toBeGreaterThan(transitions[0].lease_sequence)
    expect(transitions[2].lease_sequence).toBeGreaterThan(transitions[1].lease_sequence)
  })

  it('后端重启后从无参数页面恢复最近访问空间，即使它不在历史列表第一页', async () => {
    rememberSession('kesepain', 'selected-old')
    let restores = 0
    server.use(
      http.get('/api/users/kesepain/sessions/active', () => {
        restores += 1
        return HttpResponse.json({ session: { session_id: 'background-latest' } })
      }),
      http.get('/api/users/kesepain/sessions/selected-old/history', () => HttpResponse.json({
        user: 'kesepain', session_id: 'selected-old',
        messages: [{ role: 'user', content: '最近访问空间的正文' }], round_metrics: [], round_traces: [],
      })),
    )
    const app = renderApp('/chat?user=kesepain')
    await waitFor(() => expect(app.getSearch()).toBe('?user=kesepain&session=selected-old'))
    expect(await screen.findByText('最近访问空间的正文')).toBeInTheDocument()
    expect(restores).toBe(0)
  })

  it('URL 显式空间优先于缓存，并在切页后保留原空间和草稿', async () => {
    rememberSession('kesepain', 'cached-other')
    const app = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.change(await screen.findByRole('textbox', { name: '消息内容' }), { target: { value: '保留在原空间' } })
    fireEvent.click(screen.getByRole('link', { name: '配置' }))
    await screen.findByRole('heading', { name: '配置' })
    expect(app.getSearch()).toBe('?user=kesepain&session=s1')
    fireEvent.click(screen.getByRole('link', { name: '对话' }))
    expect(await screen.findByRole('textbox', { name: '消息内容' })).toHaveValue('保留在原空间')
    expect(app.getSearch()).toBe('?user=kesepain&session=s1')
  })

  it('新建此用户标签页保留原页面，并通过安全新标签链接请求独立对话', async () => {
    const app = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByRole('button', { name: '切换当前用户' }))
    const link = screen.getByRole('menuitem', { name: '新建此用户标签页' })
    expect(link).toHaveAttribute('href', '/chat?user=kesepain&new_session=1')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    expect(app.getSearch()).toBe('?user=kesepain&session=s1')
  })

  it('新标签页只创建独立对话，不恢复用户已有会话，成功后清除创建标记', async () => {
    let creates = 0
    let restores = 0
    server.use(
      http.post('/api/users/kesepain/sessions', async () => {
        creates += 1
        await delay(30)
        return HttpResponse.json({ user: 'kesepain', created: true, session: { session_id: 'fresh_tab', rounds: 0 } })
      }),
      http.get('/api/users/kesepain/sessions/active', () => {
        restores += 1
        return HttpResponse.json({ user: 'kesepain', session: { session_id: 's1', rounds: 2 } })
      }),
    )
    const app = renderApp('/chat?user=kesepain&new_session=1')
    expect(screen.queryByRole('textbox', { name: '消息内容' })).not.toBeInTheDocument()
    await waitFor(() => expect(app.getSearch()).toBe('?user=kesepain&session=fresh_tab'))
    expect(creates).toBe(1)
    expect(restores).toBe(0)
  })

  it('新标签创建失败不自动重试或退回旧对话，提供显式重试', async () => {
    let creates = 0
    server.use(http.post('/api/users/kesepain/sessions', () => {
      creates += 1
      return creates === 1
        ? HttpResponse.json({ error: { message: 'test' } }, { status: 500 })
        : HttpResponse.json({ user: 'kesepain', created: true, session: { session_id: 'retry_tab', rounds: 0 } })
    }))
    const app = renderApp('/chat?user=kesepain&new_session=1')
    fireEvent.click(await screen.findByRole('button', { name: '重试创建对话' }))
    await waitFor(() => expect(app.getSearch()).toBe('?user=kesepain&session=retry_tab'))
    expect(creates).toBe(2)
  })

  it('已清理对话恢复时解除绑定、刷新日期归档且不在开始页显示孤立计划', async () => {
    let creates = 0
    server.use(
      http.post('/api/users/kesepain/sessions/gone/lease', () => HttpResponse.json({ error: { message: 'gone' } }, { status: 404 })),
      http.post('/api/users/kesepain/sessions', () => {
        creates += 1
        return HttpResponse.json({ user: 'kesepain', created: true, session: { session_id: 'must_not_create', rounds: 0 } })
      }),
      http.get('/api/users/kesepain/overview', ({ request }) => {
        const selectedSession = new URL(request.url).searchParams.get('session_id') || ''
        return HttpResponse.json({
          user: 'kesepain', session_id: selectedSession,
          context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false }, limit: 120000, percent: 0, rounds: 0, round_limit: 30 },
          provider: { type: 'kemo', base_url: 'http://127.0.0.1:8741/v1', model: 'test-model', reasoning_effort: 'medium', timeout: 120, stream: false, credential_source: 'environment', configured: true },
          counts: { sessions: 0, knowledge_documents: 0, enabled_tools: 0, enabled_agents: 0, active_tasks: 1 },
          context_window: {
            tokens: { total_tokens: 0, capacity_tokens: 120000, percent: 0, source: 'unavailable' },
            conversation: { foreground_rounds: 0, archived_rounds: 0, session_total_rounds: 0, session_tool_calls: 0, total_tool_calls: 0 },
            tasks: { active_plans: 1, waiting_crons: 0 }, capabilities: { tools_enabled: 0, tools_disabled: 0, agents_enabled: 0 },
            knowledge: { enabled: 0, disabled: 0 }, messages: { connected: 0 }, integrations: { expands: 0, senses: 0 },
          },
          runtime_host: { state: 'unmanaged', components: {} }, activities: [],
          active_plan: {
            plan_id: 'orphan-plan', title: '不应出现在开始页的孤立计划', description: '来源会话已经清理',
            status: 'paused', source: 'web', session_id: 'gone', revision: 1,
            progress: { completed: 1, total: 2, percent: 50 }, steps: [], updated_at: '2026-08-25T12:00:00+08:00',
          },
        })
      }),
    )
    const app = renderApp('/chat?user=kesepain&session=gone')
    app.client.setQueryData(['history-sessions', 'kesepain', '2026-08-25'], {
      user: 'kesepain', source: 'all', date: '2026-08-25', sessions: [{ session_id: 'gone' }],
    })
    await screen.findByText(/此对话已被清理或删除/)
    await waitFor(() => expect(new URLSearchParams(app.getSearch()).has('session')).toBe(false))
    await waitFor(() => expect(app.client.getQueryState(['history-sessions', 'kesepain', '2026-08-25'])?.isInvalidated).toBe(true))
    await delay(30)
    expect(creates).toBe(0)
    expect(screen.queryByText('不应出现在开始页的孤立计划')).not.toBeInTheDocument()
  })

  it('把注入策略状态码稳定映射为三个只读状态', () => {
    expect(injectionPolicyPresentation('round').label).toBe('按轮注入')
    expect(injectionPolicyPresentation('realtime').label).toBe('实时注入')
    expect(injectionPolicyPresentation('disabled').label).toBe('不注入')
  })

  it('URL 用户优先于浏览器中保存的上次用户', () => {
    expect(resolveCurrentUser('alice', 'bob', [{ name: 'alice' }, { name: 'bob' }])).toBe('alice')
  })

  it('没有 URL 用户时恢复浏览器上次选择的有效用户', () => {
    expect(resolveCurrentUser('', 'bob', [{ name: 'alice' }, { name: 'bob' }, { name: 'carol' }])).toBe('bob')
  })

  it('上次用户已删除时回退到当前用户列表第一项', () => {
    expect(resolveCurrentUser('', 'removed', [{ name: 'alice' }, { name: 'bob' }])).toBe('alice')
  })

  it('浏览器缓存只保存最近用户名', () => {
    persistLastActiveUser(' bob ')
    expect(readLastActiveUser()).toBe('bob')
    expect(localStorage.getItem('kemo-last-active-user')).toBe('bob')
  })
})

describe('Windows run sound', () => {
  it('只在成功 done 后播放一次', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const play = vi.fn().mockResolvedValue(undefined)
    const AudioMock = vi.fn(function AudioMock(this: { play: typeof play }, _url: string) {
      this.play = play
    })
    vi.stubGlobal('Audio', AudioMock)
    let chatRequests = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequests += 1
      return new Response(
        'event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"completed"}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '音效测试已就绪' }], round_metrics: [], round_traces: [],
      })),
    )

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('音效测试已就绪')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(input, { target: { value: '完成后播放音效' } })
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(chatRequests).toBe(1))
    await waitFor(() => expect(AudioMock).toHaveBeenCalledOnce())
    expect(String(AudioMock.mock.calls[0][0])).toContain('/api/users/kesepain/completion-sound')
    expect(String(AudioMock.mock.calls[0][0])).not.toContain('?v=')
    expect(play).toHaveBeenCalledOnce()
  })

  it('最终失败 done 后播放失败音效一次，不播放成功音效', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const play = vi.fn().mockResolvedValue(undefined)
    const AudioMock = vi.fn(function AudioMock(this: { play: typeof play }, _url: string) {
      this.play = play
    })
    vi.stubGlobal('Audio', AudioMock)
    let chatRequests = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequests += 1
      return new Response(
        'event: done\ndata: {"type":"done","metadata":{"committed":false,"status":"failed"}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '失败音效测试已就绪' }], round_metrics: [], round_traces: [],
      })),
    )

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('失败音效测试已就绪')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(input, { target: { value: '触发失败音效' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(chatRequests).toBe(1))
    await waitFor(() => expect(AudioMock).toHaveBeenCalledOnce())
    expect(String(AudioMock.mock.calls[0][0])).toContain('/api/users/kesepain/failure-sound')
    expect(String(AudioMock.mock.calls[0][0])).not.toContain('/completion-sound')
    expect(play).toHaveBeenCalledOnce()
  })

  it('受限终态不播放音效', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const AudioMock = vi.fn()
    vi.stubGlobal('Audio', AudioMock)
    let chatRequests = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequests += 1
      return new Response(
        'event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"limited"}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '受限测试已就绪' }], round_metrics: [], round_traces: [],
      })),
    )

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('受限测试已就绪')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(input, { target: { value: '受限运行' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatRequests).toBe(1))
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
    expect(AudioMock).not.toHaveBeenCalled()
  })

  it('客户端流中断只显示未确认状态，不播放失败音效', async () => {
    vi.spyOn(window.navigator, 'userAgent', 'get').mockReturnValue('Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
    const AudioMock = vi.fn()
    vi.stubGlobal('Audio', AudioMock)
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      return new Response(
        'event: text_delta\ndata: {"type":"text_delta","content":"partial"}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '断流测试已就绪' }], round_metrics: [], round_traces: [],
      })),
    )

    renderApp('/chat?user=kesepain&session=s1')
    // 全量并行跑 68 个测试文件时，默认 1 秒的 findBy 等待在慢机器上会超时；
    // 放宽到 6 秒，仍能检出历史加载被真实阻塞的问题。
    await screen.findByText('断流测试已就绪', undefined, { timeout: 6000 })
    const input = await screen.findByRole('textbox', { name: '消息内容' }, { timeout: 6000 })
    fireEvent.change(input, { target: { value: '模拟断流' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect((await screen.findAllByText('响应流在终态事件到达前结束，未收到最终状态')).length).toBeGreaterThan(0)
    expect(AudioMock).not.toHaveBeenCalled()
  })
})

function renderApp(path = '/chat', chatPage = <ChatPage />) {
  let currentSearch = new URL(path, 'http://test').search
  let currentPathname = new URL(path, 'http://test').pathname
  let navigateRoute: ((target: string) => void) | undefined
  function LocationProbe() {
    const location = useLocation()
    currentSearch = location.search
    currentPathname = location.pathname
    return null
  }
  function NavigationProbe() {
    navigateRoute = useNavigate()
    return null
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <LocationProbe />
        <NavigationProbe />
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route path="chat" element={chatPage} />
            <Route path="settings" element={<SettingsPage />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return {
    getSearch: () => currentSearch,
    getPathname: () => currentPathname,
    navigate: (target: string) => {
      if (!navigateRoute) throw new Error('导航器尚未挂载')
      act(() => navigateRoute?.(target))
    },
    client,
  }
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

describe('历史个人标记与侧栏同步', () => {
  it('侧栏收藏后历史气泡读取全量计数，收藏过滤清除旧日期，并支持重命名同步', async () => {
    let record: SessionSummary = { ...session('s1', '标记验收对话', 1), source: 'web', state: 'open' }
    const requests: URL[] = []
    server.use(
      http.get('/api/users/kesepain/sessions', ({ request }) => {
        const url = new URL(request.url)
        requests.push(url)
        return HttpResponse.json({ user: 'kesepain', source: 'all', favorites_count: record.favorite ? 1 : 0,
          sessions: url.searchParams.get('favorites_only') === 'true' && !record.favorite ? [] : [record] })
      }),
      http.patch('/api/users/kesepain/sessions/s1/marks', async ({ request }) => {
        record = { ...record, ...await request.json() as object }
        return HttpResponse.json({ user: 'kesepain', source: 'web', session: record })
      }),
      http.patch('/api/users/kesepain/sessions/s1', async ({ request }) => {
        record = { ...record, ...await request.json() as object }
        return HttpResponse.json({ user: 'kesepain', source: 'web', session: record })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByRole('button', { name: '对话操作 标记验收对话' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '收藏' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '对话操作 标记验收对话' })).toHaveAttribute('aria-expanded', 'false'))
    fireEvent.click(screen.getByTitle('搜索历史对话'))
    const drawer = screen.getByRole('dialog', { name: '历史对话' })
    fireEvent.click(await within(drawer).findByRole('button', { name: '收藏 1' }))
    await waitFor(() => expect(requests.some(url => url.searchParams.get('favorites_only') === 'true' && !url.searchParams.has('date'))).toBe(true))
    fireEvent.click(within(drawer).getByRole('button', { name: '对话操作 标记验收对话' }))
    fireEvent.click(screen.getByRole('menuitem', { name: '重命名' }))
    fireEvent.change(screen.getByRole('textbox', { name: '对话名称' }), { target: { value: '新的验收标题' } })
    fireEvent.click(screen.getByRole('button', { name: '保存名称' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    expect(await within(drawer).findByRole('button', { name: '打开对话 新的验收标题' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: '对话操作 新的验收标题' })).toHaveLength(2)
  })
})

describe('新建对话关键路径', () => {
  const createdResponse = (id: string, clientId: string) => ({
    user: 'kesepain', created: true, client_id: clientId, active_clients: 1,
    session: { session_id: id, source: 'web', state: 'open', rounds: 0,
      window: '', title: '', updated_at: '2026-10-15T10:00:00Z' },
  })

  it('首次返回已有计划时仍刷新正文，不漏掉首屏加载期间完成的任务结果', async () => {
    let historyReads = 0
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => {
        historyReads += 1
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: 's1',
          messages: [{ role: 'user', content: 'Fixture question' }, { role: 'assistant',
            content: historyReads === 1 ? '等待任务完成' : '任务已完成后的归档' }],
          round_metrics: [], round_traces: [] })
      }),
      http.get('/api/users/kesepain/tasks', async () => {
        await delay(100)
        return HttpResponse.json({ user: 'kesepain',
          summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 1 },
          plans: [{ plan_id: 'plan_fixture', title: 'Fixture plan', description: 'Fixture only',
            source: 'web', session_id: 's1', status: 'completed', auto_accept: false,
            reminder: '', revision: 1, current_step: '', created_at: '', updated_at: '',
            progress: { completed: 0, total: 0, percent: 0 }, steps: [] }],
          cron_tasks: [], executions: [] })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    expect(await screen.findByText('任务已完成后的归档')).toBeInTheDocument()
    expect(historyReads).toBeGreaterThanOrEqual(2)
  })

  it('新标签使用创建回执即可读取正文，不等待慢列表、概览或重复租约请求', async () => {
    let release!: () => void
    const gate = new Promise<void>(resolve => { release = resolve })
    const overviewSessions: string[] = []
    let leaseCalls = 0
    let historyReads = 0
    server.use(
      http.post('/api/users/kesepain/sessions', async ({ request }) => {
        const body = await request.json() as { client_id: string }
        return HttpResponse.json(createdResponse('fast_tab', body.client_id))
      }),
      http.get('/api/users/kesepain/sessions', async () => { await gate }),
      http.get('/api/users/kesepain/overview', async ({ request }) => {
        overviewSessions.push(new URL(request.url).searchParams.get('session_id') || '')
        await gate
      }),
      http.post('/api/users/kesepain/sessions/fast_tab/lease', async () => {
        leaseCalls += 1
        await gate
        return HttpResponse.json({ leased: true })
      }),
      http.get('/api/users/kesepain/sessions/fast_tab/history', () => {
        historyReads += 1
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: 'fast_tab',
          messages: [], round_metrics: [], round_traces: [] })
      }),
      http.get('/api/users/kesepain/tasks', async () => { await delay(100) }),
    )
    try {
      const app = renderApp('/chat?user=kesepain&new_session=1')
      await waitFor(() => expect(app.getSearch()).toBe('?user=kesepain&session=fast_tab'))
      await waitFor(() => expect(historyReads).toBe(1))
      expect(screen.getByRole('textbox', { name: '消息内容' })).toBeEnabled()
      expect(leaseCalls).toBe(0)
      expect(overviewSessions).toEqual(['fast_tab'])
      // Initial tasks loading must not invalidate and refetch the blank history.
      await act(async () => { await delay(150) })
      expect(historyReads).toBe(1)
    } finally { release() }
  })

  it('保存新建保持先关闭后创建，连点只执行一次且不再刷新旧空间概览', async () => {
    let closeCalls = 0
    let createCalls = 0
    let leaseCalls = 0
    let releaseClose!: () => void
    let releaseReads!: () => void
    const closeGate = new Promise<void>(resolve => { releaseClose = resolve })
    const readGate = new Promise<void>(resolve => { releaseReads = resolve })
    const overviewSessions: string[] = []
    const app = renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    await waitFor(() => expect(app.client.getQueryState(['overview', 'kesepain', 's1'])?.status).toBe('success'))
    fireEvent.change(composer, { target: { value: '旧空间的草稿' } })
    server.use(
      http.post('/api/users/kesepain/sessions/s1/close', async () => {
        closeCalls += 1
        await closeGate
        return HttpResponse.json({ user: 'kesepain', closed: true, session_id: 's1' })
      }),
      http.post('/api/users/kesepain/sessions', async ({ request }) => {
        createCalls += 1
        const body = await request.json() as { client_id: string }
        return HttpResponse.json(createdResponse('fast_saved', body.client_id))
      }),
      http.get('/api/users/kesepain/overview', async ({ request }) => {
        overviewSessions.push(new URL(request.url).searchParams.get('session_id') || '')
        await readGate
      }),
      http.post('/api/users/kesepain/sessions/fast_saved/lease', () => {
        leaseCalls += 1
        return HttpResponse.json({ leased: true })
      }),
      http.get('/api/users/kesepain/sessions/fast_saved/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 'fast_saved',
        messages: [], round_metrics: [], round_traces: [],
      })),
    )
    try {
      fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
      const save = screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ })
      fireEvent.click(save)
      fireEvent.click(save)
      await waitFor(() => expect(closeCalls).toBe(1))
      expect(createCalls).toBe(0)
      expect(overviewSessions).toEqual([])
      releaseClose()
      await waitFor(() => expect(app.getSearch()).toContain('session=fast_saved'))
      expect(createCalls).toBe(1)
      expect(closeCalls).toBe(1)
      expect(leaseCalls).toBe(0)
      await waitFor(() => expect(overviewSessions).toEqual(['fast_saved']))
      expect(screen.getByRole('textbox', { name: '消息内容' })).toBeEnabled()
      expect(useChatDraftStore.getState().drafts[chatDraftKey('kesepain', 's1')]).toBeUndefined()
    } finally { releaseClose(); releaseReads() }
  })

  it.each(['legacy', 'ordered', 'unconfirmed'])('保存关闭未完成时不新建、不丢草稿且可重试（%s）', async (mode) => {
    const deferred = mode !== 'unconfirmed'
    let closed = false
    let creates = 0
    server.use(
      http.post('/api/users/kesepain/sessions/s1/close', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1', closed,
        deferred: !closed && deferred, active_clients: closed ? 0 : 1,
        ...(mode === 'ordered' ? { retry_after_seconds: 37 } : {}),
        memory: { status: closed ? 'queued' : 'skipped', reason: 'session_in_use_by_other_clients' },
      })),
      http.post('/api/users/kesepain/sessions', async ({ request }) => {
        creates += 1
        const body = await request.json() as { client_id: string }
        return HttpResponse.json(createdResponse('saved_after_retry', body.client_id))
      }),
      http.get('/api/users/kesepain/sessions/saved_after_retry/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 'saved_after_retry',
        messages: [], round_metrics: [], round_traces: [],
      })),
    )
    const app = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.change(await screen.findByRole('textbox', { name: '消息内容' }), {
      target: { value: '关闭未完成时保留的草稿' },
    })
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ }))
    const feedback = mode === 'ordered'
      ? '另有 1 个页面或客户端仍在使用此对话，尚未关闭，也未启动后台提取。请关闭其他页面后重试；旧页面停止心跳后，租约预计最多 37 秒过期。'
      : deferred
      ? '此对话仍被其他页面或客户端使用，尚未关闭，也未启动后台提取。请关闭其他页面后重试；异常断开的页面可能需要等待在线租约过期。'
      : '当前对话尚未确认关闭，未创建新对话，请重试。'
    expect(await screen.findByText(feedback)).toBeInTheDocument()
    expect(app.getSearch()).toContain('session=s1')
    expect(creates).toBe(0)
    expect(useChatDraftStore.getState().drafts[chatDraftKey('kesepain', 's1')]?.text).toBe('关闭未完成时保留的草稿')
    await waitFor(() => expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('关闭未完成时保留的草稿'))
    expect(screen.getByText(feedback)).toBeInTheDocument()
    closed = true
    fireEvent.click(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ }))
    await waitFor(() => expect(app.getSearch()).toContain('session=saved_after_retry'))
    expect(creates).toBe(1)
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    expect(screen.queryByText(feedback)).not.toBeInTheDocument()
  })

  it('同一空间并发新建复用一次创建请求及同一个 Promise', async () => {
    let creates = 0
    let samePromise = false
    let results: Array<string | undefined> = []
    function CreationProbe() {
      const ctx = useOutletContext<ShellOutletContext>()
      return <button onClick={() => {
        const first = ctx.createNewSession()
        const second = ctx.createNewSession()
        samePromise = first === second
        void Promise.all([first, second]).then(value => { results = value })
      }}>并发新建</button>
    }
    server.use(http.post('/api/users/kesepain/sessions', async ({ request }) => {
      creates += 1
      await delay(50)
      const body = await request.json() as { client_id: string }
      return HttpResponse.json(createdResponse('single_create', body.client_id))
    }))
    const app = renderApp('/chat?user=kesepain&session=s1', <CreationProbe />)
    fireEvent.click(await screen.findByRole('button', { name: '并发新建' }))
    await waitFor(() => expect(results).toEqual(['single_create', 'single_create']))
    expect(creates).toBe(1)
    expect(samePromise).toBe(true)
    expect(app.getSearch()).toContain('session=single_create')
  })

  it('保存后新建失败保留原草稿，不自动恢复已关闭的空间', async () => {
    let restores = 0
    server.use(
      http.post('/api/users/kesepain/sessions', () =>
        HttpResponse.json({ error: { message: 'Fixture creation failed' } }, { status: 500 })),
      http.get('/api/users/kesepain/sessions/active', () => {
        restores += 1
        return HttpResponse.json({ user: 'kesepain', session: session('s1', 'Closed', 1) })
      }),
    )
    const app = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.change(await screen.findByRole('textbox', { name: '消息内容' }), {
      target: { value: '创建失败时不能丢失的草稿' },
    })
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ }))
    expect(await screen.findByText('Fixture creation failed')).toBeInTheDocument()
    expect(useChatDraftStore.getState().drafts[chatDraftKey('kesepain', 's1')].text)
      .toBe('创建失败时不能丢失的草稿')
    expect(app.getSearch()).not.toContain('session=s1')
    expect(restores).toBe(0)
  })
})

describe('AppShell navigation', () => {
  it('加载真实用户并展示空聊天入口', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    await waitFor(() => expect(localStorage.getItem('kemo-last-active-user')).toBe('kesepain'))
    expect(await screen.findByText(/kemo-agent 已就绪/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '切换当前用户' })).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: '核心工作区' })).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: '运行能力' })).toBeInTheDocument()
    expect(screen.getByRole('separator', { name: '资源与系统' })).toBeInTheDocument()
  })

  it('欢迎页移除快捷开始栏但保留其他入口', async () => {
    renderApp('/chat?user=kesepain')

    expect(await screen.findByText(/kemo-agent 已就绪/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看当前感知情况/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /查看拓展模块状态/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /诊断运行状态/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /创建定时任务/ })).not.toBeInTheDocument()
    expect(screen.getByText('系统状态')).toBeInTheDocument()
    expect(screen.getByText('总 Token')).toBeInTheDocument()
    expect(screen.getByText('Token 命中率')).toBeInTheDocument()
    expect(screen.getByText('输出 Token')).toBeInTheDocument()
  })

  it('对话首屏只请求最新20轮并可向上加载更早轮次', async () => {
    const requestedBefore: Array<string | null> = []
    server.use(http.get('/api/users/kesepain/sessions/s1/history', ({ request }) => {
      const url = new URL(request.url)
      requestedBefore.push(url.searchParams.get('before'))
      expect(url.searchParams.get('limit')).toBe('20')
      if (url.searchParams.get('before') === '21') {
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1',
          messages: [
            { role: 'user', content: '第1轮问题' },
            { role: 'assistant', content: '第1轮回复' },
          ],
          round_metrics: [], round_traces: [],
          pagination: { limit: 20, total_rounds: 21, first_round: 1, last_round: 20, has_more_before: false, next_before: null },
        })
      }
      return HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [
          { role: 'user', content: '第21轮问题' },
          { role: 'assistant', content: '第21轮回复' },
        ],
        round_metrics: [], round_traces: [],
        pagination: { limit: 20, total_rounds: 21, first_round: 21, last_round: 21, has_more_before: true, next_before: 21 },
      })
    }))
    renderApp('/chat?user=kesepain&session=s1')

    expect(await screen.findByText('第21轮问题')).toBeInTheDocument()
    expect(screen.queryByText('第1轮问题')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '加载更早对话' }))
    expect(await screen.findByText('第1轮问题')).toBeInTheDocument()
    expect(requestedBefore).toContain(null)
    expect(requestedBefore).toContain('21')
    expect(screen.getByText('已到达对话开头')).toBeInTheDocument()
  })

  it('用户消息优先显示当前用户头像并在加载失败时回退图标', async () => {
    server.use(http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: 's1',
      messages: [
        { role: 'user', content: '头像显示测试' },
        { role: 'assistant', content: '收到' },
      ],
      round_metrics: [], round_traces: [],
    })))
    renderApp('/chat?user=kesepain&session=s1')

    expect(await screen.findByText('头像显示测试')).toBeInTheDocument()
    const avatar = document.querySelector<HTMLImageElement>('.msg.is-user .user-message-avatar img')
    expect(avatar?.getAttribute('src')).toBe('/api/users/kesepain/avatar?v=0')
    const avatarFrame = screen.getByRole('img', { name: 'kesepain头像' })
    expect(avatarFrame.parentElement).toHaveClass('msg', 'is-user')
    expect(avatarFrame.nextElementSibling).toHaveClass('msg-body')
    const agentAvatar = document.querySelector('.msg.is-assistant > .msg-avatar')
    expect(agentAvatar?.querySelector('.kemo-logo')).not.toBeNull()
    expect(agentAvatar?.nextElementSibling).toHaveClass('msg-body')
    expect(document.querySelector('.chat-stream .msg-head')).not.toBeInTheDocument()
    fireEvent.error(avatar!)
    expect(document.querySelector('.msg.is-user .user-message-avatar img')).not.toBeInTheDocument()
    expect(document.querySelector('.msg.is-user .user-message-avatar svg')).toBeInTheDocument()
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

    await screen.findByText(/kemo-agent 已就绪/)
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '保持运行' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByRole('button', { name: '停止生成' })
    await chatStarted

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '用户切换' }))
    const userRow = await screen.findByRole('button', { name: '切换到用户 reviewer' })
    expect(userRow).toBeDisabled()
    expect(screen.getByText('对话运行中，暂不可切换')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '切换当前用户' }))
    expect(screen.getByRole('menuitem', { name: /reviewer/ })).toBeDisabled()

    fireEvent.click(screen.getByRole('link', { name: /^对话$/ }))
    const abortedBeforeStop = chatSignal?.aborted
    fireEvent.click(await screen.findByRole('button', { name: '停止生成' }))
    await waitFor(() => expect(screen.getByRole('button', { name: '停止生成' })).toHaveTextContent('正在停止'))
    expect(chatSignal?.aborted).toBe(abortedBeforeStop)

    releaseChat()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '用户切换' }))
    expect(await screen.findByRole('button', { name: '切换到用户 reviewer' })).toBeEnabled()
  })

  it('长任务取消响应晚于 SSE 终态时不会重新锁死发送按钮', async () => {
    let firstStreamController!: ReadableStreamDefaultController<Uint8Array>
    let releaseCancel!: () => void
    let markCancelStarted!: () => void
    const cancelGate = new Promise<void>((resolve) => { releaseCancel = resolve })
    const cancelStarted = new Promise<void>((resolve) => { markCancelStarted = resolve })
    let chatRequests = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequests += 1
      if (chatRequests === 1) {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) { firstStreamController = controller },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return new Response(
        'event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"completed"}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    const longTaskState = (status: string) => ({
      enabled: status === 'running', status, task_id: 'long_task_pause_race', original_prompt: '暂停竞态测试',
      started_at: '2026-09-13T08:00:00+08:00', updated_at: '2026-09-13T08:01:00+08:00', finished_at: '',
      run_count: 1, continuation_count: 0, total_tool_calls: 1, total_provider_requests: 1,
      active_elapsed_ms: 1000, usage: {}, current_run_id: status === 'running' ? 'server-run' : '',
      last_stop_reason: status === 'cancelled' ? 'user_emergency_stop' : '', cancel_requested: status === 'cancelled', last_error: null,
    })
    server.use(
      http.get('/api/users/kesepain/sessions/s1/long-task', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1', long_task: longTaskState('running'),
      })),
      http.post('/api/users/kesepain/sessions/s1/long-task/cancel', async () => {
        markCancelStarted()
        await cancelGate
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', long_task: longTaskState('cancelled'),
        })
      }),
    )

    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '开始长任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatRequests).toBe(1))
    fireEvent.click(await screen.findByRole('button', { name: '停止长任务' }))
    await cancelStarted

    firstStreamController.enqueue(new TextEncoder().encode(
      'event: done\ndata: {"type":"done","metadata":{"committed":false,"status":"cancelled","cancelled":true}}\n\n',
    ))
    firstStreamController.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())

    releaseCancel()
    await waitFor(() => expect(screen.queryByRole('button', { name: '正在停止…' })).not.toBeInTheDocument())
    fireEvent.change(composer, { target: { value: '暂停后继续发送' } })
    const sendButton = screen.getByRole('button', { name: '发送' })
    expect(sendButton).toBeEnabled()
    fireEvent.click(sendButton)
    await waitFor(() => expect(chatRequests).toBe(2))
  })

  it('不同会话并行运行时保留各自的停止控制器', async () => {
    const encoder = new TextEncoder()
    const streamControllers = new Map<string, ReadableStreamDefaultController<Uint8Array>>()
    const requestSignals = new Map<string, AbortSignal | null | undefined>()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      const body = JSON.parse(String(init?.body || '{}')) as { session_id?: string }
      const sessionId = String(body.session_id || '')
      requestSignals.set(sessionId, init?.signal)
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamControllers.set(sessionId, controller)
          init?.signal?.addEventListener('abort', () => {
            try {
              controller.error(new DOMException('Aborted', 'AbortError'))
            } catch {
              // The stream may already have been closed by the test cleanup.
            }
          }, { once: true })
        },
      })
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
    }))
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
        user: 'kesepain', source: 'web', sessions: [session('s1', '会话 A', 1), session('s2', '会话 B', 2)],
      })),
      http.get('/api/users/kesepain/sessions/:sessionId/history', ({ params }) => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: params.sessionId,
        messages: [], round_metrics: [], round_traces: [],
      })),
    )

    const app = renderApp('/chat?user=kesepain&session=s1')
    const firstInput = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(firstInput, { target: { value: '会话 A 运行' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByRole('button', { name: '停止生成' })
    await waitFor(() => expect(requestSignals.has('s1')).toBe(true))

    app.navigate('/chat?user=kesepain&session=s2')
    await screen.findByRole('textbox', { name: '消息内容' })
    expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '会话 B 运行' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(requestSignals.has('s2')).toBe(true))
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
    streamControllers.get('s2')?.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":false,"status":"cancelled","cancelled":true}}\n\n'))
    streamControllers.get('s2')?.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())

    app.navigate('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument())
    streamControllers.get('s1')?.enqueue(encoder.encode('event: done\ndata: {"type":"done"}\n\n'))
    streamControllers.get('s1')?.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
  })

  it('切换页面后继续接收流式事件并在返回对话页时恢复现场', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    let markChatStarted!: () => void
    const chatStarted = new Promise<void>((resolve) => { markChatStarted = resolve })
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller
          markChatStarted()
        },
      })
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '跨页面运行测试' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await chatStarted

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    await screen.findByRole('heading', { name: '配置' })
    streamController.enqueue(encoder.encode('event: text_delta\ndata: {"type":"text_delta","content":"切页后仍然可见"}\n\n'))

    fireEvent.click(screen.getByRole('link', { name: /^对话$/ }))
    expect(await screen.findByText('跨页面运行测试')).toBeInTheDocument()
    expect(await screen.findByText('切页后仍然可见')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument()

    streamController.enqueue(encoder.encode('event: done\ndata: {"type":"done"}\n\n'))
    streamController.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
  })

  it('首轮运行期间立即绑定空白空间，切页不丢流式事件与后台完成结果', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    let requestSignal: AbortSignal | null | undefined
    let chatRequests = 0
    let committed = false
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      expect(JSON.parse(String(init?.body)).session_id).toBe('web_first_run')
      requestSignal = init?.signal
      chatRequests += 1
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) { streamController = controller },
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/active', () => HttpResponse.json({
        user: 'kesepain', created: true, session: { ...session('web_first_run', '', 0), state: 'open' },
      })),
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
        user: 'kesepain', source: 'web', sessions: [],
      })),
      http.get('/api/users/kesepain/sessions/web_first_run/history', () => HttpResponse.json({
        user: 'kesepain', session_id: 'web_first_run',
        messages: committed
          ? [{ role: 'user', content: '首轮跨页任务' }, { role: 'assistant', content: '首轮后台完成' }] : [],
        round_metrics: [], round_traces: [],
      })),
    )
    const app = renderApp('/chat?user=kesepain')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(input, { target: { value: '首轮跨页任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatRequests).toBe(1))
    expect(app.getSearch()).toBe('?user=kesepain&session=web_first_run')
    fireEvent.click(screen.getByRole('link', { name: '配置' }))
    await screen.findByRole('heading', { name: '配置' })
    expect(app.getSearch()).toBe('?user=kesepain&session=web_first_run')
    streamController.enqueue(encoder.encode('event: text_delta\ndata: {"type":"text_delta","content":"首轮后台完成"}\n\n'))
    committed = true
    streamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n'))
    streamController.close()
    fireEvent.click(screen.getByRole('link', { name: '对话' }))
    expect(await screen.findByText('首轮后台完成')).toBeInTheDocument()
    expect(requestSignal?.aborted).toBe(false)
    expect(chatRequests).toBe(1)
    expect(app.getSearch()).toBe('?user=kesepain&session=web_first_run')
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
      http.post('/api/users/kesepain/sessions/web_uncommitted/lease', () => (
        HttpResponse.json({ error: { message: '未预留的会话不存在' } }, { status: 404 })
      )),
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
      http.get('/api/users/kesepain/sessions/active', () => HttpResponse.json({
        user: 'kesepain', created: true, session: { ...session('web_first_test', '', 0), state: 'open' },
      })),
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
        messages: committedSessionId ? [
          { role: 'user', content: '首轮测试', round: 1 },
          { role: 'assistant', content: '历史回复', round: 1 },
        ] : [],
        round_metrics: [],
        round_traces: [],
      })),
    )
    const { getSearch } = renderApp('/chat?user=kesepain')
    const welcomeText = /kemo-agent 已就绪/
    await screen.findByText(welcomeText)
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '首轮测试' } })
    const sendButton = screen.getByRole('button', { name: '发送' })
    await waitFor(() => expect(sendButton).toBeEnabled())
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
    expect(screen.getAllByTitle('界面比例').length).toBeGreaterThan(0)
    expect(screen.getByTitle('切换主题')).toBeInTheDocument()
    expect(screen.getByTitle('运行诊断')).toBeInTheDocument()
    expect(screen.getByTitle('搜索历史对话')).toBeInTheDocument()
    expect(await screen.findByTitle('模型与思考档位')).toBeInTheDocument()
  })

  it('文件历史只展示当前对话的来源与输出文件', async () => {
    server.use(http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: 's1',
      messages: [{
        role: 'user',
        content: '查看附件',
        attachments: [{
          asset_id: 'source-1', name: '输入图片.png', media_kind: 'image', mime_type: 'image/png',
          size: 1024, checksum_sha256: 'a'.repeat(64), scope: 'file_upload',
          relative_path: 'uploads/input.png', available: true,
        }],
      }],
      round_metrics: [{
        round: 1, usage: {}, elapsed_ms: 1, tool_calls: 0, guidance: [],
        artifacts: [{
          asset_id: 'output-1', type: 'file', name: '结果报告.pdf', scope: 'download',
          path: 'artifacts/result.pdf', project_path: '', mime_type: 'application/pdf',
          size: 2048, checksum_sha256: 'b'.repeat(64),
        }],
      }],
      round_traces: [],
    })))
    renderApp('/chat?user=kesepain&session=s1')
    const trigger = await screen.findByRole('button', { name: '当前对话文件历史' })
    fireEvent.click(trigger)
    const panel = await screen.findByRole('region', { name: '当前对话文件历史' })
    expect(panel).toHaveTextContent('相关文件')
    expect(panel).not.toHaveTextContent('只显示与此对话空间关联的文件')
    expect(await within(panel).findByText('输入图片.png')).toBeInTheDocument()
    expect(within(panel).getByText('结果报告.pdf')).toBeInTheDocument()
    expect(within(panel).getByText('来源')).toBeInTheDocument()
    expect(within(panel).getByText('输出内容')).toBeInTheDocument()
    expect(panel.querySelectorAll('a[download]')).toHaveLength(2)
    fireEvent.click(within(panel).getByRole('button', { name: '引用 输入图片.png' }))
    expect(await screen.findByRole('listitem', { name: '待发送附件：输入图片.png' })).toBeInTheDocument()
  })

  it.each(['chat', 'kemo'])('输入框模型切换使用 %s 对应目录，保存后同步顶栏与思考能力且保留草稿', async (protocol) => {
    let model = 'original-model'
    let savedChanges: unknown
    let catalogRequests = 0
    const capabilityModels: string[] = []
    server.use(
      http.get('/api/users/kesepain/overview', () => HttpResponse.json({
        user: 'kesepain', session_id: 's1',
        context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 }, limit: 120000, percent: 0, rounds: 2, round_limit: 30 },
        provider: { type: protocol, model, base_url: 'https://provider.test/v1', reasoning_effort: 'medium', configured: true, stream: true, timeout: 120, credential_source: 'environment' },
        counts: { sessions: 1, knowledge_documents: 0, enabled_tools: 0, enabled_agents: 0, active_tasks: 0 },
        context_window: {
          tokens: { total_tokens: 0, capacity_tokens: 120000, percent: 0, source: 'unavailable' },
          conversation: { foreground_rounds: 2, archived_rounds: 0, session_total_rounds: 2 },
          tasks: { active_plans: 0, waiting_crons: 0 }, capabilities: { tools_enabled: 0, tools_disabled: 0, agents_enabled: 0 },
          knowledge: { enabled: 0, disabled: 0 }, messages: { connected: 0 }, integrations: { expands: 0, senses: 0 },
        },
        runtime_host: { state: 'unmanaged', components: {} }, activities: [], active_plan: null,
      })),
      http.get(`/api/users/kesepain/provider/${protocol === 'chat' ? 'chat-models' : 'models'}`, () => {
        catalogRequests += 1
        return HttpResponse.json({ protocol, data: [
          { id: 'original-model', owned_by: 'vendor', provider_id: 'vendor', task: 'llm' },
          { id: 'next-model', owned_by: 'vendor', provider_id: 'vendor', task: 'llm' },
        ] })
      }),
      http.get('/api/users/kesepain/provider/model-capabilities', ({ request }) => {
        capabilityModels.push(new URL(request.url).searchParams.get('model') || '')
        return HttpResponse.json({
          capabilities: {
            reasoning: { supported: true, efforts: ['low', 'medium', 'high'] },
            extensions: {},
          },
        })
      }),
      http.patch('/api/users/kesepain/config', async ({ request }) => {
        savedChanges = (await request.json() as { changes: unknown }).changes
        await delay(30)
        model = 'next-model'
        return HttpResponse.json({ updated: true })
      }),
    )
    const app = renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getByRole('button', { name: '输入框切换模型' })).toBeEnabled())
    const trigger = screen.getByRole('button', { name: '输入框切换模型' })
    const textarea = screen.getByRole('textbox', { name: '消息内容' })
    fireEvent.change(textarea, { target: { value: '保留待发送草稿' } })
    expect(catalogRequests).toBe(0)
    fireEvent.click(trigger)
    fireEvent.click(await screen.findByRole('option', { name: 'next-model vendor' }))
    await waitFor(() => expect(trigger).toBeDisabled())
    expect(screen.getByRole('combobox', { name: '输入框思考档位' })).toBeDisabled()
    await waitFor(() => expect(trigger).toHaveTextContent('next-model'))
    await waitFor(() => expect(trigger).toBeEnabled())
    expect(screen.getByTitle('模型与思考档位')).toHaveTextContent('next-model')
    expect(textarea).toHaveValue('保留待发送草稿')
    expect(savedChanges).toEqual({ provider: { model: 'next-model' } })
    expect(app.getPathname()).toBe('/chat')
    expect(app.getSearch()).toBe('?user=kesepain&session=s1')
    expect(catalogRequests).toBe(1)
    if (protocol === 'kemo') await waitFor(() => expect(capabilityModels).toContain('next-model'))
    else expect(capabilityModels).toHaveLength(0)
  })

  it('输入框直接展开档位气泡并保存，不跳配置页', async () => {
    let savedChanges: unknown
    server.use(http.patch('/api/users/kesepain/config', async ({ request }) => {
      const body = await request.json() as { changes: unknown }
      savedChanges = body.changes
      return HttpResponse.json({ updated: true })
    }))
    const app = renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getByRole('combobox', { name: '输入框思考档位' })).toBeEnabled())
    const trigger = screen.getByRole('combobox', { name: '输入框思考档位' })
    fireEvent.click(trigger)
    expect(screen.getByRole('listbox', { name: '输入框思考档位选项' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /高.*深度推理/ }))
    await waitFor(() => expect(savedChanges).toEqual({ provider: { reasoning_effort: 'high' } }))
    expect(app.getPathname()).toBe('/chat')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('输入框档位保存失败保留原值并显示错误，支持重试', async () => {
    server.use(http.patch('/api/users/kesepain/config', () => HttpResponse.json({ error: { message: '保存失败' } }, { status: 500 })))
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getByRole('combobox', { name: '输入框思考档位' })).toBeEnabled())
    const trigger = screen.getByRole('combobox', { name: '输入框思考档位' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: /高.*深度推理/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('思考强度保存失败')
    expect(trigger).toHaveTextContent('中度')
    expect(trigger).toBeEnabled()
  })

  it('输入框档位气泡点击外部或 Escape 关闭，不发送保存请求', async () => {
    let patches = 0
    server.use(http.patch('/api/users/kesepain/config', () => { patches += 1; return HttpResponse.json({ updated: true }) }))
    renderApp('/chat?user=kesepain&session=s1')
    await waitFor(() => expect(screen.getByRole('combobox', { name: '输入框思考档位' })).toBeEnabled())
    const trigger = screen.getByRole('combobox', { name: '输入框思考档位' })
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    fireEvent.pointerDown(document.body)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(patches).toBe(0)
  })

  it('顶部模型气泡可单独保存思考强度', async () => {
    let savedChanges: Record<string, unknown> | undefined
    server.use(http.patch('/api/users/kesepain/config', async ({ request }) => {
      const body = await request.json() as { changes: Record<string, unknown> }
      savedChanges = body.changes
      return HttpResponse.json({ user: 'kesepain', config: body.changes, redacted_paths: [], updated: true })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const providerButton = await screen.findByTitle('模型与思考档位')
    await waitFor(() => expect(providerButton).toHaveTextContent('test-model'))
    fireEvent.click(providerButton)
    fireEvent.click(screen.getByRole('combobox', { name: '顶部模型思考强度' }))
    expect(screen.getByRole('listbox', { name: '顶部模型思考强度选项' }).parentElement).toBe(document.body)
    const option = screen.getByRole('option', { name: /高.*深度推理/ })
    fireEvent.pointerDown(option)
    fireEvent.mouseDown(option)
    expect(screen.getByRole('combobox', { name: '顶部模型思考强度' })).toBeInTheDocument()
    fireEvent.click(option)
    await waitFor(() => expect(savedChanges).toEqual({ provider: { reasoning_effort: 'high' } }))
  })

  it('Kemo 顶部模型气泡完全按能力声明展示新档位、排除 none 并原样保存', async () => {
    let savedChanges: Record<string, unknown> | undefined
    server.use(
      http.get('/api/users/kesepain/provider/model-capabilities', ({ request }) => {
        const model = new URL(request.url).searchParams.get('model') || 'test-model'
        return HttpResponse.json({
          user: 'kesepain', protocol: 'kemo', api_valid: true, model, stale: false, warning: '',
          capabilities: {
            model, task: 'llm', input_modalities: ['text'], output_modalities: ['text'], streaming: true,
            reasoning: { supported: true, efforts: ['none', 'low', 'ultra'], summary: true, persisted_state: false },
            tools: { function_calling: true, parallel_calls: false, multimodal_results: false },
            structured_output: true, metadata: {},
            extensions: {
              reasoning_effort_map: { low: 'low', ultra: 'high' },
              reasoning_policy: { mode: 'mapped', logical_efforts: ['low', 'ultra'], collapsed: true },
            },
          },
        })
      }),
      http.patch('/api/users/kesepain/config', async ({ request }) => {
        const body = await request.json() as { changes: Record<string, unknown> }
        savedChanges = body.changes
        return HttpResponse.json({ user: 'kesepain', config: body.changes, redacted_paths: [], updated: true })
      }),
    )

    renderApp('/chat?user=kesepain&session=s1')
    const providerButton = await screen.findByTitle('模型与思考档位')
    await waitFor(() => expect(providerButton).toHaveTextContent('test-model'))
    fireEvent.click(providerButton)
    expect(screen.getByText('此模型的部分思考档位会映射到相同的上游强度。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('combobox', { name: '顶部模型思考强度' }))
    expect(screen.getByRole('option', { name: /低.*轻度推理/ })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /ultra.*Kemo 网关声明档位/ })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /none/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: /中.*均衡/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /ultra.*Kemo 网关声明档位/ }))
    await waitFor(() => expect(savedChanges).toEqual({ provider: { reasoning_effort: 'ultra' } }))
  })

  it('Kemo 模型声明不支持推理时不展示固定五档', async () => {
    server.use(http.get('/api/users/kesepain/provider/model-capabilities', ({ request }) => {
      const model = new URL(request.url).searchParams.get('model') || 'test-model'
      return HttpResponse.json({
        user: 'kesepain', protocol: 'kemo', api_valid: true, model, stale: false, warning: '',
        capabilities: {
          model, task: 'llm', input_modalities: ['text'], output_modalities: ['text'], streaming: true,
          reasoning: { supported: false, efforts: [], summary: false, persisted_state: false },
          tools: { function_calling: true, parallel_calls: false, multimodal_results: false },
          structured_output: false, metadata: {}, extensions: {},
        },
      })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    const providerButton = await screen.findByTitle('模型与思考档位')
    await waitFor(() => expect(providerButton).toHaveTextContent('test-model'))
    fireEvent.click(providerButton)
    expect(screen.queryByRole('combobox', { name: '顶部模型思考强度' })).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '输入框思考档位' })).toBeDisabled()
    expect(await screen.findByText('模型未声明推理档位')).toBeInTheDocument()
  })

  it('Chat 协议保持固定五档且不请求 Kemo 能力接口', async () => {
    let capabilityRequests = 0
    server.use(
      http.get('/api/users/kesepain/overview', () => HttpResponse.json({
        user: 'kesepain', session_id: '',
        context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false }, limit: 120000, percent: 0, rounds: 0, round_limit: 30 },
        provider: { type: 'chat', base_url: 'https://chat.test/v1', model: 'chat-model', reasoning_effort: 'medium', timeout: 120, stream: true, credential_source: 'inline', configured: true },
        counts: { sessions: 0, knowledge_documents: 0, enabled_tools: 0, enabled_agents: 0, active_tasks: 0 },
        context_window: {
          tokens: { total_tokens: 0, capacity_tokens: 120000, percent: 0, source: 'unavailable' },
          conversation: { foreground_rounds: 0, archived_rounds: 0, session_total_rounds: 0, session_tool_calls: 0, total_tool_calls: 0 },
          tasks: { active_plans: 0, waiting_crons: 0 },
          capabilities: { tools_enabled: 0, tools_disabled: 0, agents_enabled: 0 },
          knowledge: { enabled: 0, disabled: 0 },
          messages: { connected: 0 },
          integrations: { expands: 0, senses: 0 },
        },
        runtime_host: { state: 'unmanaged', components: {} }, activities: [], active_plan: null,
      })),
      http.get('/api/users/kesepain/provider/model-capabilities', () => {
        capabilityRequests += 1
        return HttpResponse.json({})
      }),
    )

    renderApp('/chat?user=kesepain&session=s1')
    const providerButton = await screen.findByTitle('模型与思考档位')
    await waitFor(() => expect(providerButton).toHaveTextContent('chat-model'))
    expect(capabilityRequests).toBe(0)
    fireEvent.click(providerButton)
    fireEvent.click(screen.getByRole('combobox', { name: '顶部模型思考强度' }))
    expect(screen.getAllByRole('option')).toHaveLength(5)
    expect(screen.getByRole('option', { name: /最大.*最强推理/ })).toBeInTheDocument()
    expect(capabilityRequests).toBe(0)
  })

  it('上传文件随下一条消息发送并在本轮完成后清除提示', async () => {
    let chatBody: { uploaded_files?: string[] } | undefined
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatBody = JSON.parse(String(init?.body)) as { uploaded_files?: string[] }
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) { streamController = controller },
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const uploadButton = await screen.findByRole('button', { name: '上传文件' })
    fireEvent.click(uploadButton)
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()
    fireEvent.change(fileInput!, { target: { files: [new File(['attachment'], 'note.md', { type: 'text/markdown' })] } })
    expect(await screen.findByLabelText('待发送附件：note.md')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '请读取这个文件' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatBody?.uploaded_files).toEqual(['note.md']))
    await waitFor(() => expect(screen.queryByLabelText('待发送附件：note.md')).not.toBeInTheDocument())
    expect(screen.getByLabelText('附件：note.md')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('')
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument()

    streamController.enqueue(new TextEncoder().encode('event: done\ndata: {"type":"done"}\n\n'))
    streamController.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
  })

  it('非成功 done 仍立即清除本次提交的图片附件引用', async () => {
    let chatBody: { uploaded_files?: string[] } | undefined
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatBody = JSON.parse(String(init?.body)) as { uploaded_files?: string[] }
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) { streamController = controller },
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [new File(['image'], 'limited.png', { type: 'image/png' })] } })
    expect(await screen.findByLabelText('待发送附件：limited.png')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '处理这张图片' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(chatBody?.uploaded_files).toEqual(['limited.png']))
    await waitFor(() => expect(screen.queryByLabelText('待发送附件：limited.png')).not.toBeInTheDocument())
    expect(screen.getByLabelText('附件：limited.png')).toBeInTheDocument()

    streamController.enqueue(new TextEncoder().encode('event: done\ndata: {"type":"done","metadata":{"committed":true,"status":"limited"}}\n\n'))
    streamController.close()
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
    expect(screen.queryByLabelText('待发送附件：limited.png')).not.toBeInTheDocument()
  })

  it('历史图片附件显示缩略图和下载入口', async () => {
    server.use(http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: 's1',
      messages: [
        {
          role: 'user', content: '请识别这张图', attachments: [{
            asset_id: 'asset_photo', name: 'photo.png', media_kind: 'image',
            mime_type: 'image/png', size: 1024, checksum_sha256: 'a'.repeat(64),
            scope: 'file_upload', relative_path: 'photo.png', available: true,
          }],
        },
        { role: 'assistant', content: '图片识别完成' },
      ],
      round_metrics: [], round_traces: [],
    })))

    renderApp('/chat?user=kesepain&session=s1')

    const card = await screen.findByLabelText('附件：photo.png')
    expect(within(card).getByRole('img', { name: 'photo.png' })).toHaveAttribute('src', expect.stringContaining('/attachment-thumbnails/'))
    expect(within(card).getByRole('button', { name: '下载附件 photo.png' })).toBeInTheDocument()
  })

  it('源文件已清理时仍保留轻量图片缩略图', async () => {
    server.use(http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
      user: 'kesepain', source: 'web', session_id: 's1',
      messages: [
        {
          role: 'user', content: '', attachments: [{
            asset_id: 'asset_removed', name: 'removed.png', media_kind: 'image',
            mime_type: 'image/png', size: 2048, checksum_sha256: 'd'.repeat(64),
            scope: 'file_upload', relative_path: 'removed.png', available: false,
          }],
        },
        { role: 'assistant', content: '此前已处理该图片' },
      ],
      round_metrics: [], round_traces: [],
    })))

    renderApp('/chat?user=kesepain&session=s1')

    const card = await screen.findByLabelText('已清理附件：removed.png')
    expect(within(card).getByText(/源文件已清理/)).toBeInTheDocument()
    expect(within(card).getByRole('img', { name: 'removed.png' })).toHaveAttribute('src', expect.stringContaining('/attachment-thumbnails/'))
    expect(within(card).queryByRole('button', { name: /下载附件/ })).not.toBeInTheDocument()
    expect(screen.queryByText('[附件] removed.png')).not.toBeInTheDocument()
  })

  it('切换到其他页面后保留当前会话的未发送文本和附件', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '这段内容还没有发送' } })
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    expect(fileInput).not.toBeNull()
    fireEvent.change(fileInput!, { target: { files: [new File(['draft'], 'draft-note.md', { type: 'text/markdown' })] } })
    expect(await screen.findByLabelText('待发送附件：draft-note.md')).toBeInTheDocument()
    expect(screen.getByLabelText('待发送附件：draft-note.md')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    expect(await screen.findByRole('heading', { name: '配置' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /^对话$/ }))

    expect(await screen.findByRole('textbox', { name: '消息内容' })).toHaveValue('这段内容还没有发送')
    expect(screen.getByLabelText('待发送附件：draft-note.md')).toBeInTheDocument()
  })

  it('上传过程中切换页面时仍把完成结果写回原会话草稿', async () => {
    let releaseUpload!: () => void
    const uploadGate = new Promise<void>((resolve) => { releaseUpload = resolve })
    server.use(http.post('/api/users/kesepain/files/file_upload/upload', async ({ request }) => {
      await uploadGate
      return HttpResponse.json({
        user: 'kesepain',
        scope: 'file_upload',
        path: new URL(request.url).searchParams.get('path'),
        size: 4,
        updated: true,
      })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [new File(['late'], 'late.png', { type: 'image/png' })] } })
    expect(await screen.findByText('正在上传 1 个文件…')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    await screen.findByRole('heading', { name: '配置' })
    releaseUpload()
    fireEvent.click(screen.getByRole('link', { name: /^对话$/ }))

    expect(await screen.findByLabelText('待发送附件：late.png')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上传文件' })).toBeEnabled()
  })

  it('聊天请求失败时恢复本次文本但不恢复已提交附件', async () => {
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      return HttpResponse.json({ error: { message: 'Provider 暂时不可用' } }, { status: 502 })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [new File(['retry'], 'retry.txt', { type: 'text/plain' })] } })
    expect(await screen.findByLabelText('待发送附件：retry.txt')).toBeInTheDocument()
    fireEvent.change(composer, { target: { value: '失败后需要恢复' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    await waitFor(() => expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('失败后需要恢复'))
    expect(screen.queryByLabelText('待发送附件：retry.txt')).not.toBeInTheDocument()
    expect(screen.getByLabelText('附件：retry.txt')).toBeInTheDocument()
  })

  it('响应流缺少终态后重发不会把新正文和思考累加到旧尝试', async () => {
    let attempt = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      attempt += 1
      if (attempt === 1) {
        return new Response(
          'event: reasoning_delta\ndata: {"type":"reasoning_delta","content":"旧思考"}\n\n'
          + 'event: tool_call_start\ndata: {"type":"tool_call_start","tool_call_id":"shared-call","tool_name":"file","arguments":{}}\n\n'
          + 'event: text_delta\ndata: {"type":"text_delta","content":"旧正文"}\n\n',
          { headers: { 'Content-Type': 'text/event-stream' } },
        )
      }
      return new Response(
        'event: reasoning_delta\ndata: {"type":"reasoning_delta","content":"新思考"}\n\n'
        + 'event: tool_call_start\ndata: {"type":"tool_call_start","tool_call_id":"shared-call","tool_name":"shell","arguments":{}}\n\n'
        + 'event: tool_call_result\ndata: {"type":"tool_call_result","tool_call_id":"shared-call","tool_name":"shell","result":{"ok":true}}\n\n'
        + 'event: text_delta\ndata: {"type":"text_delta","content":"新正文"}\n\n'
        + 'event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))

    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '请重试这项任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect((await screen.findAllByText('响应流在终态事件到达前结束，未收到最终状态')).length).toBeGreaterThan(0)
    await waitFor(() => expect(composer).toHaveValue('请重试这项任务'))
    expect(screen.getByText('旧正文')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('新正文')).toBeInTheDocument()
    expect(screen.getByText('旧正文')).toBeInTheDocument()
    expect(screen.queryByText('旧正文新正文')).not.toBeInTheDocument()
    expect(screen.queryByText('旧思考新思考')).not.toBeInTheDocument()
    expect(attempt).toBe(2)
  })

  it('自动重试事件会保留失败尝试快照并隔离新尝试，不产生累加', async () => {
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      return new Response(
        'event: reasoning_delta\ndata: {"type":"reasoning_delta","content":"旧思考"}\n\n'
        + 'event: tool_call_start\ndata: {"type":"tool_call_start","tool_call_id":"old-call","tool_name":"old-tool","arguments":{"value":"before-retry"}}\n\n'
        + 'event: tool_call_result\ndata: {"type":"tool_call_result","tool_call_id":"old-call","tool_name":"old-tool","result":{"ok":true},"metadata":{"status":"completed"}}\n\n'
        + 'event: text_delta\ndata: {"type":"text_delta","content":"旧正文"}\n\n'
        + 'event: retrying\ndata: {"type":"retrying","content":"正在自动重试","metadata":{"failed_attempt":1,"next_attempt":2,"max_attempts":6}}\n\n'
        + 'event: reasoning_delta\ndata: {"type":"reasoning_delta","content":"新思考"}\n\n'
        + 'event: tool_call_start\ndata: {"type":"tool_call_start","tool_call_id":"new-call","tool_name":"new-tool","arguments":{"value":"after-retry"}}\n\n'
        + 'event: tool_call_result\ndata: {"type":"tool_call_result","tool_call_id":"new-call","tool_name":"new-tool","result":{"ok":true},"metadata":{"status":"completed"}}\n\n'
        + 'event: text_delta\ndata: {"type":"text_delta","content":"新正文"}\n\n'
        + 'event: done\ndata: {"type":"done","metadata":{"status":"completed","committed":true}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))

    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '自动重试' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('新正文')).toBeInTheDocument()
    expect(screen.getByText('旧正文')).toBeInTheDocument()
    expect(screen.getByText('旧思考')).toBeInTheDocument()
    expect(screen.getByText('old-tool')).toBeInTheDocument()
    expect(screen.getByText('new-tool')).toBeInTheDocument()
    expect(screen.queryByText('旧正文新正文')).not.toBeInTheDocument()
    expect(screen.queryByText(/运行出现问题，正在自动重试/)).not.toBeInTheDocument()
  })

  it('从剪贴板粘贴多个文件后允许不输入文字直接发送附件', async () => {
    let chatBody: { prompt?: string; uploaded_files?: string[] } | undefined
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatBody = JSON.parse(String(init?.body)) as { prompt?: string; uploaded_files?: string[] }
      return new Response('event: done\ndata: {"type":"done"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    const screenshot = new File(['png'], 'screenshot.png', { type: 'image/png' })
    const archive = new File(['zip'], 'bundle.zip', { type: 'application/zip' })
    fireEvent.paste(input, {
      clipboardData: { items: [], files: [screenshot, archive] },
    })

    expect(await screen.findByLabelText('待发送附件：screenshot.png')).toBeInTheDocument()
    expect(screen.getByLabelText('待发送附件：bundle.zip')).toBeInTheDocument()
    const imageCard = screen.getByLabelText('待发送附件：screenshot.png')
    expect(within(imageCard).getByRole('img', { name: 'screenshot.png' })).toBeInTheDocument()
    expect(within(imageCard).getByRole('link', { name: '预览图片 screenshot.png' })).toHaveAttribute('href', expect.stringContaining('/preview?path=screenshot.png'))
    const fileCard = screen.getByLabelText('待发送附件：bundle.zip')
    expect(within(fileCard).getByRole('img', { name: '文件缩略图' })).toBeInTheDocument()
    fireEvent.click(within(fileCard).getByRole('button', { name: '取消引用 bundle.zip' }))
    expect(screen.queryByLabelText('待发送附件：bundle.zip')).not.toBeInTheDocument()
    expect(screen.getByLabelText('待发送附件：screenshot.png')).toBeInTheDocument()
    const sendButton = screen.getByRole('button', { name: '发送' })
    expect(sendButton).toBeEnabled()
    fireEvent.click(sendButton)

    await waitFor(() => expect(chatBody).toEqual(expect.objectContaining({
      prompt: '',
      uploaded_files: ['screenshot.png'],
    })))
  })

  it('对话操作菜单提供保存新建、清空和压缩，不重复显示长任务与重新发送操作', async () => {
    let compressionCalled = false
    const historyMessages = [
      { role: 'user', content: '上一条问题' },
      { role: 'assistant', content: '上一条回答' },
    ]
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: historyMessages,
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/compress', () => {
        compressionCalled = true
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', requested: true,
          compressed: true, rounds_removed: 2, summary_cache_exists: true,
          context: { rounds_removed: 2 },
          memory: {
            status: 'queued', user: 'kesepain', source: 'web', session_id: 's1',
            round: 2, candidates: 0, processed_round: 0, target_round: 2,
            pending_rounds: 2, extraction: null,
            retry_pending: false,
          },
        })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('上一条回答')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    expect(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /清空此对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ })).toBeInTheDocument()
    expect(screen.getByText('优先按轮数压缩，不足时按 Token 压缩；记忆转入后台')).toBeInTheDocument()
    expect(screen.queryByRole('menuitemcheckbox', { name: /长任务模式/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('menuitem', { name: /重新发送一次消息/ })).not.toBeInTheDocument()
    expect(screen.getByText('再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ }))
    await waitFor(() => expect(compressionCalled).toBe(true))
    expect(await screen.findByText('上下文压缩完成，已整理 2 轮历史。记忆提取已转入后台，共有 2 轮待处理。')).toBeInTheDocument()
  })

  it.each([true, false])('最后一轮仍高于 Token 目标时说明压缩边界（已压缩：%s）', async (compressed) => {
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '问题' }, { role: 'assistant', content: '最后一轮正文' }],
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/compress', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1', requested: true,
        compressed, rounds_removed: compressed ? 2 : 0,
        context: { minimum_rounds_reached: true, target_reached: false },
        memory: { status: 'skipped', candidates: 0, reason: 'already_processed' },
      })),
    )
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('最后一轮正文')
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ }))
    const expected = compressed
      ? '上下文压缩完成，已整理 2 轮历史。已保留最后一轮，仍高于目标 Token 比例。记忆已是最新状态。'
      : '已到最后一轮，无法继续按完整轮次压缩；当前仍高于目标 Token 比例。记忆已是最新状态。'
    expect(await screen.findByText(expected)).toBeInTheDocument()
  })

  it('编辑重发只撤销最新一轮并把原问题放回输入框', async () => {
    let undoBody: Record<string, unknown> | null = null
    let historyMessages = [
      { role: 'user', content: '第一条问题' },
      { role: 'assistant', content: '第一条回答' },
      { role: 'user', content: '第二条问题' },
      { role: 'assistant', content: '第二条回答' },
    ]
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: historyMessages,
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/undo-last-round', async ({ request }) => {
        undoBody = await request.json() as Record<string, unknown>
        historyMessages = historyMessages.slice(0, 2)
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', found: true,
          rolled_back: true, round: 2, remaining_rounds: 1, prompt: '第二条问题',
          content: [{ type: 'text', text: '第一条问题' }, { type: 'text', text: '第一条回答' }],
        })
      }),
    )

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('第二条回答')
    const editButtons = screen.getAllByRole('button', { name: '编辑后重发' })
    expect(editButtons).toHaveLength(1)

    fireEvent.click(editButtons[0])
    await waitFor(() => expect(undoBody).toEqual({ expected_round: 2, prompt: '第二条问题' }))
    await waitFor(() => expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('第二条问题'))
    await waitFor(() => expect(screen.queryByText('第二条回答')).not.toBeInTheDocument())
    expect(screen.getByText('第一条回答')).toBeInTheDocument()
    expect(screen.getByText('最新一轮已撤销；修改内容后发送将创建新的最新一轮。')).toBeInTheDocument()
  })

  it('唯一第一轮编辑后保持对话布局并可在原会话重新发送', async () => {
    const welcomeText = /kemo-agent 已就绪/
    let undoBody: Record<string, unknown> | null = null
    let chatBody: Record<string, unknown> | null = null
    let historyMessages = [
      { role: 'user', content: '第一轮原问题' },
      { role: 'assistant', content: '第一轮原回答' },
    ]
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatBody = JSON.parse(String(init?.body || '{}')) as Record<string, unknown>
      historyMessages = [
        { role: 'user', content: '修改后的第一轮问题' },
        { role: 'assistant', content: '修改后的第一轮回答' },
      ]
      return new Response(
        'event: text_delta\ndata: {"type":"text_delta","content":"修改后的第一轮回答"}\n\n'
        + 'event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: historyMessages,
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/undo-last-round', async ({ request }) => {
        undoBody = await request.json() as Record<string, unknown>
        historyMessages = []
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', found: true,
          rolled_back: true, round: 1, remaining_rounds: 0, prompt: '第一轮原问题',
          content: [{ type: 'text', text: '第一轮原问题' }],
        })
      }),
    )

    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('第一轮原回答')
    fireEvent.click(screen.getByRole('button', { name: '编辑后重发' }))

    await waitFor(() => expect(undoBody).toEqual({ expected_round: 1, prompt: '第一轮原问题' }))
    const input = screen.getByRole('textbox', { name: '消息内容' })
    await waitFor(() => expect(input).toHaveValue('第一轮原问题'))
    await waitFor(() => expect(screen.queryByText('第一轮原回答')).not.toBeInTheDocument())
    expect(screen.queryByText(welcomeText)).not.toBeInTheDocument()
    expect(screen.getByText('最新一轮已撤销；修改内容后发送将创建新的最新一轮。')).toBeInTheDocument()

    fireEvent.change(input, { target: { value: '修改后的第一轮问题' } })
    const sendButton = screen.getByRole('button', { name: '发送' })
    expect(sendButton).toBeEnabled()
    fireEvent.click(sendButton)

    await waitFor(() => expect(chatBody).toMatchObject({
      session_id: 's1',
      prompt: '修改后的第一轮问题',
    }))
    expect(getSearch()).toContain('session=s1')
    expect(await screen.findByText('修改后的第一轮回答')).toBeInTheDocument()
    expect(screen.queryByText(welcomeText)).not.toBeInTheDocument()
  })

  it('消息跟进默认不引导，排序后按队列顺序自动发送下一轮', async () => {
    let firstController!: ReadableStreamDefaultController<Uint8Array>
    const prompts: string[] = []
    let guidanceRequests = 0
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    server.use(http.post('/api/runs/:runId/guidance', () => {
      guidanceRequests += 1
      return HttpResponse.json({ status: 'accepted_current_run' })
    }))
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      prompts.push(String(JSON.parse(String(init?.body || '{}')).prompt || ''))
      if (prompts.length === 1) return new Response(new ReadableStream<Uint8Array>({ start(controller) { firstController = controller } }), { headers: { 'Content-Type': 'text/event-stream' } })
      return new Response('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '当前任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(prompts).toEqual(['当前任务']))
    for (const text of ['跟进A', '跟进B']) {
      fireEvent.change(composer, { target: { value: text } })
      fireEvent.keyDown(composer, { key: 'Enter' })
    }
    fireEvent.change(composer, { target: { value: '跟进C' } })
    fireEvent.click(screen.getByRole('button', { name: '下一轮发送' }))
    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    expect(screen.getAllByRole('article', { name: /^消息跟进 \d+$/ })).toHaveLength(3)
    expect(guidanceRequests).toBe(0)
    expect(prompts).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '上移消息跟进 3' }))
    fireEvent.click(screen.getByRole('button', { name: '上移消息跟进 2' }))
    expect(screen.getByRole('article', { name: '消息跟进 1' })).toHaveTextContent('跟进C')
    firstController.enqueue(new TextEncoder().encode('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n'))
    firstController.close()
    await waitFor(() => expect(prompts).toEqual(['当前任务', '跟进C', '跟进A', '跟进B']))
    await waitFor(() => expect(screen.queryByLabelText('消息跟进队列')).not.toBeInTheDocument())
    expect(guidanceRequests).toBe(0)
  })

  it('运行中只在输入框上方展示最新引导并在结束后归档到 Token 统计下方', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    let markChatStarted!: () => void
    const chatStarted = new Promise<void>((resolve) => { markChatStarted = resolve })
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          streamController = controller
          markChatStarted()
        },
      })
      return new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '开始处理任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await chatStarted

    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '先检查目录' } })
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Enter', ctrlKey: true })
    const firstCurrent = await screen.findByText('正在引导')
    const firstCard = firstCurrent.closest('article')!
    const guidancePreview = firstCard.closest('.composer-guidance-preview')!
    const composer = screen.getByRole('textbox', { name: '消息内容' }).closest('section[aria-label="消息输入区域"]')!
    expect(guidancePreview).toBeInTheDocument()
    expect(firstCard.closest('.messages')).toBeNull()
    expect(guidancePreview.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.queryByRole('button', { name: '展开跟进消息' })).not.toBeInTheDocument()

    streamController.enqueue(encoder.encode('event: guidance_applied\ndata: {"type":"guidance_applied","metadata":{"guidance":["先检查目录"]}}\n\n'))
    expect(await screen.findByText('智能体已读取该引导并继续运行')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '结果放入临时区' } })
    fireEvent.click(screen.getByRole('button', { name: '消息跟进' }))
    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    fireEvent.click(screen.getByRole('button', { name: '本轮引导' }))
    await waitFor(() => expect(screen.getByText('结果放入临时区')).toBeInTheDocument())
    expect(screen.queryByText('先检查目录')).not.toBeInTheDocument()

    streamController.enqueue(encoder.encode('event: guidance_applied\ndata: {"type":"guidance_applied","metadata":{"guidance":["结果放入临时区"]}}\n\n'))
    streamController.enqueue(encoder.encode('event: text_delta\ndata: {"type":"text_delta","content":"任务完成"}\n\n'))
    streamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","usage":{"total_tokens":12},"metadata":{"guidance_count":2}}\n\n'))
    streamController.close()

    await screen.findByText('任务完成')
    await waitFor(() => expect(screen.queryByRole('button', { name: '停止生成' })).not.toBeInTheDocument())
    expect(screen.getAllByText('引导成功')).toHaveLength(2)
    expect(screen.getByText('先检查目录')).toBeInTheDocument()
    expect(screen.getByText('结果放入临时区')).toBeInTheDocument()
    const guidanceList = screen.getByText('先检查目录').closest('.assistant-guidance-list')!
    const completedCards = guidanceList.querySelectorAll('.guidance-message')
    expect(completedCards).toHaveLength(2)
    expect(completedCards[0]).toHaveTextContent('先检查目录')
    expect(completedCards[1]).toHaveTextContent('结果放入临时区')
    const footer = guidanceList.previousElementSibling!
    expect(footer).toHaveClass('assistant-turn-footer')
  })

  it('运行中支持只发送音频视频附件作为多模态引导', async () => {
    let streamController!: ReadableStreamDefaultController<Uint8Array>
    let guidanceBody: Record<string, unknown> | undefined
    let releaseGuidance!: () => void
    const guidanceGate = new Promise<void>((resolve) => { releaseGuidance = resolve })
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    server.use(http.post('/api/runs/:runId/guidance', async ({ params, request }) => {
      guidanceBody = await request.json() as Record<string, unknown>
      await guidanceGate
      return HttpResponse.json({
        run_id: params.runId,
        status: 'accepted_current_run',
        queued: 1,
        guidance_id: guidanceBody.guidance_id,
      })
    }))
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      return new Response(new ReadableStream<Uint8Array>({
        start(controller) { streamController = controller },
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '开始长任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await screen.findByRole('button', { name: '停止生成' })

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [
      new File(['audio'], 'voice.mp3', { type: 'audio/mpeg' }),
      new File(['video'], 'clip.mp4', { type: 'video/mp4' }),
    ] } })
    expect(await screen.findByLabelText('待发送附件：voice.mp3')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '消息跟进' })).toBeEnabled()
    fireEvent.keyDown(composer, { key: 'Enter', ctrlKey: true })

    await waitFor(() => expect(guidanceBody).toMatchObject({
      guidance: '',
      uploaded_files: ['voice.mp3', 'clip.mp4'],
    }))
    expect(screen.queryByLabelText('待发送附件：voice.mp3')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('待发送附件：clip.mp4')).not.toBeInTheDocument()
    releaseGuidance()
    await waitFor(() => expect(screen.getByText('voice.mp3')).toBeInTheDocument())
    expect(screen.getByText('clip.mp4')).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '音频缩略图' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: '视频缩略图' })).toBeInTheDocument()
    const guidanceId = String(guidanceBody?.guidance_id || '')
    streamController.enqueue(encoder.encode(`event: guidance_applied\ndata: ${JSON.stringify({
      type: 'guidance_applied',
      metadata: { guidance: ['附件引导：voice.mp3、clip.mp4'], guidance_details: [{ id: guidanceId }] },
    })}\n\n`))
    streamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":true,"guidance_count":1}}\n\n'))
    streamController.close()

    expect(await screen.findByText('引导成功')).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByLabelText('待发送附件：voice.mp3')).not.toBeInTheDocument())
  })

  it('任务计划终态卡片归档到执行轮次的 Token 统计与引导之间', async () => {
    const pendingPlan = {
      plan_id: 'plan_12345678', title: '终态归档验收', description: '验证计划卡片位置', status: 'pending', auto_accept: false,
      reminder: '', source: 'web', session_id: 's1', current_step: 'step_1', revision: 1, created_at: '', updated_at: '',
      progress: { completed: 0, total: 1, percent: 0 },
      steps: [{ step_id: 'step_1', title: '执行验收', description: '', status: 'pending', depends_on: [], critical: true, tool_name: '', started_at: '', finished_at: '' }],
    }
    const completedPlan = {
      ...pendingPlan,
      status: 'completed', revision: 4,
      progress: { completed: 1, total: 1, percent: 100 },
      steps: [{ ...pendingPlan.steps[0], status: 'completed', finished_at: '2026-07-26T12:00:00+08:00' }],
    }
    server.use(
      http.get('/api/users/kesepain/tasks', () => HttpResponse.json({
        user: 'kesepain', summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 0, completed_plans: 1 },
        plans: [completedPlan], cron_tasks: [], executions: [],
      })),
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [
          { role: 'user', content: '创建任务计划' },
          { role: 'assistant', content: '新计划已生成：完整步骤' },
          { role: 'user', content: '【任务计划连续执行】\n计划 ID：plan_12345678\n起始步骤：step_1' },
          { role: 'assistant', content: '任务计划执行完成。' },
        ],
        round_metrics: [
          { round: 1, usage: { prompt_tokens: 10, completion_tokens: 2 }, elapsed_ms: 10, tool_calls: 1, guidance: [] },
          { round: 2, usage: { prompt_tokens: 20, completion_tokens: 4 }, elapsed_ms: 20, tool_calls: 1, guidance: ['执行时补充说明'] },
        ],
        round_traces: [{
          round: 1, reasoning: '', tools: [{
            call_id: 'create-plan', name: 'subagent_dispatch', status: 'completed', elapsed_ms: 5,
            arguments_text: '{}', arguments_truncated: false,
            result_text: JSON.stringify({ ok: true, result: { plan: pendingPlan } }), result_truncated: false,
          }],
        }],
        pagination: { limit: 20, total_rounds: 2, first_round: 1, last_round: 2, has_more_before: false, next_before: null },
      })),
    )

    renderApp('/chat?user=kesepain&session=s1')
    const planCard = await screen.findByLabelText('已创建任务计划：终态归档验收')
    expect(screen.getAllByLabelText('已创建任务计划：终态归档验收')).toHaveLength(1)
    expect(screen.queryByLabelText('任务计划：终态归档验收')).not.toBeInTheDocument()
    const executionTurn = screen.getByText('任务计划执行完成。').closest('.msg.is-assistant')
    expect(executionTurn).not.toBeNull()
    expect(executionTurn).toContainElement(planCard)
    const usage = within(executionTurn as HTMLElement).getByRole('group', { name: '第 2 轮运行统计' })
    const guidance = within(executionTurn as HTMLElement).getByText('执行时补充说明').closest('.guidance-message')
    if (!guidance) throw new Error('expected archived guidance card')
    expect(usage.compareDocumentPosition(planCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(planCard.compareDocumentPosition(guidance) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('任务计划已创建，请在发送框上方查看并确认。')).toBeInTheDocument()
    expect(screen.queryByText('新计划已生成：完整步骤')).not.toBeInTheDocument()
  })

  it('本轮引导入口关闭后保留消息并自动作为下一轮发送', async () => {
    let firstStreamController!: ReadableStreamDefaultController<Uint8Array>
    let chatRequestCount = 0
    let secondPrompt = ''
    let secondUploadedFiles: string[] = []
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    server.use(
      http.post('/api/runs/:runId/guidance', ({ params }) => HttpResponse.json({
        run_id: params.runId,
        status: 'queued_next_turn',
        queued: 0,
      })),
    )
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequestCount += 1
      if (chatRequestCount === 1) {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) { firstStreamController = controller },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      const body = JSON.parse(String(init?.body || '{}')) as { prompt?: string; uploaded_files?: string[] }
      secondPrompt = String(body.prompt || '')
      secondUploadedFiles = body.uploaded_files || []
      return new Response('event: text_delta\ndata: {"type":"text_delta","content":"第二轮已收到"}\n\nevent: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '第一轮任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatRequestCount).toBe(1))

    const fileInput = document.querySelector<HTMLInputElement>('input[type="file"]')
    fireEvent.change(fileInput!, { target: { files: [new File(['audio'], 'queued.mp3', { type: 'audio/mpeg' })] } })
    expect(await screen.findByLabelText('待发送附件：queued.mp3')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '作为第二轮继续处理' } })
    fireEvent.click(screen.getByRole('button', { name: '消息跟进' }))
    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    fireEvent.click(screen.getByRole('button', { name: '本轮引导' }))
    fireEvent.click(await screen.findByRole('button', { name: '展开跟进消息' }))
    expect(await screen.findByText('已排队到下一轮')).toBeInTheDocument()
    expect(screen.getByText('作为第二轮继续处理')).toBeInTheDocument()

    firstStreamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n'))
    firstStreamController.close()

    await waitFor(() => expect(chatRequestCount).toBe(2))
    expect(secondPrompt).toBe('作为第二轮继续处理')
    expect(secondUploadedFiles).toEqual(['queued.mp3'])
    expect(await screen.findByText('第二轮已收到')).toBeInTheDocument()
    expect(screen.queryByText('已排队到下一轮')).not.toBeInTheDocument()
  })

  it('下一轮自动发送失败后允许取消待重发消息', async () => {
    let firstStreamController!: ReadableStreamDefaultController<Uint8Array>
    let chatRequestCount = 0
    const encoder = new TextEncoder()
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    server.use(
      http.post('/api/runs/:runId/guidance', ({ params }) => HttpResponse.json({
        run_id: params.runId,
        status: 'queued_next_turn',
        queued: 0,
      })),
    )
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatRequestCount += 1
      if (chatRequestCount === 1) {
        return new Response(new ReadableStream<Uint8Array>({
          start(controller) { firstStreamController = controller },
        }), { headers: { 'Content-Type': 'text/event-stream' } })
      }
      return HttpResponse.json({ error: { message: '网关连接已断开' } }, { status: 502 })
    }))

    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '第一轮任务' } })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    await waitFor(() => expect(chatRequestCount).toBe(1))

    fireEvent.change(composer, { target: { value: '你给修一下网关' } })
    fireEvent.click(screen.getByRole('button', { name: '消息跟进' }))
    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    fireEvent.click(screen.getByRole('button', { name: '本轮引导' }))
    firstStreamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n'))
    firstStreamController.close()

    fireEvent.click(await screen.findByRole('button', { name: '展开跟进消息' }))
    expect(await screen.findByText('自动发送失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '取消' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '重新发送' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))

    await waitFor(() => expect(screen.queryByText('自动发送失败')).not.toBeInTheDocument())
    expect(screen.getByText('你给修一下网关')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: '消息内容' })).toBeEnabled()
    expect(chatRequestCount).toBe(2)
  })

  it('保存并创建新对话时不在前台同步等待记忆提取', async () => {
    let extractedSession = ''
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [
          { id: 'u1', role: 'user', content: '需要记住的内容' },
          { id: 'a1', role: 'assistant', content: '已经记录' },
        ],
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/:sessionId/extract-memory', ({ params }) => {
        extractedSession = String(params.sessionId)
        return HttpResponse.json({
          status: 'completed', user: 'kesepain', source: 'web',
          session_id: params.sessionId, round: 1, candidates: 1,
          extraction: { status: 'completed', candidate_count: 1 },
        })
      }),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('已经记录')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ }))

    await waitFor(() => expect(getSearch()).toBe('?user=kesepain&session=conv_new_session'))
    expect(extractedSession).toBe('')
  })

  it('保存新建期间可立即切换侧栏且新会话只更新当前页面参数', async () => {
    let releaseClose!: () => void
    let markCloseStarted!: () => void
    const closeGate = new Promise<void>((resolve) => { releaseClose = resolve })
    const closeStarted = new Promise<void>((resolve) => { markCloseStarted = resolve })
    server.use(
      http.post('/api/users/kesepain/sessions/s1/close', async () => {
        markCloseStarted()
        await closeGate
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', closed: true,
          memory: { status: 'queued', reason: 'queued', rounds: 1, processed_round: 0 },
          session: { ...session('s1', '旧对话', 1), state: 'closed' },
        })
      }),
    )
    const { getSearch, getPathname } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ }))
    await closeStarted
    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))

    expect(await screen.findByRole('heading', { name: '配置' })).toBeInTheDocument()
    expect(getPathname()).toBe('/settings')
    expect(getSearch()).not.toContain('session=s1')

    releaseClose()
    await waitFor(
      () => expect(getSearch()).toContain('session=conv_new_session'),
      { timeout: 10_000 },
    )
    expect(getPathname()).toBe('/settings')
  }, 15_000)

  it('清空当前对话会删除归档并进入新对话', async () => {
    let deletedSession = ''
    const nativeConfirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
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

    const dialog = screen.getByRole('alertdialog', { name: '清空此对话？' })
    expect(deletedSession).toBe('')
    expect(nativeConfirm).not.toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: '确认清空' }))
    await waitFor(() => expect(deletedSession).toBe('s1'))
    await waitFor(() => expect(getSearch()).toBe('?user=kesepain&session=conv_new_session'))
  })

  it('清空确认可以取消或 Escape 关闭，不发删除请求且保留草稿', async () => {
    let deletes = 0
    server.use(http.delete('/api/users/kesepain/sessions/s1', () => { deletes += 1; return HttpResponse.json({ deleted: true }) }))
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    const input = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(input, { target: { value: '保留草稿' } })
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '取消' }))
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(deletes).toBe(0)
    expect(getSearch()).toContain('session=s1')
    expect(input).toHaveValue('保留草稿')
  })

  it('清空执行期间连点只删除一次，禁止取消', async () => {
    let deletes = 0
    server.use(http.delete('/api/users/kesepain/sessions/s1', async () => {
      deletes += 1
      await delay(150)
      return HttpResponse.json({ deleted: true })
    }))
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    await waitFor(() => expect(screen.getByTitle('模型与思考档位')).toHaveTextContent('test-model'))
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))
    const dialog = screen.getByRole('alertdialog')
    const confirm = within(dialog).getByRole('button', { name: '确认清空' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    expect(within(dialog).getByRole('button', { name: '取消' })).toBeDisabled()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(dialog).toBeInTheDocument()
    await waitFor(() => expect(getSearch()).toContain('session=conv_new_session'))
    expect(deletes).toBe(1)
  })

  it('删除成功后新建失败可见，重试不会重复删除原会话', async () => {
    let deletes = 0
    let creates = 0
    server.use(
      http.delete('/api/users/kesepain/sessions/s1', () => { deletes += 1; return HttpResponse.json({ deleted: true }) }),
      http.get('/api/users/kesepain/sessions/active', () => HttpResponse.json({ user: 'kesepain', session: null })),
      http.post('/api/users/kesepain/sessions', () => {
        creates += 1
        return creates === 1 ? HttpResponse.json({ error: { message: '隔离新建失败' } }, { status: 500 })
          : HttpResponse.json({ user: 'kesepain', session: { session_id: 'retry_clear_new', rounds: 0 }, created: true })
      }),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    await waitFor(() => expect(screen.getByTitle('模型与思考档位')).toHaveTextContent('test-model'))
    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认清空' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('新对话创建失败')
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '重试创建新对话' }))
    await waitFor(() => expect(getSearch()).toContain('session=retry_clear_new'))
    expect(deletes).toBe(1)
    expect(creates).toBe(2)
  })

  it('其他页面仍在使用对话时清空返回 409 并恢复原会话', async () => {
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '共享问题' }, { role: 'assistant', content: '共享回答' }],
        round_metrics: [], round_traces: [],
      })),
      http.delete('/api/users/kesepain/sessions/s1', () => HttpResponse.json({
        error: { code: 'conflict', message: '该对话正在其他页面中使用，暂时不能删除' },
      }, { status: 409 })),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('共享回答')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    fireEvent.click(screen.getByRole('menuitem', { name: /清空此对话/ }))

    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: '确认清空' }))
    expect(await screen.findByText('该对话正在其他页面中使用，暂时不能删除')).toBeInTheDocument()
    await waitFor(() => expect(getSearch()).toContain('session=s1'))
  })

  it('上下文窗口抽屉展示七组真实聚合统计', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByTitle('查看上下文与运行状态'))

    expect(screen.getByText('Token 占用概览')).toBeInTheDocument()
    expect(screen.getByText('数据注入策略')).toBeInTheDocument()
    expect(screen.getByLabelText('感知数据注入情况：按轮注入')).toBeInTheDocument()
    expect(screen.getByLabelText('拓展数据注入情况：按轮注入')).toBeInTheDocument()
    expect(screen.getByText('对话统计')).toBeInTheDocument()
    expect(screen.getByText('任务与定时')).toBeInTheDocument()
    expect(screen.getByText('工具与子智能体')).toBeInTheDocument()
    expect(screen.getByText('知识库状态')).toBeInTheDocument()
    expect(screen.getAllByText('外部消息').length).toBeGreaterThan(0)
    expect(screen.getByText('拓展与感知')).toBeInTheDocument()
    const cacheHitMetric = screen.getByText('Token 总命中率').closest('.context-metric')
    expect(cacheHitMetric).toHaveTextContent('80%')
    expect(cacheHitMetric).toHaveTextContent('当前对话空间累计')
    expect(screen.getAllByText('18.67%').length).toBeGreaterThan(0)
    expect(screen.queryByText('知识图谱')).not.toBeInTheDocument()
    expect(document.querySelector('.context-drawer-body')).toHaveClass('drawer-body')
  })

  it('不以用户每日统计替代当前会话 Token 命中率，并移除顶栏命令面板重复入口', async () => {
    server.use(http.get('/api/users/kesepain/overview', ({ request }) => {
      const selectedSession = new URL(request.url).searchParams.get('session_id') || ''
      return HttpResponse.json({
        user: 'kesepain', session_id: selectedSession,
        context: { usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0, estimated: false, cache_hit_rate: null },
          limit: 120000, percent: 0, rounds: 0, session_total_rounds: 0, archived_rounds: 0, round_limit: 30 },
        provider: { type: 'kemo', model: 'test-model', configured: true },
        counts: { sessions: 1, knowledge_documents: 0, enabled_tools: 0, enabled_agents: 0, active_tasks: 0 },
        context_window: {
          tokens: { total_tokens: 0, capacity_tokens: 120000, percent: 0, source: 'unavailable', measurement: 'unknown' },
          conversation: { foreground_rounds: 0, archived_rounds: 0, total_tool_calls: 0, session_total_rounds: 0, session_tool_calls: 0 },
          tasks: { active_plans: 0, waiting_crons: 0 }, capabilities: { tools_enabled: 0, tools_disabled: 0, agents_enabled: 0 },
          knowledge: { enabled: 0, disabled: 0 }, messages: { connected: 0 }, integrations: { expands: 0, senses: 0 },
          injection_policy: { expand: 'round', perception: 'round' },
        },
        context_snapshot: { available: false, source: 'unavailable', measurement: 'unknown', captured_at: '',
          system_prompt_tokens: 0, tool_schema_tokens: 0, conversation_tokens: 0, summary_tokens: 0, other_tokens: 0,
          total_tokens: 0, capacity_tokens: 120000, percent: 0, foreground_rounds: 0 },
        session_context_stats: { selected: true, foreground_rounds: 0, background_archived_rounds: 0, session_total_rounds: 0, session_tool_calls: 0 },
        agents: [], summary_cache: { exists: false, covered_rounds: [], created_at: '', window: '' },
        runtime_host: { state: 'unmanaged', components: {} }, active_plan: null, activities: [],
      })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByRole('textbox', { name: '消息内容' })
    expect(screen.queryByRole('button', { name: '命令面板' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '打开快捷指令' })).toBeInTheDocument()
    fireEvent.click(await screen.findByTitle('查看上下文与运行状态'))
    const cacheHitMetric = screen.getByText('Token 总命中率').closest('.context-metric')
    expect(cacheHitMetric).toHaveTextContent('—')
    expect(cacheHitMetric).not.toHaveTextContent('45.2%')
  })

  it('字号切换会同步更新文字与顶部布局比例', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))

    fireEvent.click(screen.getByTitle('界面比例'))
    expect(screen.getByText('界面字号')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('menuitem', { name: /^大/ }))
    await waitFor(() => expect(document.documentElement.dataset.fontSize).toBe('large'))

    fireEvent.click(screen.getByTitle('界面比例'))
    fireEvent.click(screen.getByRole('menuitem', { name: /^中/ }))
    await waitFor(() => expect(document.documentElement.dataset.fontSize).toBe('medium'))
  })

  it('快捷指令面板按分组展示卡片并提供斜杠指令参考', async () => {
    renderApp('/chat')
    await waitFor(() => expect(screen.getAllByText('kesepain').length).toBeGreaterThan(0))
    fireEvent.keyDown(document, { key: 'k', ctrlKey: true })
    const commandDialog = screen.getByRole('dialog', { name: '全局搜索与命令' })
    expect(within(commandDialog).getByRole('heading', { name: '对话操作' })).toBeInTheDocument()
    expect(within(commandDialog).getByRole('heading', { name: '查看指令' })).toBeInTheDocument()
    expect(within(commandDialog).getByRole('heading', { name: '侧边栏功能' })).toBeInTheDocument()
    expect(within(commandDialog).getByText('打开记忆管理')).toBeInTheDocument()
    expect(within(commandDialog).getByText('编辑用户资料')).toBeInTheDocument()
    expect(commandDialog.querySelector('kbd')).not.toBeInTheDocument()
    expect(within(commandDialog).queryByText(/Ctrl K 打开/)).not.toBeInTheDocument()

    fireEvent.click(within(commandDialog).getByRole('button', { name: /查看斜杠指令/ }))
    expect(within(commandDialog).getByText('/compress')).toBeInTheDocument()
    expect(within(commandDialog).getByText('/remember <内容>')).toBeInTheDocument()
    expect(within(commandDialog).getByRole('button', { name: '返回快捷指令' })).toBeInTheDocument()
  })

  it('快捷指令中的对话操作复用对话栏原有处理逻辑', async () => {
    let compressionCalled = false
    server.use(
      http.get('/api/users/kesepain/sessions/s1/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's1',
        messages: [{ role: 'user', content: '需要压缩的问题' }, { role: 'assistant', content: '需要压缩的回答' }],
        round_metrics: [], round_traces: [],
      })),
      http.post('/api/users/kesepain/sessions/s1/compress', () => {
        compressionCalled = true
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', requested: true,
          compressed: false, rounds_removed: 0, summary_cache_exists: true,
          context: { rounds_removed: 0 },
          memory: { status: 'skipped', user: 'kesepain', source: 'web', session_id: 's1', round: 1, candidates: 0, reason: 'already_processed', extraction: { status: 'skipped', candidate_count: 0 }, retry_pending: false },
        })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('需要压缩的回答')
    fireEvent.click(screen.getByRole('button', { name: '打开快捷指令' }))
    const commandDialog = screen.getByRole('dialog', { name: '全局搜索与命令' })
    fireEvent.click(within(commandDialog).getByRole('button', { name: /手动进行一次上下文压缩/ }))

    await waitFor(() => expect(compressionCalled).toBe(true))
    expect(screen.queryByRole('dialog', { name: '全局搜索与命令' })).not.toBeInTheDocument()
  })

  it('全部删除跨渠道历史携带当前页面标识，失败保留绑定，成功清理只读缓存', async () => {
    let deleted = false
    const requests: URL[] = []
    const mixed = [
      { ...session('s1', '当前工作', 3), source: 'web' },
      { ...session('shared', '桌宠只读历史', 2), source: 'message:nya_desk_pet' },
    ]
    server.use(
      http.get('/api/users/kesepain/sessions', ({ request }) => {
        const source = new URL(request.url).searchParams.get('source')
        return HttpResponse.json({
          user: 'kesepain', source: source || 'web',
          sessions: deleted ? [] : source === 'all' ? mixed : mixed.slice(0, 1),
        })
      }),
      http.delete('/api/users/kesepain/sessions', ({ request }) => {
        requests.push(new URL(request.url))
        if (requests.length === 1) return HttpResponse.json({
          error: { code: 'conflict', message: '其他设备正在使用对话' },
        }, { status: 409 })
        deleted = true
        return HttpResponse.json({
          user: 'kesepain', source: 'all', deleted: true, deleted_sessions: 29, deleted_windows: 31,
        })
      }),
    )
    const app = renderApp('/chat?user=kesepain&session=s1')
    app.client.setQueryData(['readonly-history', 'kesepain', 'message:nya_desk_pet', 'shared'], { stale: true })
    app.client.setQueryData(['readonly-history', 'reviewer', 'cli', 'other'], { keep: true })
    fireEvent.click(await screen.findByTitle('搜索历史对话'))
    const drawer = await screen.findByRole('dialog', { name: '历史对话' })
    expect(await within(drawer).findByText('桌宠只读历史')).toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '全部删除' }))
    fireEvent.click(screen.getByRole('button', { name: '确认全部删除' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('其他设备正在使用对话')
    expect(app.getSearch()).toContain('session=s1')
    expect(app.client.getQueryData(['readonly-history', 'kesepain', 'message:nya_desk_pet', 'shared'])).toEqual({ stale: true })
    fireEvent.click(screen.getByRole('button', { name: '确认全部删除' }))
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument())
    await waitFor(() => expect(app.getSearch()).not.toContain('session=s1'))
    expect(requests).toHaveLength(2)
    expect(requests[0].searchParams.get('source')).toBe('all')
    expect(requests[0].searchParams.get('client_id')).toBeTruthy()
    expect(requests[1].searchParams.get('client_id')).toBe(requests[0].searchParams.get('client_id'))
    expect(app.client.getQueryData(['readonly-history', 'kesepain', 'message:nya_desk_pet', 'shared'])).toBeUndefined()
    expect(app.client.getQueryData(['readonly-history', 'reviewer', 'cli', 'other'])).toEqual({ keep: true })
  })

  it('顶部历史搜索保存当前对话后跳转到指定历史会话', async () => {
    let extractedSession = ''
    let closedSession = ''
    let reopenedSession = ''
    const sessions = [
      { ...session('s1', '当前工作', 3), state: 'open' },
      { ...session('s2', '项目复盘', 8), state: 'closed' },
    ]
    server.use(
      http.get('/api/users/kesepain/sessions', () => HttpResponse.json({ user: 'kesepain', source: 'web', sessions })),
      http.post('/api/users/kesepain/sessions/:sessionId/extract-memory', ({ params }) => {
        extractedSession = String(params.sessionId)
        return HttpResponse.json({ status: 'completed', user: 'kesepain', source: 'web', session_id: params.sessionId, round: 3, candidates: 0, extraction: { status: 'completed', candidate_count: 0 } })
      }),
      http.post('/api/users/kesepain/sessions/:sessionId/close', ({ params }) => {
        closedSession = String(params.sessionId)
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: params.sessionId, closed: true, session: { ...sessions[0], state: 'closed' } })
      }),
      http.post('/api/users/kesepain/sessions/:sessionId/reopen', ({ params }) => {
        reopenedSession = String(params.sessionId)
        return HttpResponse.json({ user: 'kesepain', source: 'web', session_id: params.sessionId, reopened: true, session: { ...sessions[1], state: 'open' } })
      }),
      http.get('/api/users/kesepain/sessions/s2/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 's2', messages: [], round_metrics: [], round_traces: [],
      })),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByTitle('搜索历史对话'))

    const historyDrawer = await screen.findByRole('dialog', { name: '历史对话' })
    fireEvent.change(within(historyDrawer).getByRole('textbox', { name: '搜索历史对话名称' }), { target: { value: '项目' } })
    fireEvent.click(await within(historyDrawer).findByRole('button', { name: '打开对话 项目复盘' }))
    const switchDialog = screen.getByRole('alertdialog', { name: '确认切换历史对话？' })
    expect(switchDialog.parentElement?.parentElement).toBe(document.body)
    fireEvent.click(within(switchDialog).getByRole('button', { name: '确认切换' }))

    await waitFor(() => expect(closedSession).toBe('s1'))
    await waitFor(() => expect(reopenedSession).toBe('s2'))
    await waitFor(() => expect(getSearch()).toContain('session=s2'))
    expect(extractedSession).toBe('')
    expect(screen.queryByRole('dialog', { name: '历史对话' })).not.toBeInTheDocument()
  })

  it('历史抽屉选择日期后使用独立日期查询加载归档', async () => {
    // Calendar defaults to the Shanghai current month. Pin only Date so this
    // contract remains deterministic without replacing the async timer APIs
    // used by React Query and Testing Library.
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-09-25T04:00:00+00:00'))
    const requestedDates: string[] = []
    let reopenedSession = ''
    server.use(
      http.get('/api/users/kesepain/sessions', ({ request }) => {
        const selectedDate = new URL(request.url).searchParams.get('date') || ''
        requestedDates.push(selectedDate)
        return HttpResponse.json({
          user: 'kesepain',
          source: 'all',
          date: selectedDate,
          sessions: selectedDate === '2026-09-24'
            ? [{ ...session('dated-session', '日期归档', 2), state: 'closed', updated_at: '2026-09-24T08:00:00+00:00' }]
            : [{ ...session('s1', '当前工作', 3), state: 'open' }],
          has_more: false,
          next_cursor: '',
        })
      }),
      http.post('/api/users/kesepain/sessions/:sessionId/reopen', ({ params }) => {
        reopenedSession = String(params.sessionId)
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: params.sessionId, reopened: true,
          session: { ...session('dated-session', '日期归档', 2), state: 'open' },
        })
      }),
      http.get('/api/users/kesepain/sessions/dated-session/history', () => HttpResponse.json({
        user: 'kesepain', source: 'web', session_id: 'dated-session', messages: [], round_metrics: [], round_traces: [],
      })),
    )
    const { getSearch } = renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByTitle('搜索历史对话'))

    const historyDrawer = await screen.findByRole('dialog', { name: '历史对话' })
    fireEvent.click(within(historyDrawer).getByRole('button', { name: '按日期筛选历史归档' }))
    const calendar = screen.getByRole('dialog', { name: '选择历史归档日期' })
    fireEvent.click(within(calendar).getByRole('button', { name: '2026年9月24日' }))

    expect(await within(historyDrawer).findByText('日期归档')).toBeInTheDocument()
    expect(requestedDates).toContain('2026-09-24')
    expect(within(historyDrawer).queryByText('当前工作')).not.toBeInTheDocument()
    fireEvent.click(within(historyDrawer).getByRole('button', { name: '打开对话 日期归档' }))
    const switchDialog = screen.getByRole('alertdialog', { name: '确认切换历史对话？' })
    fireEvent.click(within(switchDialog).getByRole('button', { name: '确认切换' }))

    await waitFor(() => expect(reopenedSession).toBe('dated-session'))
    await waitFor(() => expect(getSearch()).toContain('session=dated-session'))
  })

  it('输入框知识库按钮按层级展示卡片并把引用写入草稿', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByRole('button', { name: '打开知识库' }))

    const drawer = await screen.findByRole('dialog', { name: '知识库引用' })
    fireEvent.click(await within(drawer).findByRole('button', { name: '用户' }))
    expect(await within(drawer).findByText('个人笔记')).toBeInTheDocument()
    expect(within(drawer).queryByText('共享笔记')).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 个人笔记' }))

    await waitFor(() => expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('[知识库引用 user:notes.md] 个人笔记'))
    expect(screen.queryByRole('dialog', { name: '知识库引用' })).not.toBeInTheDocument()
  })

  it('聊天知识引用可叠加分类和搜索，引用仍写入原始路径标记', async () => {
    const documents = [
      { scope: 'user', relative_path: 'notes.md', title: '个人笔记', category: '开发规范', size: 120, updated_at: 1, active_for_main_agent: true },
      { scope: 'shared', relative_path: 'team.md', title: '共享规范', category: '开发规范', size: 90, updated_at: 1, active_for_main_agent: true },
      { scope: 'shared', relative_path: 'other.md', title: '其他资料', category: '其他分类', size: 90, updated_at: 1, active_for_main_agent: true },
    ]
    server.use(http.get('/api/users/kesepain/knowledge', () => HttpResponse.json({
      user: 'kesepain', documents, summary: { documents: 3, user_documents: 1, shared_documents: 2, global_documents: 0 },
      categories: {
        user: [{ name: '开发规范', count: 1 }],
        shared: [{ name: '开发规范', count: 1 }, { name: '其他分类', count: 1 }],
      },
    })))
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '请参考规范' } })
    fireEvent.click(screen.getByRole('button', { name: '打开知识库' }))
    const drawer = await screen.findByRole('dialog', { name: '知识库引用' })
    const select = within(drawer).getByRole('combobox', { name: '知识库分类' })
    const option = await within(select).findByRole('option', { name: '共享层 · 开发规范（1）' })
    fireEvent.change(select, { target: { value: option.getAttribute('value') } })
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索知识库' }), { target: { value: 'team.md' } })
    expect(within(drawer).getByText('匹配 1 / 共 3 个知识文件')).toBeInTheDocument()
    expect(within(drawer).queryByText('个人笔记')).not.toBeInTheDocument()
    expect(within(drawer).queryByText('其他资料')).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 共享规范' }))
    await waitFor(() => expect(composer).toHaveValue('请参考规范\n[知识库引用 shared:team.md] 共享规范'))
    expect(screen.queryByRole('dialog', { name: '知识库引用' })).not.toBeInTheDocument()
  })

  it('输入框能力按钮按类型引用拓展、技能和插件并追加稳定标记', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '先检查当前状态' } })
    fireEvent.click(screen.getByRole('button', { name: '打开能力引用' }))

    let drawer = await screen.findByRole('dialog', { name: '能力引用' })
    fireEvent.click(await within(drawer).findByRole('button', { name: '全局' }))
    expect(await within(drawer).findByText('智能灯光控制')).toBeInTheDocument()
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索拓展' }), { target: { value: '客厅' } })
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 智能灯光控制' }))

    await waitFor(() => expect(composer).toHaveValue('先检查当前状态\n[拓展引用 global:example] 智能灯光控制'))
    expect(screen.queryByRole('dialog', { name: '能力引用' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '打开能力引用' }))
    drawer = await screen.findByRole('dialog', { name: '能力引用' })
    fireEvent.click(within(drawer).getByRole('tab', { name: '技能' }))
    fireEvent.click(within(drawer).getByRole('button', { name: '用户' }))
    fireEvent.click(await within(drawer).findByRole('button', { name: '引用 用户自建技能' }))
    await waitFor(() => expect(composer).toHaveValue('先检查当前状态\n[拓展引用 global:example] 智能灯光控制\n[技能引用 user:user_create/manual] 用户自建技能'))

    fireEvent.click(screen.getByRole('button', { name: '打开能力引用' }))
    drawer = await screen.findByRole('dialog', { name: '能力引用' })
    fireEvent.click(within(drawer).getByRole('tab', { name: '插件' }))
    expect(within(drawer).getByRole('navigation', { name: '插件层级' })).toHaveTextContent('全局')
    fireEvent.click(await within(drawer).findByRole('button', { name: '引用 clock' }))
    await waitFor(() => expect(composer).toHaveValue('先检查当前状态\n[拓展引用 global:example] 智能灯光控制\n[技能引用 user:user_create/manual] 用户自建技能\n[插件引用 global:clock] clock'))
  })

  it('样例侧栏提供最近对话，完整历史仍由顶栏抽屉查看', async () => {
    server.use(http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
      user: 'kesepain', source: 'web', sessions: [session('s1', '历史 1', 1)],
    })))
    renderApp('/chat?user=kesepain')

    await screen.findByTitle('搜索历史对话')
    expect(screen.getByText('最近对话')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /全部删除/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '历史 1' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收缩侧边栏' }))
    const expandButton = screen.getByRole('button', { name: '展开侧边栏' })
    expect(document.querySelector('.brand-mark img')).toHaveAttribute('src', '/api/logo')
    expect(document.querySelector('.brand-mark img')).toHaveAttribute('alt', 'kemo-agent Logo')
    expect(document.querySelector('a[aria-label="身份与人格"]')).toHaveAttribute('title', '身份与人格')
    fireEvent.click(expandButton)
    expect(screen.getByRole('button', { name: '收缩侧边栏' })).toBeInTheDocument()
  })

  it('运行限制展示并保存多层并发与反压配置', async () => {
    const captured: { globalChanges?: Record<string, unknown> } = {}
    server.use(
      http.patch('/api/global-config', async ({ request }) => {
        const payload = await request.json() as { changes: Record<string, unknown> }
        captured.globalChanges = payload.changes
        return HttpResponse.json({ scope: 'global', config: payload.changes, redacted_paths: [], updated: true })
      }),
    )
    renderApp('/settings?user=kesepain&tab=runtime')

    expect(await screen.findByText('Provider 并发控制')).toBeInTheDocument()
    expect(screen.getByRole('spinbutton', { name: '最大并发请求数' })).toHaveValue(10)
    expect(screen.getByRole('spinbutton', { name: '单用户最大并发聊天' })).toHaveValue(3)
    expect(screen.getByRole('spinbutton', { name: '消息路由队列上限' })).toHaveValue(20)
    expect(screen.getByRole('spinbutton', { name: '子代理队列上限' })).toHaveValue(50)
    expect(screen.getByRole('spinbutton', { name: '单轮最大工具调用数' })).toHaveValue(80)
    expect(screen.getByRole('spinbutton', { name: '单个工具最大连续使用上限' })).toHaveValue(8)
    expect(screen.getByRole('spinbutton', { name: '工具参数异常重试次数' })).toHaveValue(2)
    expect(screen.getByRole('spinbutton', { name: '定时任务历史保留天数' })).toHaveValue(7)
    expect(screen.getByRole('switch', { name: 'Cron 自动退避' })).toBeChecked()
    expect(screen.getByRole('slider', { name: '退避触发阈值' })).toHaveValue('0.2')

    fireEvent.change(screen.getByRole('spinbutton', { name: '最大并发请求数' }), { target: { value: '12' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Web 排队槽位上限' }), { target: { value: '7' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '单个工具最大连续使用上限' }), { target: { value: '9' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '工具参数异常重试次数' }), { target: { value: '3' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '定时任务历史保留天数' }), { target: { value: '14' } })
    fireEvent.click(screen.getByRole('button', { name: '保存运行限制' }))
    await waitFor(() => expect(captured.globalChanges).toBeDefined())

    const providerRuntime = captured.globalChanges?.provider_runtime as Record<string, unknown>
    const web = captured.globalChanges?.web as Record<string, unknown>
    const tools = captured.globalChanges?.tools as Record<string, unknown>
    expect(providerRuntime.max_concurrent_requests).toBe(12)
    expect(web.max_pending_chats).toBe(7)
    expect(tools.consecutive_identical_call_limit).toBe(9)
    expect(tools.invalid_tool_arguments_retries).toBe(3)
    const cron = captured.globalChanges?.cron as Record<string, unknown>
    expect(cron.history_retention_days).toBe(14)
  })

  it('拓展与感知支持不注入、按轮注入和实时注入三种用户策略', async () => {
    let savedChanges: Record<string, unknown> | undefined
    server.use(
      http.patch('/api/users/kesepain/config', async ({ request }) => {
        const payload = await request.json() as { changes: Record<string, unknown> }
        savedChanges = payload.changes
        return HttpResponse.json({ user: 'kesepain', config: payload.changes, redacted_paths: [], updated: true })
      }),
    )
    renderApp('/settings?user=kesepain&tab=permissions')

    const expandMaster = await screen.findByRole('switch', { name: '拓展数据注入' })
    const expandRealtime = screen.getByRole('switch', { name: '拓展数据实时注入' })
    const perceptionMaster = screen.getByRole('switch', { name: '感知数据注入' })
    const perceptionRealtime = screen.getByRole('switch', { name: '感知数据实时注入' })
    expect(expandMaster).toBeChecked()
    expect(perceptionMaster).toBeChecked()
    expect(expandRealtime).not.toBeChecked()
    expect(perceptionRealtime).not.toBeChecked()
    fireEvent.click(expandRealtime)
    fireEvent.click(perceptionMaster)
    expect(perceptionRealtime).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '保存权限边界' }))
    await waitFor(() => expect(savedChanges).toBeDefined())

    const expand = savedChanges?.expand as Record<string, unknown>
    expect(expand.prompt_injection).toBe(true)
    expect(expand.realtime_injection).toBe(true)
    expect(expand.global_whitelist).toEqual([])
    expect(expand.shared_whitelist).toEqual([])
    const perception = savedChanges?.perception as Record<string, unknown>
    expect(perception.prompt_injection).toBe(false)
    expect(perception.realtime_injection).toBe(false)
    expect(perception.global_whitelist).toEqual([])
  })
})
