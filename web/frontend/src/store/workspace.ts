import { create } from 'zustand'
import type { FileListEntry } from '../types/api'
import type { FileArea } from '../reference/FilePreview'

export type WorkspaceTab =
  | { id: string; type: 'file'; title: string; file: FileListEntry; area: FileArea; pinned?: boolean }
  | { id: string; type: 'blank'; title: string; pinned?: boolean }
  | { id: string; type: 'jobs'; title: string; pinned?: boolean }
  | { id: string; type: 'job'; title: string; jobId: string; sessionId: string; source: string; pinned?: boolean }
  | { id: string; type: 'agents'; title: string; pinned?: boolean }
  | { id: string; type: 'agent-task'; title: string; taskId: string; sessionId: string; source: string; pinned?: boolean }
export interface WorkspaceState {
  tabs: WorkspaceTab[]
  activeId: string
  open: boolean
  maximized: boolean
  width: number
  notice: string
}
const empty = (): WorkspaceState => ({ tabs: [], activeId: '', open: false, maximized: false, width: 480, notice: '' })
interface WorkspaceStore {
  users: Record<string, WorkspaceState>
  openTab: (user: string, tab: WorkspaceTab) => void
  select: (user: string, id: string) => void
  close: (user: string, id: string) => void
  patch: (user: string, patch: Partial<WorkspaceState>) => void
  pin: (user: string, id: string) => void
}
export const useWorkspaceStore = create<WorkspaceStore>(set => ({
  users: {},
  openTab: (user, tab) => {
    if (!user) return
    set(state => {
      const current = state.users[user] || empty()
      const exists = current.tabs.some(item => item.id === tab.id)
      const next = !exists && current.tabs.length >= 16
        ? { ...current, open: true, notice: '最多同时打开 16 个标签，请先关闭部分标签。' }
        : { ...current, open: true, activeId: tab.id, notice: '', tabs: exists ? current.tabs : [...current.tabs, tab] }
      return { users: { ...state.users, [user]: next } }
    })
  },
  select: (user, id) => set(state => {
    const current = state.users[user]
    if (!current?.tabs.some(tab => tab.id === id)) return state
    return { users: { ...state.users, [user]: { ...current, activeId: id, open: true } } }
  }),
  close: (user, id) => set(state => {
    const current = state.users[user]
    if (!current) return state
    const index = current.tabs.findIndex(tab => tab.id === id)
    const tabs = current.tabs.filter(tab => tab.id !== id)
    return { users: { ...state.users, [user]: { ...current, tabs, notice: '',
      activeId: current.activeId === id ? tabs[Math.min(index, tabs.length - 1)]?.id || '' : current.activeId,
      open: current.open && tabs.length > 0, maximized: current.maximized && tabs.length > 0,
    } } }
  }),
  patch: (user, patch) => {
    if (!user) return
    set(state => ({ users: { ...state.users, [user]: { ...(state.users[user] || empty()), ...patch } } }))
  },
  pin: (user, id) => set(state => {
    const current = state.users[user]
    if (!current) return state
    return { users: { ...state.users, [user]: { ...current, tabs: current.tabs.map(tab => tab.id === id ? { ...tab, pinned: !tab.pinned } : tab) } } }
  }),
}))
export function openWorkspaceFile(user: string, area: FileArea, file: FileListEntry) {
  useWorkspaceStore.getState().openTab(user, { id: `file:${area}:${file.relative_path}`, type: 'file', title: file.name, area, file })
}
export function openWorkspacePath(user: string, area: FileArea, path: string, name: string, size = 0) {
  openWorkspaceFile(user, area, { type: 'file', name, relative_path: path, parent_path: path.split('/').slice(0, -1).join('/'),
    size, updated_at: 0, extension: name.split('.').pop() || '', child_count: 0 })
}
export function openWorkspaceJobs(user: string) {
  useWorkspaceStore.getState().openTab(user, { id: 'jobs', type: 'jobs', title: 'Shell 后台任务' })
}
export function openWorkspaceBlank(user: string) {
  useWorkspaceStore.getState().openTab(user, { id: 'blank', type: 'blank', title: '草稿本' })
}
export function openWorkspaceJob(user: string, job: { job_id: string; session_id?: string; source?: string }) {
  useWorkspaceStore.getState().openTab(user, { id: `job:${job.job_id}`, type: 'job',
    title: `Shell · ${job.job_id.slice(-6)}`, jobId: job.job_id, sessionId: job.session_id || '', source: job.source || '' })
}
export function openWorkspaceAgents(user: string) {
  useWorkspaceStore.getState().openTab(user, { id: 'agents', type: 'agents', title: '智能体任务' })
}
export function openWorkspaceAgentTask(user: string, task: { id: string; agent?: string; source: string; session_id: string }) {
  useWorkspaceStore.getState().openTab(user, {
    id: `agent-task:${task.source}:${task.session_id}:${task.id}`, type: 'agent-task',
    title: `${task.agent || 'Agent'} · ${task.id.slice(-6)}`, taskId: task.id,
    source: task.source, sessionId: task.session_id,
  })
}
