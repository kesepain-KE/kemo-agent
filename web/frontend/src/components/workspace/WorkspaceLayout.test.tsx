import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { openWorkspaceBlank, openWorkspaceJobs, openWorkspacePath, useWorkspaceStore } from '../../store/workspace'
import { WorkspaceLauncher, WorkspaceLayout } from './WorkspaceLayout'
import { ShellJobLink, shellJobId } from './ShellJobLink'

vi.mock('../../reference/FilePreview', () => ({
  FilePreview: ({ file, active }: { file: { name: string }; active: boolean }) =>
    <input aria-label={`Preview ${file.name}`} data-active={active} defaultValue="" />,
}))
vi.mock('./ShellJobs', () => ({ ShellJobs: () => <p>Jobs view</p>, ShellJobView: () => <p>Job view</p> }))
beforeEach(() => {
  useWorkspaceStore.setState({ users: {} })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 1300, right: 1500 } as DOMRect)
})
afterEach(() => vi.restoreAllMocks())

it('opens on an editable scratchpad by default; job lists require an explicit entry and instance console is absent', () => {
  render(<><WorkspaceLauncher user="alice" /><WorkspaceLayout user="alice" sessionId="s1"><span>Chat</span></WorkspaceLayout></>)
  fireEvent.click(screen.getByRole('button', { name: '工作面板' }))
  expect(screen.getByRole('tab', { name: '草稿本' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('textbox', { name: '草稿内容' })).toBeVisible()
  expect(screen.queryByText('Jobs view')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '实例终端' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Shell 后台任务' }))
  expect(screen.getByText('Jobs view')).toBeVisible()
  expect(useWorkspaceStore.getState().users.alice.activeId).toBe('jobs')
})

it('keeps file state across tab switching and collapse; closing does not send requests', () => {
  const fetchSpy = vi.spyOn(globalThis, 'fetch')
  openWorkspacePath('alice', 'download', 'a.md', 'a.md')
  openWorkspaceJobs('alice')
  const app = render(<WorkspaceLayout user="alice" sessionId="s1"><textarea aria-label="chat draft" /></WorkspaceLayout>)
  fireEvent.change(screen.getByLabelText('chat draft'), { target: { value: 'keep draft' } })
  fireEvent.click(screen.getByRole('tab', { name: 'a.md' }))
  fireEvent.change(screen.getByLabelText('Preview a.md'), { target: { value: 'keep preview state' } })
  fireEvent.click(screen.getByRole('tab', { name: 'Shell 后台任务' }))
  fireEvent.click(screen.getByRole('tab', { name: 'a.md' }))
  expect(screen.getByLabelText('Preview a.md')).toHaveValue('keep preview state')
  fireEvent.click(screen.getByRole('button', { name: '收起工作面板' }))
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  act(() => openWorkspaceJobs('alice'))
  fireEvent.click(screen.getByRole('tab', { name: 'a.md' }))
  expect(screen.getByLabelText('Preview a.md')).toHaveValue('keep preview state')
  expect(screen.getByLabelText('chat draft')).toHaveValue('keep draft')
  fireEvent.click(screen.getByRole('button', { name: '关闭标签 Shell 后台任务' }))
  expect(fetchSpy).not.toHaveBeenCalled()
  app.rerender(<WorkspaceLayout user="bob" sessionId="s2"><span>Other user</span></WorkspaceLayout>)
  expect(screen.queryByRole('tab')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Preview a.md')).not.toBeInTheDocument()
})

it('supports keyboard tab selection, pinning, resize limits, and responsive fullscreen', () => {
  openWorkspacePath('alice', 'download', 'a.md', 'a.md')
  openWorkspaceJobs('alice')
  render(<WorkspaceLayout user="alice" sessionId="s1"><span>Chat</span></WorkspaceLayout>)
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Shell 后台任务' }), { key: 'Home' })
  expect(screen.getByRole('tab', { name: 'a.md' })).toHaveAttribute('aria-selected', 'true')
  fireEvent.click(screen.getByRole('button', { name: '固定标签 Shell 后台任务' }))
  expect(screen.getAllByRole('tab')[0]).toHaveTextContent('Shell 后台任务')
  fireEvent.keyDown(screen.getByRole('separator'), { key: 'End' })
  expect(screen.getByRole('separator')).toHaveAttribute('aria-valuenow', '760')
  fireEvent.click(screen.getByRole('button', { name: '最大化面板' }))
  expect(screen.getByRole('dialog', { name: '工作面板' })).toHaveAttribute('aria-modal', 'true')
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})

it('opens validated shell results only on an explicit click', () => {
  const job_id = `job_${'b'.repeat(32)}`
  expect(shellJobId({ result: JSON.stringify({ data: { job_id } }) })).toBe(job_id)
  expect(shellJobId({ job_id: '../../private' })).toBeUndefined()
  expect(shellJobId('x'.repeat(200_001))).toBeUndefined()
  const view = render(<ShellJobLink user="alice" sessionId="s1" name="file" result={{ job_id }} />)
  expect(screen.queryByRole('button')).not.toBeInTheDocument()
  view.rerender(<ShellJobLink user="alice" sessionId="s1" name="shell" result={{ status: 'running', job_id }} />)
  expect(useWorkspaceStore.getState().users.alice).toBeUndefined()
  fireEvent.click(screen.getByRole('button', { name: '查看后台任务' }))
  expect(useWorkspaceStore.getState().users.alice.tabs[0]).toMatchObject({ type: 'job', jobId: job_id, sessionId: 's1', source: 'web' })
  expect(within(view.container).getAllByRole('button')).toHaveLength(1)
})
