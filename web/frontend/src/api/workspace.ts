import { requestJson } from './transport'

export interface ShellJob {
  job_id: string
  status: string
  source: string
  session_id: string
  pid: number
  started_at: string
  finished_at?: string
  exit_code: number | null
  shell_type: string
  working_dir?: string
  command_digest: string
  label?: string
  root_task_id?: string
  owner_task_id?: string
  notification_enabled?: boolean
  notification_handled?: boolean
  error?: { message?: string } | null
}
export interface ShellJobsResponse {
  jobs: ShellJob[]
  active_count: number
  total: number
  page: number
  total_pages: number
}
export interface JobStream { text: string; size: number; truncated: boolean; error: string }
export interface ShellJobDetail {
  job: ShellJob
  streams: { stdout: JobStream; stderr: JobStream }
  generated_at: string
}
export const activeJob = (status: string) => ['starting', 'running', 'cancelling'].includes(status)
export function getShellJobs(user: string, sessionId = '', page = 1, source = '') {
  const query = new URLSearchParams({ session_id: sessionId, page: String(page) })
  if (source) query.set('source', source)
  return requestJson<ShellJobsResponse>(`/api/users/${encodeURIComponent(user)}/workspace/jobs?${query}`)
}
export function getShellJob(user: string, jobId: string, sessionId = '', source = '') {
  const query = new URLSearchParams({ session_id: sessionId, source })
  return requestJson<ShellJobDetail>(`/api/users/${encodeURIComponent(user)}/workspace/jobs/${encodeURIComponent(jobId)}?${query}`)
}

export interface AgentTaskEvent {
  at: string
  status: string
  tool_name?: string
  tool_call_id?: string
  tool_status?: string
  elapsed_ms?: number
  run_count?: number
  iteration?: number
  summary?: string
  arguments?: unknown
  result?: unknown
}
export interface AgentTask {
  id: string
  root_task_id: string
  submission_order?: number
  agent: string
  description: string
  status: string
  source: string
  session_id: string
  created_at: string
  started_at?: string
  finished_at?: string
  tool_call_id?: string
  handled?: boolean
  execution_live?: boolean
  run_count?: number
  resumable?: boolean
  active_elapsed_ms?: number
  stop_reason?: string | null
  budget?: {
    max_tool_calls: number; unlimited: boolean; tool_calls_used: number
    tool_calls_remaining: number | null; total_tool_calls: number
    timeout: number; elapsed_seconds: number; last_tool: string
  }
  origin_task_id?: string
  result?: unknown
  error?: { message?: string } | null
  usage?: Record<string, number>
  events?: AgentTaskEvent[]
  children?: AgentTask[]
  children_total?: number
  phase?: string
  active_tool?: string
  artifacts?: { scope: 'file_upload' | 'download'; relative_path: string; name: string; size: number }[]
}
export function getAgentTasks(user: string, sessionId: string, page: number) {
  const query = new URLSearchParams({ session_id: sessionId, source: 'web', page: String(page) })
  return requestJson<{ tasks: AgentTask[]; total: number; total_pages: number }>(`/api/users/${encodeURIComponent(user)}/workspace/agents?${query}`)
}
export function getAgentTask(user: string, id: string, source: string, sessionId: string) {
  const query = new URLSearchParams({ source, session_id: sessionId })
  return requestJson<{ task: AgentTask }>(`/api/users/${encodeURIComponent(user)}/workspace/agents/${encodeURIComponent(id)}?${query}`)
}
