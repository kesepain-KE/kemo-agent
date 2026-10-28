import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, it } from 'vitest'
import { server } from '../../test/server'
import type { AgentTask, AgentTaskEvent } from '../../api/workspace'
import { languages, translate } from '../../i18n'
import { useUiStore } from '../../store/ui'
import { AgentTasks, AgentTaskView, agentStatus } from './AgentTasks'
import { AgentTimeline, timelineRows } from './AgentTimeline'
import { AgentTaskResult } from './AgentTaskResult'
import { AgentUsage } from './AgentUsage'
import styles from './AgentTaskDetail.module.css'

const at = '2026-10-25T01:45:00Z'
const events: AgentTaskEvent[] = [
  { at, status: 'started' },
  { at, status: 'tool_running', tool_name: 'shell', tool_call_id: 'call-1', arguments: { command: 'python report.py' } },
  { at, status: 'tool_finished', tool_name: 'shell', tool_call_id: 'call-1', tool_status: 'completed', elapsed_ms: 928, result: { ok: true, result: 'public output' } },
]
const task: AgentTask = { id: 'agent-task-1', root_task_id: 'main-task-1', agent: 'general_worker',
  source: 'web', session_id: 's1', description: 'Collect data', status: 'completed',
  created_at: at, finished_at: at, events, result: { summary: '**Ready**', results: ['## Weather', '| City | Temp |', '| --- | --- |', '| Beijing | 12°C |'] },
  usage: { prompt_tokens: 580892, input_tokens: 580892, completion_tokens: 17638,
    output_tokens: 17638, total_tokens: 598530, cached_input_tokens: 518528,
    cached_prompt_tokens: 518528, cache_hit_rate: .893, provider_request_count: 18 } }

afterEach(() => useUiStore.getState().setLanguage('zh-CN', ''))

it('pairs only call IDs, preserves parameters on completion, and keeps simultaneous same-name calls distinct', () => {
  const rows = timelineRows([
    ...events.slice(0, 2),
    { at, status: 'tool_running', tool_name: 'shell', tool_call_id: 'call-2', arguments: { command: 'second' } },
    events[2],
    { at, status: 'tool_finished', tool_name: 'shell' },
  ])
  expect(rows).toHaveLength(4)
  expect(rows[1]).toMatchObject({ arguments: { command: 'python report.py' }, result: { ok: true } })
  expect(rows[2].arguments).toEqual({ command: 'second' })
  expect(timelineRows(events.slice(1))[0].key).toBe(timelineRows(events)[1].key)
})

it('puts an expandable action alongside the time and safely renders arguments/results as text', () => {
  const view = render(<AgentTimeline events={events} statusLabel={agentStatus} />)
  const summary = screen.getByText('shell').closest('summary')!
  const details = summary.parentElement!
  expect(details.previousElementSibling?.tagName).toBe('TIME')
  expect(details.hasAttribute('open')).toBe(false)
  fireEvent.click(summary)
  expect(details).toHaveAttribute('open')
  expect(screen.getByText(/python report.py/, { selector: 'pre' })).toBeVisible()
  expect(screen.getByText(/public output/)).toBeVisible()
  expect(screen.getAllByText('shell')).toHaveLength(1)
  view.rerender(<AgentTimeline events={[{ at, status: 'tool_finished', tool_name: 'file', arguments: '<img src=x onerror=alert(1)>', result: false }]} statusLabel={agentStatus} />)
  expect(view.container.querySelector('img')).toBeNull()
  fireEvent.click(screen.getByText('file').closest('summary')!)
  expect(screen.getByText('false')).toBeVisible()
})

it('hides model requests in the visible timeline without mutating stored events', () => {
  const original = [...events, { at, status: 'model_request', tool_name: 'must-not-show' }]
  const view = render(<AgentTimeline events={original} statusLabel={agentStatus} />)
  expect(screen.queryByText('请求模型')).toBeNull()
  expect(screen.queryByText('must-not-show')).toBeNull()
  expect(view.container.querySelectorAll('li')).toHaveLength(2)
  expect(original.at(-1)?.status).toBe('model_request')
})

it('shows the main-chat style single-line parameter preview followed by duration, chevron and status icon', () => {
  const view = render(<AgentTimeline events={[{ at, status: 'tool_finished', tool_name: 'file',
    tool_status: 'completed', elapsed_ms: 2100, arguments: { action: 'read', path: 'folder/report.md', api_key: 'never-display' },
    result: null }]} statusLabel={agentStatus} />)
  const summary = screen.getByText('file').closest('summary')!
  expect(summary.querySelector(`.${styles.argumentSummary}`)).toHaveTextContent('action: read · path: folder/report.md')
  expect(summary).not.toHaveTextContent('never-display')
  expect(summary).not.toHaveTextContent('已完成')
  expect(summary.querySelector(`.${styles.actionMeta}`)?.children[0]).toHaveTextContent('2.1 s')
  expect(screen.getByRole('img', { name: '已完成' })).toHaveAttribute('data-state', 'success')
  fireEvent.click(summary)
  expect(screen.getByText('null', { selector: 'pre' })).toBeVisible()
  view.rerender(<AgentTimeline events={[{ at, status: 'tool_finished', tool_name: 'legacy' }]} statusLabel={agentStatus} />)
  expect(view.container.querySelector(`.${styles.argumentSummary}`)).toBeEmptyDOMElement()
  expect(screen.getByRole('img', { name: '工具已返回' })).toHaveAttribute('data-state', 'unknown')
})

it.each([
  ['tool_running', undefined, 'running', '执行工具'],
  ['tool_finished', 'failed', 'error', '失败'],
  ['tool_finished', 'cancelled', 'unknown', '已取消'],
] as const)('shows an accessible truthful tool status: %s / %s', (status, tool_status, state, label) => {
  render(<AgentTimeline events={[{ at, status, tool_status, tool_name: 'shell' }]} statusLabel={agentStatus} />)
  expect(screen.getByRole('img', { name: label })).toHaveAttribute('data-state', state)
})

it('bounds preview text, preserves complete parameters, and does not translate user content', () => {
  act(() => useUiStore.getState().setLanguage('en', 'alice'))
  const argument = '运行中' + 'x'.repeat(1200)
  const view = render(<AgentTimeline events={[{ at, status: 'tool_running', tool_name: 'file', arguments: { query: argument } }]} statusLabel={agentStatus} />)
  const preview = view.container.querySelector(`.${styles.argumentSummary}`)!
  expect(preview.textContent).toHaveLength(512)
  expect(preview).toHaveTextContent('运行中')
  fireEvent.click(screen.getByText('file').closest('summary')!)
  expect(screen.getByText(/xxx/, { selector: 'pre' })).toHaveTextContent(argument)
  view.rerender(<AgentTimeline events={[{ at, status: 'tool_running', tool_name: 'file', arguments: {} }]} statusLabel={agentStatus} />)
  expect(screen.getByText('No parameters')).toBeVisible()
})

it('keeps an expanded action through polling and explains pending or missing historical data', () => {
  const view = render(<AgentTimeline events={[events[1]]} statusLabel={agentStatus} />)
  fireEvent.click(screen.getByText('shell').closest('summary')!)
  expect(screen.getByText('等待工具返回…')).toBeVisible()
  view.rerender(<AgentTimeline events={events.slice(1)} statusLabel={agentStatus} />)
  expect(screen.getByText('shell').closest('details')).toHaveAttribute('open')
  expect(screen.getByText(/public output/)).toBeVisible()
  view.rerender(<AgentTimeline events={[{ at, status: 'tool_finished', tool_name: 'legacy' }]} statusLabel={agentStatus} />)
  fireEvent.click(screen.getByText('legacy').closest('summary')!)
  expect(screen.getByText('此历史事件未保存调用参数')).toBeVisible()
  expect(screen.getByText('此历史事件未保存返回结果')).toBeVisible()
})

it('renders structured replies and row-array Markdown tables, retaining collapsed structured data', () => {
  const view = render(<AgentTaskResult result={task.result} />)
  expect(view.container.querySelector('strong')).toHaveTextContent('Ready')
  expect(screen.getByRole('heading', { name: 'Weather' })).toBeVisible()
  expect(screen.getByRole('table')).toBeVisible()
  expect(screen.getByRole('cell', { name: 'Beijing' })).toBeVisible()
  const body = view.container.querySelector(`.${styles.resultBody}`)!
  expect(body).toContainElement(screen.getByRole('table'))
  expect(body).not.toContainElement(screen.getByText('查看结构化数据'))
  expect(screen.getByText('查看结构化数据').closest('details')).not.toHaveAttribute('open')
  view.rerender(<AgentTaskResult result={{ custom_report: '# Report\n- first\n- second', nested: { answer: '**nested**' } }} />)
  expect(screen.getByRole('heading', { name: 'Report' })).toBeVisible()
  expect(screen.getAllByRole('listitem')).toHaveLength(2)
  expect(screen.getByText('nested', { selector: 'strong' })).toBeVisible()
})

it('renders plain and JSON-string replies, without executing unsafe HTML or links', () => {
  const view = render(<AgentTaskResult result={JSON.stringify({ answer: '# Result\n[unsafe](javascript:alert(1))\n<script>alert(1)</script>' })} />)
  expect(screen.getByRole('heading', { name: 'Result' })).toBeVisible()
  expect(view.container.querySelector('script')).toBeNull()
  expect(view.container.querySelector('a[href^="javascript:"]')).toBeNull()
  view.rerender(<AgentTaskResult result={'# Plain reply\n\n```python\nprint(1)\n```'} />)
  expect(screen.getByRole('heading', { name: 'Plain reply' })).toBeVisible()
  expect(view.container.querySelector('code')).toHaveTextContent('print(1)')
  expect(view.container.querySelector(`.${styles.resultBody}`)).toContainElement(screen.getByRole('heading', { name: 'Plain reply' }))
})

it('localizes usage labels and number formatting, formats percentages, and deduplicates aliases', () => {
  act(() => useUiStore.getState().setLanguage('en', 'alice'))
  const view = render(<AgentUsage usage={task.usage!} />)
  expect(screen.getByRole('region', { name: 'Token usage' })).toBeVisible()
  expect(screen.getAllByText('Input tokens')).toHaveLength(1)
  expect(screen.getAllByText('580,892')).toHaveLength(1)
  expect(screen.getAllByText('518,528')).toHaveLength(1)
  expect(screen.getByText('89.3%')).toBeVisible()
  expect(view.container).not.toHaveTextContent('prompt_tokens')
  act(() => useUiStore.getState().setLanguage('de', 'alice'))
  expect(screen.getByText('Eingabe-Token')).toBeVisible()
  expect(screen.getByText('580.892')).toBeVisible()
  expect(screen.getByText(/89,3/)).toBeVisible()
  view.rerender(<AgentUsage usage={{ cache_hit_rate: 0, input_tokens: 0 }} />)
  expect(screen.getByText('0')).toBeVisible()
})

it.each(languages)('has translated details and metrics for $code', ({ code }) => {
  for (const label of ['调用参数', '返回结果', 'Token 用量', '输入 Token', '推理 Token', '模型请求次数',
    '超时停止', '超时停止中', '执行轮次', '已有结论', '已保存进度，主智能体可继续此任务并重新计时。',
    '正在等待执行真实退出，暂不能继续任务。']) {
    expect(translate(label, code)).toBeTruthy()
    if (code !== 'zh-CN') expect(translate(label, code)).not.toBe(label)
  }
})

it('distinguishes stopped timeout from still-stopping execution and displays the original task round', async () => {
  let current: AgentTask = { ...task, status: 'timed_out', run_count: 2, resumable: true }
  server.use(http.get('/api/users/alice/workspace/agents/agent-task-1', () => HttpResponse.json({ task: current })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}>
    <AgentTaskView user="alice" taskId={task.id} source="web" sessionId="s1" active />
  </QueryClientProvider>)
  expect(await screen.findByText('超时停止')).toBeVisible()
  expect(screen.getByText('执行轮次：2')).toBeVisible()
  expect(screen.getByText('已有结论')).toBeVisible()
  expect(screen.getByText('已保存进度，主智能体可继续此任务并重新计时。')).toBeVisible()
  expect(screen.getByText('shell')).toBeVisible()
  current = { ...current, status: 'timed_out_running', resumable: false, execution_live: true }
  await act(async () => { await client.refetchQueries({ queryKey: ['workspace-agent'] }) })
  expect(await screen.findByText('超时停止中')).toBeVisible()
  expect(screen.getByText('正在等待执行真实退出，暂不能继续任务。')).toBeVisible()
  expect(screen.queryByText('已保存进度，主智能体可继续此任务并重新计时。')).toBeNull()
})

it('shows budget stops as resumable partial work with segment and cumulative counts', async () => {
  const stopped: AgentTask = { ...task, status: 'tool_limit', run_count: 2, resumable: true,
    execution_live: false, stop_reason: 'tool_budget_exhausted',
    budget: { max_tool_calls: 80, unlimited: false, tool_calls_used: 80, tool_calls_remaining: 0,
      total_tool_calls: 160, timeout: 600, elapsed_seconds: 300, last_tool: 'shell' } }
  server.use(http.get('/api/users/alice/workspace/agents/agent-task-1', () => HttpResponse.json({ task: stopped })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}>
    <AgentTaskView user="alice" taskId={task.id} source="web" sessionId="s1" active />
  </QueryClientProvider>)
  expect(await screen.findByText('工具额度停止')).toHaveAttribute('data-active', 'false')
  expect(screen.getByText('已保存进度，主智能体可继续此任务并分配新一段工具额度。')).toBeVisible()
  expect(screen.getByText('本段工具调用：80 / 80')).toBeVisible()
  expect(screen.getByText('累计工具调用：160')).toBeVisible()
  expect(screen.getByRole('heading', { name: '已有结论' })).toBeVisible()
  for (const language of languages) {
    if (language.code === 'zh-CN') continue
    expect(translate('工具额度停止', language.code)).not.toBe('工具额度停止')
    expect(translate('不限次数', language.code)).not.toBe('不限次数')
  }
})

it('integrates the detail view without raw metric names or duplicate tool cards', async () => {
  server.use(http.get('/api/users/alice/workspace/agents/agent-task-1', () => HttpResponse.json({ task })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}>
    <AgentTaskView user="alice" taskId={task.id} source="web" sessionId="s1" active />
  </QueryClientProvider>)
  expect(await screen.findByRole('table')).toBeVisible()
  expect(screen.getAllByText('shell')).toHaveLength(1)
  expect(screen.getByText('开始执行')).toBeVisible()
  expect(screen.getByText('Token 用量')).toBeVisible()
})

it('keeps submitted task order and visible ordinals when concurrent results arrive', async () => {
  let children: AgentTask[] = [
    { ...task, id: 'z-first', submission_order: 1, description: 'First submitted', status: 'running', finished_at: '' },
    { ...task, id: 'a-second', submission_order: 2, description: 'Second submitted', status: 'completed' },
  ]
  server.use(http.get('/api/users/alice/workspace/agents', () => HttpResponse.json({
    tasks: [{ ...task, id: 'main-task-1', root_task_id: '', agent: 'main', children, children_total: 2 }],
    total: 1, total_pages: 1,
  })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><AgentTasks user="alice" sessionId="s1" active /></QueryClientProvider>)
  await screen.findByText('First submitted')
  const jobButtons = () => screen.getAllByRole('button').filter(button => button.textContent?.includes('general_worker'))
  expect(jobButtons()[0]).toHaveTextContent('#1')
  expect(jobButtons()[0]).toHaveTextContent('First submitted')
  expect(jobButtons()[1]).toHaveTextContent('#2')
  children = children.map(child => ({ ...child, status: 'completed', finished_at: at }))
  await act(async () => { await client.refetchQueries({ queryKey: ['workspace-agents'] }) })
  expect(jobButtons()[0]).toHaveTextContent('First submitted')
  expect(jobButtons()[1]).toHaveTextContent('Second submitted')
})

it('contains each main task and its own children in separate named groups even with matching times and agents', async () => {
  const first = { ...task, id: 'child-a', root_task_id: 'main-a', description: 'Only belongs to A', submission_order: 1 }
  const second = { ...task, id: 'child-b', root_task_id: 'main-b', description: 'Only belongs to B', submission_order: 1 }
  let roots: AgentTask[] = [
    { ...task, id: 'main-a', root_task_id: '', agent: 'main', description: 'Parent A', children: [first], children_total: 1 },
    { ...task, id: 'main-b', root_task_id: '', agent: 'main', description: 'Parent B', children: [second], children_total: 1 },
  ]
  server.use(http.get('/api/users/alice/workspace/agents', () => HttpResponse.json({ tasks: roots, total: 2, total_pages: 1 })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  render(<QueryClientProvider client={client}><AgentTasks user="alice" sessionId="s1" active /></QueryClientProvider>)
  const groupA = await screen.findByRole('region', { name: '主任务 · main-a' })
  const groupB = screen.getByRole('region', { name: '主任务 · main-b' })
  const childrenA = within(groupA).getByRole('group', { name: '子智能体' })
  const childrenB = within(groupB).getByRole('group', { name: '子智能体' })
  expect(groupA).toContainElement(within(groupA).getByRole('heading', { name: '主任务' }))
  expect(within(childrenA).getByRole('button')).toHaveTextContent('Only belongs to A')
  expect(within(childrenB).getByRole('button')).toHaveTextContent('Only belongs to B')
  expect(groupA).not.toContainElement(childrenB)
  expect(groupB).not.toContainElement(childrenA)
  roots = roots.map(root => ({ ...root, status: 'running', children: root.children?.map(child => ({ ...child, status: 'running' })) }))
  await act(async () => { await client.refetchQueries({ queryKey: ['workspace-agents'] }) })
  expect(screen.getByRole('region', { name: '主任务 · main-a' })).toBe(groupA)
  expect(within(childrenA).getByRole('button')).toHaveTextContent('Only belongs to A')
  expect(within(childrenB).getByRole('button')).toHaveTextContent('Only belongs to B')
})

it('omits empty child containers, shows bounded child counts, and preserves parent descriptions verbatim', async () => {
  const roots: AgentTask[] = [
    { ...task, id: 'empty-main', root_task_id: '', description: '主任务 <script>not executable</script>', children: [], children_total: 0 },
    { ...task, id: 'limited-main', root_task_id: '', children: [task], children_total: 12 },
  ]
  server.use(http.get('/api/users/alice/workspace/agents', () => HttpResponse.json({ tasks: roots, total: 2, total_pages: 1 })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const view = render(<QueryClientProvider client={client}><AgentTasks user="alice" sessionId="s1" active /></QueryClientProvider>)
  const empty = await screen.findByRole('region', { name: '主任务 · empty-main' })
  expect(within(empty).queryByRole('group', { name: '子智能体' })).toBeNull()
  expect(empty).toHaveTextContent('主任务 <script>not executable</script>')
  expect(view.container.querySelector('script')).toBeNull()
  expect(within(screen.getByRole('region', { name: '主任务 · limited-main' })).getByRole('group')).toHaveTextContent('1 / 12')
})
