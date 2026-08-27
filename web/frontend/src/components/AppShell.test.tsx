import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { delay, http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppShell, injectionPolicyPresentation, persistLastActiveUser, readLastActiveUser, resolveCurrentUser } from './AppShell'
import { ChatPage } from '../pages/ChatPage'
import { SettingsPage } from '../pages/SettingsPage'
import { server } from '../test/server'
import { useChatDraftStore } from '../store/chatDrafts'
import type { SessionSummary } from '../types/api'

afterEach(() => {
  useChatDraftStore.getState().clearAll()
  localStorage.removeItem('kemo-last-active-user')
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('AppShell user persistence', () => {
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
})

function renderApp(path = '/chat') {
  let currentSearch = new URL(path, 'http://test').search
  let currentPathname = new URL(path, 'http://test').pathname
  function LocationProbe() {
    const location = useLocation()
    currentSearch = location.search
    currentPathname = location.pathname
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
  return { getSearch: () => currentSearch, getPathname: () => currentPathname, client }
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
    await waitFor(() => expect(localStorage.getItem('kemo-last-active-user')).toBe('kesepain'))
    expect(screen.getByText(/当前用户的配置、历史、知识/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '切换当前用户' })).toBeInTheDocument()
    expect(screen.getByText('核心工作区')).toBeInTheDocument()
    expect(screen.queryByText('运行能力')).not.toBeInTheDocument()
    expect(screen.getByText('资源与系统')).toBeInTheDocument()
  })

  it('欢迎页快捷卡展示感知、拓展、运行状态与定时任务入口', async () => {
    renderApp('/chat?user=kesepain')

    expect(await screen.findByRole('button', { name: /查询感知情况/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查询拓展情况/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /查询运行状态/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /创建定时任务/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /查询拓展情况/ }))
    expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('查询 kemo-agent 当前拓展情况')
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
    const avatar = document.querySelector<HTMLImageElement>('.message.user .user-message-avatar img')
    expect(avatar?.getAttribute('src')).toBe('/api/users/kesepain/avatar?v=0')
    fireEvent.error(avatar!)
    expect(document.querySelector('.message.user .user-message-avatar img')).not.toBeInTheDocument()
    expect(document.querySelector('.message.user .user-message-avatar svg')).toBeInTheDocument()
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

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    fireEvent.click(await screen.findByRole('button', { name: '用户切换 ›' }))
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
    fireEvent.click(await screen.findByRole('button', { name: '用户切换 ›' }))
    expect(await screen.findByRole('button', { name: '切换到用户 reviewer' })).toBeEnabled()
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
    expect(screen.getByTitle('搜索历史对话')).toBeInTheDocument()
    expect(await screen.findByTitle('查看当前 Provider')).toBeInTheDocument()
  })

  it('顶部模型气泡可单独保存思考强度', async () => {
    let savedChanges: Record<string, unknown> | undefined
    server.use(http.patch('/api/users/kesepain/config', async ({ request }) => {
      const body = await request.json() as { changes: Record<string, unknown> }
      savedChanges = body.changes
      return HttpResponse.json({ user: 'kesepain', config: body.changes, redacted_paths: [], updated: true })
    }))
    renderApp('/chat?user=kesepain&session=s1')
    const providerButton = await screen.findByTitle('查看当前 Provider')
    await waitFor(() => expect(providerButton).toHaveTextContent('kemo · 中度'))
    fireEvent.click(providerButton)
    fireEvent.click(screen.getByRole('combobox', { name: '顶部模型思考强度' }))
    fireEvent.click(screen.getByRole('option', { name: /高.*深度推理/ }))
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
    const providerButton = await screen.findByTitle('查看当前 Provider')
    await waitFor(() => expect(providerButton).toHaveTextContent('轻度'))
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
    const providerButton = await screen.findByTitle('查看当前 Provider')
    await waitFor(() => expect(providerButton).toHaveTextContent('推理未启用'))
    fireEvent.click(providerButton)
    expect(screen.queryByRole('combobox', { name: '顶部模型思考强度' })).not.toBeInTheDocument()
    expect(screen.getByText('不可用')).toBeInTheDocument()
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
    const providerButton = await screen.findByTitle('查看当前 Provider')
    await waitFor(() => expect(providerButton).toHaveTextContent('chat · 中度'))
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
    expect(await screen.findByText(/已上传 note\.md/)).toBeInTheDocument()

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
    expect(await screen.findByText(/已上传 draft-note\.md/)).toBeInTheDocument()
    expect(screen.getByLabelText('待发送附件：draft-note.md')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('link', { name: /^配置$/ }))
    expect(await screen.findByRole('heading', { name: '配置' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('link', { name: /^对话$/ }))

    expect(await screen.findByRole('textbox', { name: '消息内容' })).toHaveValue('这段内容还没有发送')
    expect(screen.getByText(/已上传 draft-note\.md/)).toBeInTheDocument()
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

    expect(await screen.findByText(/已上传 late\.png/)).toBeInTheDocument()
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
    expect(await screen.findByText(/已上传 retry\.txt/)).toBeInTheDocument()
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

    expect(await screen.findByText('响应流意外结束，请重新发送')).toBeInTheDocument()
    await waitFor(() => expect(composer).toHaveValue('请重试这项任务'))
    expect(screen.getByText('旧正文')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '发送' }))

    expect(await screen.findByText('新正文')).toBeInTheDocument()
    expect(screen.getByText('旧正文')).toBeInTheDocument()
    expect(screen.queryByText('旧正文新正文')).not.toBeInTheDocument()
    expect(screen.queryByText('旧思考新思考')).not.toBeInTheDocument()
    expect(attempt).toBe(2)
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

    expect(await screen.findByText(/已上传 screenshot\.png/)).toBeInTheDocument()
    expect(screen.getByText(/已上传 bundle\.zip/)).toBeInTheDocument()
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

  it('对话操作菜单提供会话级长任务开关、保存新建、清空、压缩和重新生成', async () => {
    let compressionCalled = false
    let undoBody: Record<string, unknown> | null = null
    let chatBody: Record<string, unknown> | null = null
    let historyMessages = [
      { role: 'user', content: '上一条问题' },
      { role: 'assistant', content: '上一条回答' },
    ]
    const interceptedFetch = globalThis.fetch.bind(globalThis)
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
      if (!url.endsWith('/api/chat')) return interceptedFetch(input, init)
      chatBody = JSON.parse(String(init?.body)) as Record<string, unknown>
      historyMessages = [
        { role: 'user', content: '上一条问题' },
        { role: 'assistant', content: '重新生成的回答' },
      ]
      return new Response(
        'event: text_delta\ndata: {"type":"text_delta","content":"重新生成的回答"}\n\n'
        + 'event: done\ndata: {"type":"done"}\n\n',
        { headers: { 'Content-Type': 'text/event-stream' } },
      )
    }))
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
      http.post('/api/users/kesepain/sessions/s1/undo-last-round', async ({ request }) => {
        undoBody = await request.json() as Record<string, unknown>
        historyMessages = []
        return HttpResponse.json({
          user: 'kesepain', source: 'web', session_id: 's1', found: true,
          rolled_back: true, round: 1, remaining_rounds: 0, prompt: '上一条问题',
          content: [{ type: 'text', text: '上一条问题' }],
        })
      }),
    )
    renderApp('/chat?user=kesepain&session=s1')
    await screen.findByText('上一条回答')

    fireEvent.click(screen.getByRole('button', { name: '展开对话操作' }))
    expect(screen.getByRole('menuitem', { name: /保存此对话，创建新对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /清空此对话/ })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ })).toBeInTheDocument()
    const longTaskToggle = screen.getByRole('menuitemcheckbox', { name: /长任务模式/ })
    expect(longTaskToggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('menuitem', { name: /重新发送一次消息/ })).toBeInTheDocument()
    expect(screen.getByText('再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。')).toBeInTheDocument()

    fireEvent.click(longTaskToggle)
    await waitFor(() => expect(screen.getByRole('menuitemcheckbox', { name: /长任务模式/ })).toHaveAttribute('aria-checked', 'true'))

    fireEvent.click(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ }))
    await waitFor(() => expect(compressionCalled).toBe(true))
    expect(await screen.findByText('上下文压缩完成，已整理 2 轮历史。记忆提取已转入后台，共有 2 轮待处理。')).toBeInTheDocument()

    const regenerate = screen.getByRole('menuitem', { name: /重新发送一次消息/ })
    await waitFor(() => expect(regenerate).toBeEnabled())
    fireEvent.click(regenerate)
    await waitFor(() => expect(undoBody).toEqual({ expected_round: 1, prompt: '上一条问题' }))
    await waitFor(() => expect(chatBody).toMatchObject({
      session_id: 's1',
      prompt: '',
      content: [{ type: 'text', text: '上一条问题' }],
    }))
    await screen.findByText('重新生成的回答')
    await waitFor(() => expect(screen.getAllByText('重新生成的回答')).toHaveLength(1))
    expect(screen.queryByText('上一条回答')).not.toBeInTheDocument()
    expect(screen.getAllByText('上一条问题')).toHaveLength(1)
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
    const welcomeText = /当前用户的配置、历史、知识、任务与技能运行态已载入/
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
    fireEvent.click(screen.getByRole('button', { name: '发送引导' }))
    const firstCurrent = await screen.findByText('正在引导')
    const firstCard = firstCurrent.closest('article')!
    const guidancePreview = firstCard.closest('.composer-guidance-preview')!
    const composer = screen.getByRole('textbox', { name: '消息内容' }).closest('section[aria-label="消息输入区域"]')!
    expect(guidancePreview).toBeInTheDocument()
    expect(firstCard.closest('.messages')).toBeNull()
    expect(guidancePreview.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    streamController.enqueue(encoder.encode('event: guidance_applied\ndata: {"type":"guidance_applied","metadata":{"guidance":["先检查目录"]}}\n\n'))
    expect(await screen.findByText('智能体已读取该引导并继续运行')).toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '结果放入临时区' } })
    fireEvent.click(screen.getByRole('button', { name: '发送引导' }))
    expect(await screen.findByText('结果放入临时区')).toBeInTheDocument()
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
    expect(await screen.findByText(/已上传 voice\.mp3/)).toBeInTheDocument()
    const sendGuidance = screen.getByRole('button', { name: '发送引导' })
    expect(sendGuidance).toBeEnabled()
    fireEvent.click(sendGuidance)

    await waitFor(() => expect(guidanceBody).toMatchObject({
      guidance: '',
      uploaded_files: ['voice.mp3', 'clip.mp4'],
    }))
    expect(screen.queryByLabelText('待发送附件：voice.mp3')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('待发送附件：clip.mp4')).not.toBeInTheDocument()
    releaseGuidance()
    expect(await screen.findByText('voice.mp3')).toBeInTheDocument()
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
    await waitFor(() => expect(screen.queryByText(/已上传 voice\.mp3/)).not.toBeInTheDocument())
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
    const executionTurn = screen.getByText('任务计划执行完成。').closest('.assistant-turn')
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
    expect(await screen.findByText(/已上传 queued\.mp3/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('textbox', { name: '消息内容' }), { target: { value: '作为第二轮继续处理' } })
    fireEvent.click(screen.getByRole('button', { name: '发送引导' }))
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
    fireEvent.click(screen.getByRole('button', { name: '发送引导' }))
    firstStreamController.enqueue(encoder.encode('event: done\ndata: {"type":"done","metadata":{"committed":true}}\n\n'))
    firstStreamController.close()

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
    await waitFor(() => expect(getSearch()).toBe('?user=kesepain&session=conv_new_session'))
  })

  it('其他页面仍在使用对话时清空返回 409 并恢复原会话', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
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
    expect(screen.getAllByText('18.67%').length).toBeGreaterThan(0)
    expect(screen.queryByText('知识图谱')).not.toBeInTheDocument()
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

  it('顶部历史搜索保存当前对话后跳转到指定历史会话', async () => {
    let extractedSession = ''
    let closedSession = ''
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
    await waitFor(() => expect(getSearch()).toContain('session=s2'))
    expect(extractedSession).toBe('')
    expect(screen.queryByRole('dialog', { name: '历史对话' })).not.toBeInTheDocument()
  })

  it('输入框知识库按钮按层级展示卡片并把引用写入草稿', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    fireEvent.click(await screen.findByRole('button', { name: '打开知识库' }))

    const drawer = await screen.findByRole('dialog', { name: '知识库引用' })
    fireEvent.click(within(drawer).getByRole('button', { name: '用户' }))
    expect(await within(drawer).findByText('个人笔记')).toBeInTheDocument()
    expect(within(drawer).queryByText('共享笔记')).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 个人笔记' }))

    await waitFor(() => expect(screen.getByRole('textbox', { name: '消息内容' })).toHaveValue('[知识库引用 user:notes.md] 个人笔记'))
    expect(screen.queryByRole('dialog', { name: '知识库引用' })).not.toBeInTheDocument()
  })

  it('输入框能力按钮按类型引用拓展、技能和插件并追加稳定标记', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '先检查当前状态' } })
    fireEvent.click(screen.getByRole('button', { name: '打开能力引用' }))

    let drawer = await screen.findByRole('dialog', { name: '能力引用' })
    fireEvent.click(within(drawer).getByRole('button', { name: '全局' }))
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

  it('侧边栏不再渲染历史对话区，历史统一从右上角入口查看', async () => {
    server.use(http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
      user: 'kesepain', source: 'web', sessions: [session('s1', '历史 1', 1)],
    })))
    renderApp('/chat?user=kesepain')

    await screen.findByTitle('搜索历史对话')
    expect(screen.queryByText('最近对话')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /全部删除/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '历史 1' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '收缩侧边栏' }))
    const expandButton = screen.getByRole('button', { name: '展开侧边栏' })
    expect(screen.getAllByAltText('kemo-agent logo').length).toBeGreaterThan(0)
    expect(screen.getByRole('link', { name: '身份与人格' })).toHaveAttribute('title', '身份与人格')
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
    expect(screen.getByRole('switch', { name: 'Cron 自动退避' })).toBeChecked()
    expect(screen.getByRole('slider', { name: '退避触发阈值' })).toHaveValue('0.2')

    fireEvent.change(screen.getByRole('spinbutton', { name: '最大并发请求数' }), { target: { value: '12' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Web 排队槽位上限' }), { target: { value: '7' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '单个工具最大连续使用上限' }), { target: { value: '9' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '工具参数异常重试次数' }), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: '保存运行限制' }))
    await waitFor(() => expect(captured.globalChanges).toBeDefined())

    const providerRuntime = captured.globalChanges?.provider_runtime as Record<string, unknown>
    const web = captured.globalChanges?.web as Record<string, unknown>
    const tools = captured.globalChanges?.tools as Record<string, unknown>
    expect(providerRuntime.max_concurrent_requests).toBe(12)
    expect(web.max_pending_chats).toBe(7)
    expect(tools.consecutive_identical_call_limit).toBe(9)
    expect(tools.invalid_tool_arguments_retries).toBe(3)
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
