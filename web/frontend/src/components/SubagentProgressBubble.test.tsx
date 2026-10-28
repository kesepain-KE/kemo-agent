import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatItem } from '../types/api'
import { SubagentToolCard, agentTaskId } from './SubagentProgressBubble'
import { AgentTasks, AgentTaskView } from './workspace/AgentTasks'
import { openWorkspaceAgentTask, openWorkspaceAgents, openWorkspacePath, useWorkspaceStore } from '../store/workspace'
import { getAgentTask, getAgentTasks, type AgentTask } from '../api/workspace'

vi.mock('../api/workspace', () => ({ getAgentTask: vi.fn(), getAgentTasks: vi.fn() }))
const id = `agent-task-${'a'.repeat(32)}`
const task: AgentTask = { id, root_task_id: 'main', source: 'web', session_id: 's1', agent: 'worker',
  status: 'running', description: 'test child', created_at: '2026-10-24T00:00:00Z',
  events: [{ at: '2026-10-24T00:00:00Z', status: 'tool_finished', tool_name: 'file', elapsed_ms: 80, summary: '执行成功' }] }
const root: AgentTask = { ...task, id: 'main', root_task_id: '', agent: 'main', status: 'waiting_children', children: [task] }
const item: Extract<ChatItem, { kind: 'tool' }> = { id: 'tool-call', kind: 'tool', callId: 'call', name: 'subagent_dispatch',
  arguments: { action: 'call', agent: 'worker', input: { task: 'test child' } }, status: 'success', result: { ok: true, result: { status: 'queued', task_id: id } } }
function view(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>)
}
beforeEach(() => {
  vi.clearAllMocks()
  useWorkspaceStore.setState({ users: {} })
  vi.mocked(getAgentTask).mockResolvedValue({ task })
  vi.mocked(getAgentTasks).mockResolvedValue({ tasks: [root], total: 1, total_pages: 1 })
})

describe('dispatch card replaces floating subagent progress', () => {
  it('distinguishes unsubmitted calls from queued and running tasks', async () => {
    vi.mocked(getAgentTasks).mockResolvedValue({ tasks: [], total: 0, total_pages: 1 })
    const node = view(<SubagentToolCard item={{ ...item, status: 'running', result: undefined }} user="alice" sessionId="s1" />)
    expect(screen.getByText('等待提交')).toBeInTheDocument()
    expect(screen.queryByText('执行中')).not.toBeInTheDocument()
    expect(screen.queryByText('排队中')).not.toBeInTheDocument()
    await waitFor(() => expect(getAgentTasks).toHaveBeenCalled())
    node.unmount()
    vi.mocked(getAgentTask).mockResolvedValue({ task: { ...task, status: 'queued' } })
    view(<SubagentToolCard item={item} user="alice" sessionId="s1" />)
    expect(await screen.findByText('排队中')).toBeInTheDocument()
  })
  it('shares one discovery query across the same conversation batch', async () => {
    vi.mocked(getAgentTasks).mockResolvedValue({ tasks: [], total: 0, total_pages: 1 })
    view(<>{['a', 'b', 'c', 'd', 'e'].map(callId =>
      <SubagentToolCard key={callId} item={{ ...item, callId, status: 'running', result: undefined }}
        user="alice" sessionId="s1" />)}</>)
    expect(screen.getAllByText('等待提交')).toHaveLength(5)
    await waitFor(() => expect(getAgentTasks).toHaveBeenCalledTimes(1))
  })
  it('reports lookup failure without pretending a task is running', async () => {
    vi.mocked(getAgentTasks).mockRejectedValue(new Error('offline'))
    view(<SubagentToolCard item={{ ...item, status: 'running', result: undefined }} user="alice" sessionId="s1" />)
    expect(await screen.findByText('提交状态不可用')).toBeInTheDocument()
    expect(screen.queryByText('执行中')).not.toBeInTheDocument()
  })
  it('never displays a side bubble or reports queued as completed; opens only on click', async () => {
    openWorkspacePath('alice', 'download', 'a.md', 'a.md')
    view(<SubagentToolCard item={item} user="alice" sessionId="s1" />)
    expect(screen.queryByLabelText('子代理运行进度')).not.toBeInTheDocument()
    expect(screen.queryByText('已完成')).not.toBeInTheDocument()
    expect(screen.getByText('已提交，等待执行状态')).toBeInTheDocument()
    await screen.findByText('执行中')
    expect(useWorkspaceStore.getState().users.alice.activeId).toBe('file:download:a.md')
    fireEvent.click(screen.getByRole('button', { name: '查看执行' }))
    expect(useWorkspaceStore.getState().users.alice.tabs).toHaveLength(2)
    expect(useWorkspaceStore.getState().users.alice.tabs[1]).toMatchObject({ type: 'agent-task', taskId: id })
  })
  it('uses ordinary tool cards for non-dispatch operations', () => {
    view(<SubagentToolCard item={{ ...item, arguments: { action: 'list' } }} user="alice" sessionId="s1" />)
    expect(screen.queryByRole('button', { name: '查看执行' })).not.toBeInTheDocument()
    expect(getAgentTask).not.toHaveBeenCalled()
  })
  it('rejects untrusted task identifiers and overlarge encoded payloads', () => {
    expect(agentTaskId({ result: { task_id: id } })).toBe(id)
    expect(agentTaskId({ task_id: '../../private' })).toBeUndefined()
    expect(agentTaskId('x'.repeat(200001))).toBeUndefined()
  })
  it('restores the execution link from bounded historical JSON fields after refresh', async () => {
    view(<SubagentToolCard item={{ ...item, arguments: undefined, result: undefined,
      argumentsText: JSON.stringify(item.arguments), resultText: JSON.stringify(item.result) }} user="alice" sessionId="s1" />)
    await screen.findByText('执行中')
    fireEvent.click(screen.getByRole('button', { name: '查看执行' }))
    expect(useWorkspaceStore.getState().users.alice.tabs[0]).toMatchObject({ taskId: id, type: 'agent-task' })
  })
})
describe('agent workspace views', () => {
  it('shows parent stage and child tree and opens a dedicated detail tab', async () => {
    view(<AgentTasks user="alice" sessionId="s1" active />)
    await screen.findByText('等待子任务')
    expect(screen.getByText('必要依赖')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /worker/ }))
    expect(useWorkspaceStore.getState().users.alice.tabs[0]).toMatchObject({ type: 'agent-task', sessionId: 's1' })
  })
  it('shows public timeline, actual tool duration and final output without a terminal', async () => {
    vi.mocked(getAgentTask).mockResolvedValue({ task: { ...task, status: 'completed', result: { answer: 'public output' }, usage: { total_tokens: 23 } } })
    view(<AgentTaskView user="alice" taskId={id} source="web" sessionId="s1" active />)
    await screen.findByText('file')
    expect(screen.getByText(/80 ms/)).toBeInTheDocument()
    expect(screen.getByText(/public output/)).toBeInTheDocument()
    expect(screen.getByText('总 Token')).toBeInTheDocument()
    expect(screen.getByText('23')).toBeInTheDocument()
    expect(screen.queryByText('total_tokens: 23')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('实例终端')).not.toBeInTheDocument()
  })
  it('does not fetch while hidden and isolates query scope by user and session', async () => {
    const node = view(<AgentTasks user="alice" sessionId="s1" active={false} />)
    expect(getAgentTasks).not.toHaveBeenCalled()
    node.unmount()
    view(<AgentTaskView user="bob" taskId={id} source="web" sessionId="s2" active />)
    await waitFor(() => expect(getAgentTask).toHaveBeenCalledWith('bob', id, 'web', 's2'))
  })
  it('opens validated output files in the existing file tab without duplication', async () => {
    vi.mocked(getAgentTask).mockResolvedValue({ task: { ...task, status: 'completed',
      finished_at: '2026-10-24T00:01:00Z', artifacts: [{ scope: 'download', relative_path: 'report.md', name: 'report.md', size: 23 }] } })
    openWorkspacePath('alice', 'download', 'report.md', 'report.md', 23)
    view(<AgentTaskView user="alice" taskId={id} source="web" sessionId="s1" active />)
    fireEvent.click(await screen.findByRole('button', { name: 'report.md' }))
    expect(useWorkspaceStore.getState().users.alice.tabs).toHaveLength(1)
    expect(useWorkspaceStore.getState().users.alice.activeId).toBe('file:download:report.md')
  })
  it('deduplicates task tabs and closing them never cancels execution', () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    act(() => { openWorkspaceAgents('alice'); openWorkspaceAgentTask('alice', task); openWorkspaceAgentTask('alice', task) })
    expect(useWorkspaceStore.getState().users.alice.tabs).toHaveLength(2)
    useWorkspaceStore.getState().close('alice', useWorkspaceStore.getState().users.alice.activeId)
    expect(useWorkspaceStore.getState().users.alice.tabs).toHaveLength(1)
    expect(fetch).not.toHaveBeenCalled()
    fetch.mockRestore()
  })
})
