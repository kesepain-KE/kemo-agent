import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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
    fireEvent.click(await screen.findByRole('button', { name: '停止生成' }))
    expect(chatSignal?.aborted).toBe(true)

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

  it('对话操作菜单提供保存新建、清空、压缩和重新生成', async () => {
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
            status: 'completed', user: 'kesepain', source: 'web', session_id: 's1',
            round: 2, candidates: 1,
            extraction: { status: 'completed', candidate_count: 1 },
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
    expect(screen.getByRole('menuitem', { name: /重新发送一次消息/ })).toBeInTheDocument()
    expect(screen.getByText('再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('menuitem', { name: /手动进行一次上下文压缩/ }))
    await waitFor(() => expect(compressionCalled).toBe(true))
    expect(await screen.findByText('上下文压缩完成，已整理 2 轮历史。已同步提取 1 条记忆候选。')).toBeInTheDocument()

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
    fireEvent.click(within(historyDrawer).getByRole('button', { name: '确认切换' }))

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

  it('输入框拓展按钮打开侧边卡片并把稳定引用追加到草稿', async () => {
    renderApp('/chat?user=kesepain&session=s1')
    const composer = await screen.findByRole('textbox', { name: '消息内容' })
    fireEvent.change(composer, { target: { value: '先检查当前状态' } })
    fireEvent.click(screen.getByRole('button', { name: '打开拓展' }))

    const drawer = await screen.findByRole('dialog', { name: '拓展引用' })
    fireEvent.click(within(drawer).getByRole('button', { name: '全局' }))
    expect(await within(drawer).findByText('智能灯光控制')).toBeInTheDocument()
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索拓展' }), { target: { value: '客厅' } })
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 智能灯光控制' }))

    await waitFor(() => expect(composer).toHaveValue('先检查当前状态\n[拓展引用 global:example] 智能灯光控制'))
    expect(screen.queryByRole('dialog', { name: '拓展引用' })).not.toBeInTheDocument()
  })

  it('侧边栏不再渲染历史对话区，历史统一从右上角入口查看', async () => {
    server.use(http.get('/api/users/kesepain/sessions', () => HttpResponse.json({
      user: 'kesepain', source: 'web', sessions: [session('s1', '历史 1', 1)],
    })))
    renderApp('/chat?user=kesepain')

    await screen.findByTitle('搜索历史对话')
    expect(screen.queryByText('最近对话')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /全部删除/ })).not.toBeInTheDocument()
    expect(screen.queryByText('历史 1')).not.toBeInTheDocument()

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
    expect(screen.getByRole('switch', { name: 'Cron 自动退避' })).toBeChecked()
    expect(screen.getByRole('slider', { name: '退避触发阈值' })).toHaveValue('0.2')

    fireEvent.change(screen.getByRole('spinbutton', { name: '最大并发请求数' }), { target: { value: '12' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Web 排队槽位上限' }), { target: { value: '7' } })
    fireEvent.click(screen.getByRole('button', { name: '保存运行限制' }))
    await waitFor(() => expect(captured.globalChanges).toBeDefined())

    const providerRuntime = captured.globalChanges?.provider_runtime as Record<string, unknown>
    const web = captured.globalChanges?.web as Record<string, unknown>
    expect(providerRuntime.max_concurrent_requests).toBe(12)
    expect(web.max_pending_chats).toBe(7)
  })
})
