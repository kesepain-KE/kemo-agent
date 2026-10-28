import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { server } from '../../test/server'
import { useWorkspaceStore } from '../../store/workspace'
import { ShellJobs, ShellJobView } from './ShellJobs'
import { TerminalView } from './TerminalView'

const id = `job_${'1'.repeat(32)}`
const job = { job_id: id, session_id: 's1', source: 'web', status: 'running', pid: 123,
  shell_type: 'powershell', working_dir: 'C:/workspace', started_at: '', exit_code: null }
const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
beforeEach(() => useWorkspaceStore.setState({ users: {} }))
afterEach(() => vi.useRealTimers())

it('separates completed terminals into individual cards with finish time and duration', async () => {
  server.use(http.get('/api/users/alice/workspace/jobs', () => HttpResponse.json({
    jobs: [
      { ...job, label: 'Build', root_task_id: 'main-1', status: 'completed', exit_code: 0,
        started_at: '2026-10-27T01:34:00Z', finished_at: '2026-10-27T01:36:00Z', notification_enabled: true },
      { ...job, job_id: `job_${'2'.repeat(32)}`, label: 'Tests', owner_task_id: 'child-1', status: 'failed',
        started_at: '2026-10-27T01:34:00Z', finished_at: '2026-10-27T01:35:00Z', exit_code: 1 },
    ], total: 2, active_count: 0, total_pages: 1, page: 1,
  })))
  render(<QueryClientProvider client={client()}><ShellJobs user="alice" sessionId="s1" active /></QueryClientProvider>)
  const first = await screen.findByRole('button', { name: /Build/ })
  const second = screen.getByRole('button', { name: /Tests/ })
  expect(first).not.toBe(second)
  expect(first).toHaveTextContent('完成时间')
  expect(first).toHaveTextContent('耗时 120s')
  expect(first).toHaveTextContent('待智能体处理')
  expect(second).toHaveTextContent('所属子代理')
  expect(second).toHaveTextContent('退出码 1')
})

it('requests only current Web conversation jobs and opens a separate read-only job tab', async () => {
  const scopes: Array<{ session: string; source: string }> = []
  server.use(http.get('/api/users/alice/workspace/jobs', ({ request }) => {
    const query = new URL(request.url).searchParams
    scopes.push({ session: query.get('session_id') || '', source: query.get('source') || '' })
    return HttpResponse.json({ jobs: [job, { ...job, job_id: `${id}-foreign`, session_id: 'other' },
      { ...job, job_id: `${id}-cli`, source: 'cli' }], total: 1, active_count: 1, total_pages: 1, page: 1 })
  }))
  render(<QueryClientProvider client={client()}><ShellJobs user="alice" sessionId="s1" active /></QueryClientProvider>)
  await screen.findByText(id)
  expect(scopes).toEqual([{ session: 's1', source: 'web' }])
  expect(screen.queryByRole('combobox', { name: '后台任务范围' })).not.toBeInTheDocument()
  expect(screen.queryByText(`${id}-foreign`)).not.toBeInTheDocument()
  expect(screen.queryByText(`${id}-cli`)).not.toBeInTheDocument()
  fireEvent.click(screen.getByText(id))
  expect(useWorkspaceStore.getState().users.alice.tabs[0]).toMatchObject({ type: 'job', jobId: id, sessionId: 's1' })
})

it('does not query all-user Shell jobs while no conversation is bound', async () => {
  const fetchSpy = vi.spyOn(globalThis, 'fetch')
  render(<QueryClientProvider client={client()}><ShellJobs user="alice" sessionId="" active /></QueryClientProvider>)
  expect(screen.getByText('未绑定对话')).toBeVisible()
  await waitFor(() => expect(fetchSpy).not.toHaveBeenCalled())
})

it('polls running jobs, stops at completion and does not poll a collapsed pane', async () => {
  let calls = 0
  server.use(http.get(`/api/users/alice/workspace/jobs/${id}`, () => {
    calls++
    return HttpResponse.json({
      job: { ...job, status: calls < 2 ? 'running' : 'completed', exit_code: calls < 2 ? null : 0 },
      streams: { stdout: { text: `output-${calls}\n`, size: 9 }, stderr: { text: 'warning\n', size: 8 } },
    })
  }))
  const qc = client()
  const view = render(<QueryClientProvider client={qc}><ShellJobView user="alice" jobId={id} sessionId="s1" source="web" active /></QueryClientProvider>)
  await screen.findByText('output-1')
  view.rerender(<QueryClientProvider client={qc}><ShellJobView user="alice" jobId={id} sessionId="s1" source="web" active={false} /></QueryClientProvider>)
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)) })
  expect(calls).toBe(1)
  view.rerender(<QueryClientProvider client={qc}><ShellJobView user="alice" jobId={id} sessionId="s1" source="web" active /></QueryClientProvider>)
  await screen.findByText('output-2')
  await screen.findByText('已完成')
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2200)) })
  expect(calls).toBe(2)
  fireEvent.change(screen.getByRole('combobox', { name: '输出流' }), { target: { value: 'stderr' } })
  expect(screen.queryByText('output-2')).not.toBeInTheDocument()
  expect(screen.getByText('warning')).toBeInTheDocument()
}, 10_000)

it('error recovery is explicit and late responses stay in their user cache', async () => {
  let release: (() => void) | undefined
  const pending = new Promise<void>(resolve => { release = resolve })
  server.use(
    http.get(`/api/users/alice/workspace/jobs/${id}`, async () => {
      await pending
      return HttpResponse.json({ job, streams: { stdout: { text: 'alice-private' }, stderr: { text: '' } } })
    }),
    http.get(`/api/users/bob/workspace/jobs/${id}`, () => HttpResponse.json({ error: { message: 'not found for bob' } }, { status: 404 })),
  )
  const qc = client()
  const view = render(<QueryClientProvider client={qc}><ShellJobView user="alice" jobId={id} sessionId="s1" source="web" active /></QueryClientProvider>)
  view.rerender(<QueryClientProvider client={qc}><ShellJobView user="bob" jobId={id} sessionId="s1" source="web" active /></QueryClientProvider>)
  await screen.findByText('not found for bob')
  await act(async () => { release?.(); await pending })
  await waitFor(() => expect(qc.getQueryData(['workspace-job', 'alice', id, 's1', 'web'])).toBeDefined())
  expect(screen.queryByText('alice-private')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '刷新输出' })).toBeEnabled()
})

it('terminal renders output as text, strips control codes and preserves search while inactive', () => {
  const props = { lines: [{ id: '1', text: '\x1b[31m<img src=x onerror=alert(1)>中文\x1b[0m', stream: 'stdout' },
    { id: '2', text: 'other', stream: 'stderr' }], active: true, pending: false, onRefresh: () => {} }
  const view = render(<TerminalView {...props} />)
  expect(view.container.querySelector('img')).toBeNull()
  expect(screen.getByText('<img src=x onerror=alert(1)>中文')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: '中文' } })
  view.rerender(<TerminalView {...props} active={false} />)
  view.rerender(<TerminalView {...props} />)
  expect(screen.getByRole('searchbox')).toHaveValue('中文')
  expect(screen.queryByText('other')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '暂停跟随' }))
  expect(screen.getByRole('button', { name: '跟随最新输出' })).toBeInTheDocument()
})
