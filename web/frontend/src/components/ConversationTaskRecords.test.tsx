import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, it, vi } from 'vitest'
import type { AgentTask, ShellJob } from '../api/workspace'
import { languages, translate } from '../i18n'
import { useUiStore } from '../store/ui'
import { useWorkspaceStore } from '../store/workspace'
import { server } from '../test/server'
import { ConversationFilesPopover } from './ConversationFilesPopover'
import { conversationChildren } from './ConversationTaskRecords'

const at = '2026-10-25T01:45:00Z'
const clients: QueryClient[] = []
const job = (n = 1, patch: Partial<ShellJob> = {}): ShellJob => ({
  job_id: `job_${String(n).padStart(32, '0')}`, source: 'web', session_id: 's1',
  status: 'running', pid: 100, started_at: at, exit_code: null, shell_type: 'powershell', command_digest: 'hash', ...patch,
})
const child = (n = 1, patch: Partial<AgentTask> = {}): AgentTask => ({
  id: `agent-task-${String(n).padStart(32, '0')}`, root_task_id: 'root-1', agent: 'general_worker',
  source: 'web', session_id: 's1', description: `collect report ${n}`, created_at: at, status: 'running', ...patch,
})
const root = (children = [child()], patch: Partial<AgentTask> = {}): AgentTask => ({
  id: 'root-1', root_task_id: '', agent: 'main', source: 'web', session_id: 's1',
  description: 'main task', created_at: at, status: 'running', children, children_total: children.length, ...patch,
})
function mockRecords(jobs = [job()], tasks = [root()]) {
  server.use(
    http.get('/api/users/:user/sessions/:session/history', ({ params }) => HttpResponse.json({
      user: params.user, session_id: params.session, source: 'web', messages: [], round_metrics: [], round_traces: [],
    })),
    http.get('/api/users/:user/workspace/jobs', () => HttpResponse.json({
      jobs, active_count: 1, total: jobs.length, page: 1, total_pages: 1,
    })),
    http.get('/api/users/:user/workspace/agents', () => HttpResponse.json({ tasks, total: tasks.length, total_pages: 1 })),
  )
}
function mount(user = 'alice', sessionId = 's1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  clients.push(client)
  const content = (u: string, s: string) => <QueryClientProvider client={client}>
    <ConversationFilesPopover user={u} sessionId={s} />
  </QueryClientProvider>
  const view = render(content(user, sessionId))
  return { ...view, changeScope: (u: string, s: string) => view.rerender(content(u, s)) }
}
function open() { fireEvent.click(screen.getByRole('button', { name: '当前对话文件历史' })) }
afterEach(() => {
  clients.splice(0).forEach(client => client.clear())
  useWorkspaceStore.setState({ users: {} })
  useUiStore.getState().setLanguage('zh-CN', '')
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it('queries only the current Web conversation, and opens shell/agent details with their full scope', async () => {
  mockRecords()
  const requests: URL[] = []
  server.use(
    http.get('/api/users/alice/workspace/jobs', ({ request }) => {
      requests.push(new URL(request.url))
      return HttpResponse.json({ jobs: [job(), job(2, { source: 'cli' }), job(3, { session_id: 'foreign' })], total: 1, total_pages: 1 })
    }),
    http.get('/api/users/alice/workspace/agents', ({ request }) => {
      requests.push(new URL(request.url))
      return HttpResponse.json({ tasks: [root([child(), child(2, { session_id: 'foreign' }), child(3, { source: 'cli' })])], total: 1, total_pages: 1 })
    }),
  )
  mount()
  expect(requests).toHaveLength(0)
  open()
  const shell = await screen.findByRole('button', { name: '在工作面板打开 Shell · 000001' })
  expect(await screen.findByText('collect report 1')).toBeVisible()
  expect(screen.queryByText('collect report 2')).toBeNull()
  expect(screen.queryByText('collect report 3')).toBeNull()
  expect(screen.queryByRole('button', { name: /Shell · 000002/ })).toBeNull()
  for (const url of requests) {
    expect(url.searchParams.get('source')).toBe('web')
    expect(url.searchParams.get('session_id')).toBe('s1')
  }
  fireEvent.click(shell)
  expect(screen.queryByRole('region', { name: '当前对话文件历史' })).toBeNull()
  expect(useWorkspaceStore.getState().users.alice.tabs[0]).toMatchObject({
    type: 'job', jobId: job().job_id, source: 'web', sessionId: 's1',
  })
  open()
  fireEvent.click(await screen.findByRole('button', { name: /在工作面板打开 general_worker/ }))
  expect(useWorkspaceStore.getState().users.alice.tabs[1]).toMatchObject({
    type: 'agent-task', taskId: child().id, source: 'web', sessionId: 's1',
  })
  open()
  fireEvent.click(await screen.findByRole('button', { name: /在工作面板打开 general_worker/ }))
  expect(useWorkspaceStore.getState().users.alice.tabs).toHaveLength(2)
})

it('filters root scope and parent identity, deduplicating child records rather than listing main tasks', () => {
  expect(conversationChildren([
    root([child(), child(2, { root_task_id: 'another-root' }), child()]),
    root([child(3)], { source: 'cli' }),
    root([child(4)], { session_id: 'foreign' }),
  ], 's1').map(item => item.id)).toEqual([child().id])
})

it('reveals six more rows locally, then fetches subsequent pages without duplicate records', async () => {
  mockRecords()
  const shellPages: number[] = [], agentPages: number[] = []
  server.use(
    http.get('/api/users/alice/workspace/jobs', ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get('page'))
      shellPages.push(page)
      return HttpResponse.json({ jobs: page === 1 ? Array.from({ length: 8 }, (_, i) => job(i + 1)) : [job(8), job(9)],
        total: 9, total_pages: 2, page })
    }),
    http.get('/api/users/alice/workspace/agents', ({ request }) => {
      const page = Number(new URL(request.url).searchParams.get('page'))
      agentPages.push(page)
      return HttpResponse.json({ tasks: page === 1 ? [root(Array.from({ length: 8 }, (_, i) => child(i + 1)))]
        : [root([child(9, { root_task_id: 'root-2' })], { id: 'root-2' })], total: 2, total_pages: 2 })
    }),
  )
  mount()
  open()
  const shells = await screen.findByRole('region', { name: '后台 Shell 记录' })
  const agents = screen.getByRole('region', { name: '后台子智能体记录' })
  await waitFor(() => expect(within(shells).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(6))
  await waitFor(() => expect(within(agents).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(6))
  fireEvent.click(within(shells).getByText('加载更多 Shell 记录'))
  fireEvent.click(within(agents).getByText('加载更多子智能体记录'))
  expect(within(shells).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(8)
  expect(within(agents).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(8)
  expect(shellPages).toEqual([1])
  expect(agentPages).toEqual([1])
  fireEvent.click(within(shells).getByText('加载更多 Shell 记录'))
  fireEvent.click(within(agents).getByText('加载更多子智能体记录'))
  await waitFor(() => expect(within(shells).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(9))
  await waitFor(() => expect(within(agents).getAllByRole('button', { name: /在工作面板打开/ })).toHaveLength(9))
  expect(shellPages).toEqual([1, 2])
  expect(agentPages).toEqual([1, 2])
  expect(screen.queryByText('加载更多 Shell 记录')).toBeNull()
})

it('closes and clears old visible records on session/user change; unbound pages issue no requests', async () => {
  mockRecords()
  const view = mount()
  open()
  await screen.findByText('collect report 1')
  view.changeScope('alice', 's2')
  expect(screen.queryByRole('region')).toBeNull()
  open()
  await waitFor(() => expect(screen.queryByRole('region', { name: '后台 Shell 记录' })).toBeNull())
  expect(screen.queryByRole('region', { name: '后台子智能体记录' })).toBeNull()
  expect(screen.queryByText('collect report 1')).toBeNull()
  view.changeScope('bob', '')
  expect(screen.queryByRole('button', { name: '当前对话文件历史' })).toBeNull()
  const fetch = vi.spyOn(globalThis, 'fetch')
  view.changeScope('', 's1')
  expect(fetch).not.toHaveBeenCalled()
})

it('supports outside dismissal, Escape with focus restoration, and an explicit close button', async () => {
  mockRecords()
  mount()
  open()
  await screen.findByText('collect report 1')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(screen.queryByRole('region')).toBeNull()
  expect(screen.getByRole('button', { name: '当前对话文件历史' })).toHaveFocus()
  open()
  fireEvent.pointerDown(screen.getByText('相关文件'))
  expect(screen.getByRole('region', { name: '当前对话文件历史' })).toBeVisible()
  fireEvent.pointerDown(document.body)
  expect(screen.queryByRole('region')).toBeNull()
  open()
  fireEvent.click(screen.getByRole('button', { name: '关闭' }))
  expect(screen.queryByRole('region')).toBeNull()
})

it('keeps files/other records available on a section failure and supports retry', async () => {
  mockRecords()
  let fail = true
  server.use(http.get('/api/users/alice/workspace/jobs', () => fail
    ? HttpResponse.json({ error: { message: 'unavailable' } }, { status: 503 })
    : HttpResponse.json({ jobs: [job()], total: 1, total_pages: 1 })))
  mount()
  open()
  await screen.findByText('后台 Shell 记录读取失败')
  expect(await screen.findByText('collect report 1')).toBeVisible()
  expect(screen.getByText('此对话还没有关联文件')).toBeVisible()
  fail = false
  fireEvent.click(within(screen.getByRole('region', { name: '后台 Shell 记录' })).getByText('重试'))
  await screen.findByRole('button', { name: '在工作面板打开 Shell · 000001' })
  expect(screen.queryByText('后台 Shell 记录读取失败')).toBeNull()
})

it('refreshes status while open and stops polling when closed without sending task-control requests', async () => {
  vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] })
  mockRecords()
  let requests = 0, status = 'running'
  const methods: string[] = []
  server.use(http.get('/api/users/alice/workspace/jobs', ({ request }) => {
    requests++
    methods.push(request.method)
    return HttpResponse.json({ jobs: [job(1, { status })], total: 1, total_pages: 1 })
  }))
  mount()
  open()
  await screen.findByRole('button', { name: '在工作面板打开 Shell · 000001' })
  const before = requests
  status = 'completed'
  await act(() => vi.advanceTimersByTimeAsync(2100))
  await waitFor(() => expect(requests).toBeGreaterThan(before))
  expect(within(screen.getByRole('region', { name: '后台 Shell 记录' })).getByText('已完成')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '关闭' }))
  const closed = requests
  await act(() => vi.advanceTimersByTimeAsync(6100))
  expect(requests).toBe(closed)
  expect(methods.every(method => method === 'GET')).toBe(true)
})

it.each(languages)('translates record navigation and status in $code', ({ code }) => {
  for (const key of ['相关文件', '后台 Shell 记录', '后台子智能体记录', '加载更多 Shell 记录', '当前对话关联记录']) {
    expect(translate(key, code)).toBeTruthy()
    if (code !== 'zh-CN') expect(translate(key, code)).not.toBe(key)
  }
})
