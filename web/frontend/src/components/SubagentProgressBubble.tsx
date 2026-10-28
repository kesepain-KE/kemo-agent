import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronRight } from 'lucide-react'
import type { ChatItem } from '../types/api'
import { getAgentTask, getAgentTasks } from '../api/workspace'
import { openWorkspaceAgentTask, openWorkspaceAgents } from '../store/workspace'
import { t, useLanguage } from '../i18n'
import { ToolCallCard } from './RunEventCards'
import { agentStatus } from './workspace/AgentTasks'
import styles from './workspace/Workspace.module.css'

export function agentTaskId(value: unknown, depth = 0): string | undefined {
  if (depth > 4) return undefined
  if (typeof value === 'string') {
    if (value.length > 200000) return undefined
    try { return agentTaskId(JSON.parse(value), depth + 1) } catch { return undefined }
  }
  if (!value || typeof value !== 'object') return undefined
  const record = value as Record<string, unknown>
  if (typeof record.task_id === 'string' && /^agent-task-[a-f0-9]{32}$/.test(record.task_id)) return record.task_id
  return agentTaskId(record.result, depth + 1)
}

function dispatchArguments(item: Extract<ChatItem, { kind: 'tool' }>): Record<string, unknown> | undefined {
  if (item.arguments) return item.arguments
  if (item.argumentsTruncated || !item.argumentsText || item.argumentsText.length > 200000) return undefined
  try {
    const value: unknown = JSON.parse(item.argumentsText)
    return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined
  } catch { return undefined }
}

/** A simple dispatch link replaces the previous floating progress stack. */
export function SubagentToolCard({ item, user = '', sessionId = '' }: {
  item: Extract<ChatItem, { kind: 'tool' }>; user?: string; sessionId?: string
  progress?: ChatItem[]; running?: boolean
}) {
  useLanguage()
  const args = dispatchArguments(item)
  if (args?.action !== 'call') return <ToolCallCard item={item} user={user} sessionId={sessionId} />
  return <DispatchCard item={item} args={args} user={user} sessionId={sessionId} />
}

function DispatchCard({ item, args, user, sessionId }: {
  item: Extract<ChatItem, { kind: 'tool' }>; args: Record<string, unknown>; user: string; sessionId: string
}) {
  const id = agentTaskId(item.result) || agentTaskId(item.resultText)
  const list = useQuery({
    queryKey: ['dispatch-agent-list', user, sessionId],
    queryFn: () => getAgentTasks(user, sessionId, 1),
    enabled: Boolean(user && sessionId && !id && item.status === 'running'),
    retry: false, refetchInterval: query => query.state.status !== 'error' && !id && item.status === 'running'
      && !query.state.data?.tasks.some(root => root.children?.some(child => child.tool_call_id === item.callId)) ? 1500 : false,
  })
  const registered = list.data?.tasks.flatMap(root => root.children || []).find(task => task.tool_call_id === item.callId)
  const taskId = id || registered?.id
  const detail = useQuery({
    queryKey: ['workspace-agent', user, taskId, 'web', sessionId],
    queryFn: () => getAgentTask(user, taskId!, 'web', sessionId),
    enabled: Boolean(user && sessionId && taskId), retry: false,
    refetchInterval: query => query.state.status === 'error'
      || (query.state.data?.task.finished_at && !query.state.data?.task.execution_live) ? false : 1500,
  })
  const task = detail.data?.task || registered
  const agent = String(args?.agent || task?.agent || t('子智能体'))
  const status = detail.isError ? t('执行状态不可用') : task ? agentStatus(task.status)
    : item.status === 'error' ? t('分派失败')
      : list.isError ? t('提交状态不可用')
        : item.status === 'running' ? t('等待提交') : t('已提交，等待执行状态')
  const input = args?.input
  const summary = input && typeof input === 'object' ? String((input as Record<string, unknown>).task || (input as Record<string, unknown>).instruction || '') : ''
  return <article className="tool-call" data-subagent-call-id={item.callId}>
    <div className={styles.toolbar}><Bot size={16} /><strong>{agent}</strong>
      <span className={styles.badge}>{status}</span>
      <button className={styles.button} disabled={!user || !sessionId} onClick={() => taskId
        ? openWorkspaceAgentTask(user, { id: taskId, agent, source: 'web', session_id: sessionId })
        : openWorkspaceAgents(user)}>{t('查看执行')}<ChevronRight size={14} /></button>
    </div>
    {summary && <p className={styles.hint}>{summary.slice(0, 300)}</p>}
  </article>
}
