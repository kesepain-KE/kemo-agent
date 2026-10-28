import { useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Bot, ChevronRight, Terminal } from 'lucide-react'
import { getAgentTasks, getShellJobs, type AgentTask, type ShellJob } from '../api/workspace'
import { t, useLanguage } from '../i18n'
import { openWorkspaceAgentTask, openWorkspaceJob } from '../store/workspace'
import { formatDateTime } from './ModuleUi'
import { agentStatus } from './workspace/AgentTasks'
import styles from './ConversationFilesPopover.module.css'

const BATCH = 6
const shellLabels: Record<string, string> = {
  starting: '正在启动', running: '运行中', cancelling: '正在结束', completed: '已完成',
  failed: '失败', cancelled: '已取消', interrupted: '已中断',
}

/** Only children of a verified current-conversation root belong in this entry. */
export function conversationChildren(roots: AgentTask[], sessionId: string) {
  const children = new Map<string, AgentTask>()
  for (const root of roots) {
    if (root.source !== 'web' || root.session_id !== sessionId) continue
    for (const child of root.children || []) {
      if (child.source !== 'web' || child.session_id !== sessionId
        || child.root_task_id !== root.id || children.has(child.id)) continue
      children.set(child.id, child)
    }
  }
  return [...children.values()].sort((a, b) => b.created_at.localeCompare(a.created_at))
}

function ShellRecord({ user, job, onOpen }: { user: string; job: ShellJob; onOpen: () => void }) {
  const name = `Shell · ${job.job_id.slice(-6)}`
  const status = t(shellLabels[job.status] || '状态未知')
  return <button type="button" className={`${styles.entry} ${styles.record}`}
    aria-label={`${t('在工作面板打开')} ${name}`}
    onClick={() => { openWorkspaceJob(user, job); onOpen() }}>
    <span className={styles.fileIcon}><Terminal size={17} aria-hidden="true" /></span>
    <span className={styles.copy}>
      <strong title={job.job_id}>{name}</strong>
      <small title={job.working_dir}>{job.shell_type || 'Shell'} · {formatDateTime(job.started_at)}</small>
      <small>{status}{job.exit_code != null ? ` · ${t('退出码')} ${job.exit_code}` : ''}</small>
    </span>
    <ChevronRight size={14} className={styles.recordArrow} aria-hidden="true" />
  </button>
}

function AgentRecord({ user, task, onOpen }: { user: string; task: AgentTask; onOpen: () => void }) {
  const name = `${task.agent || t('子智能体')} · ${task.id.slice(-6)}`
  return <button type="button" className={`${styles.entry} ${styles.record}`}
    aria-label={`${t('在工作面板打开')} ${name}`}
    onClick={() => { openWorkspaceAgentTask(user, task); onOpen() }}>
    <span className={styles.fileIcon}><Bot size={17} aria-hidden="true" /></span>
    <span className={styles.copy}>
      <strong title={name}>{name}</strong>
      {task.description && <small title={task.description}>{task.description}</small>}
      <small>{agentStatus(task.status)} · {formatDateTime(task.created_at)}</small>
    </span>
    <ChevronRight size={14} className={styles.recordArrow} aria-hidden="true" />
  </button>
}

/** Mounted only while the popover is open; no polling or detail/log requests when closed. */
export function ConversationTaskRecords({ user, sessionId, onOpen }: {
  user: string; sessionId: string; onOpen: () => void
}) {
  useLanguage()
  const [jobLimit, setJobLimit] = useState(BATCH)
  const [agentLimit, setAgentLimit] = useState(BATCH)
  const enabled = Boolean(user && sessionId)
  const jobs = useInfiniteQuery({
    queryKey: ['conversation-related-jobs', user, 'web', sessionId],
    queryFn: ({ pageParam }) => getShellJobs(user, sessionId, pageParam, 'web'),
    initialPageParam: 1,
    getNextPageParam: (last, _pages, page) => page < last.total_pages ? page + 1 : undefined,
    enabled, retry: false, refetchOnWindowFocus: false,
    refetchInterval: query => query.state.status === 'error' ? false : 2000,
  })
  const agents = useInfiniteQuery({
    queryKey: ['conversation-related-agents', user, 'web', sessionId],
    queryFn: ({ pageParam }) => getAgentTasks(user, sessionId, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last, _pages, page) => page < last.total_pages ? page + 1 : undefined,
    enabled, retry: false, refetchOnWindowFocus: false,
    refetchInterval: query => query.state.status === 'error' ? false : 2000,
  })
  const jobMap = new Map<string, ShellJob>()
  for (const page of jobs.data?.pages || []) {
    for (const job of page.jobs) {
      if (job.source === 'web' && job.session_id === sessionId && !jobMap.has(job.job_id)) jobMap.set(job.job_id, job)
    }
  }
  const records = [...jobMap.values()].sort((a, b) => b.started_at.localeCompare(a.started_at))
  const children = conversationChildren(agents.data?.pages.flatMap(page => page.tasks) || [], sessionId)
  const showMoreJobs = jobLimit < records.length || jobs.hasNextPage
  const showMoreAgents = agentLimit < children.length || agents.hasNextPage
  const incompleteChildren = agents.data?.pages.some(page => page.tasks.some(root =>
    root.source === 'web' && root.session_id === sessionId && (root.children_total || 0) > (root.children?.length || 0)))
  const showShellSection = records.length > 0 || jobs.isPending || jobs.isError || jobs.hasNextPage
  const showAgentSection = children.length > 0 || agents.isPending || agents.isError || agents.hasNextPage || incompleteChildren
  if (!enabled) return null
  return <>
    {showShellSection && <section className={styles.section} aria-label={t('后台 Shell 记录')}>
      <div className={styles.sectionHead}><span>{t('后台 Shell 记录')}</span><small>{records.length > 0 ? jobs.data?.pages[0]?.total ?? records.length : '—'}</small></div>
      {jobs.isPending && <p className={styles.recordNotice} role="status">{t('正在读取后台任务…')}</p>}
      {jobs.isError && <div className={styles.error} role="alert">{t('后台 Shell 记录读取失败')}
        <button type="button" className={styles.retry} onClick={() => void jobs.refetch()}>{t('重试')}</button></div>}
      <div className={styles.list}>{records.slice(0, jobLimit).map(job =>
        <ShellRecord key={job.job_id} user={user} job={job} onOpen={onOpen} />)}</div>
      {showMoreJobs && <button type="button" className={styles.more} disabled={jobs.isFetching}
        onClick={async () => {
          if (jobLimit >= records.length && jobs.hasNextPage) {
            const next = await jobs.fetchNextPage()
            if (next.isError) return
          }
          setJobLimit(value => value + BATCH)
        }}>{t('加载更多 Shell 记录')}</button>}
    </section>}
    {showAgentSection && <section className={styles.section} aria-label={t('后台子智能体记录')}>
      <div className={styles.sectionHead}><span>{t('后台子智能体记录')}</span><small>{children.length || '—'}{agents.hasNextPage || incompleteChildren ? '+' : ''}</small></div>
      {agents.isPending && <p className={styles.recordNotice} role="status">{t('正在读取任务…')}</p>}
      {agents.isError && <div className={styles.error} role="alert">{t('子智能体记录读取失败')}
        <button type="button" className={styles.retry} onClick={() => void agents.refetch()}>{t('重试')}</button></div>}
      <div className={styles.list}>{children.slice(0, agentLimit).map(task =>
        <AgentRecord key={task.id} user={user} task={task} onOpen={onOpen} />)}</div>
      {showMoreAgents && <button type="button" className={styles.more} disabled={agents.isFetching}
        onClick={async () => {
          if (agentLimit >= children.length && agents.hasNextPage) {
            const next = await agents.fetchNextPage()
            if (next.isError) return
          }
          setAgentLimit(value => value + BATCH)
        }}>{t('加载更多子智能体记录')}</button>}
      {incompleteChildren && <p className={styles.recordNotice}>{t('部分子任务未返回，请在任务总览查看')}</p>}
    </section>}
  </>
}
