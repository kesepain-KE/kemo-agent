import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Terminal } from 'lucide-react'
import { activeJob, getShellJob, getShellJobs, type ShellJob } from '../../api/workspace'
import { t, useLanguage } from '../../i18n'
import { openWorkspaceJob } from '../../store/workspace'
import { formatDateTime } from '../ModuleUi'
import { TerminalView, type TerminalLine } from './TerminalView'
import styles from './Workspace.module.css'

const labels: Record<string, string> = { starting: '正在启动', running: '运行中', cancelling: '正在结束', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
function duration(job: ShellJob) {
  const seconds = ((job.finished_at ? Date.parse(job.finished_at) : Date.now()) - Date.parse(job.started_at)) / 1000
  return Number.isFinite(seconds) ? `${Math.max(0, Math.round(seconds))}s` : '—'
}
export function JobBadge({ job }: { job: ShellJob }) {
  return <span className={styles.badge} data-active={activeJob(job.status)}>{t(labels[job.status] || '状态未知')}</span>
}
export function ShellJobs({ user, sessionId, active }: { user: string; sessionId: string; active: boolean }) {
  return <ConversationShellJobs key={JSON.stringify([user, sessionId])} user={user} sessionId={sessionId} active={active} />
}
function ConversationShellJobs({ user, sessionId, active }: { user: string; sessionId: string; active: boolean }) {
  useLanguage()
  const [page, setPage] = useState(1)
  const bound = Boolean(user && sessionId)
  const query = useQuery({
    queryKey: ['workspace-jobs', user, 'web', sessionId, page],
    queryFn: () => getShellJobs(user, sessionId, page, 'web'),
    enabled: active && bound,
    retry: false,
    refetchInterval: active ? 2000 : false,
  })
  const jobs = query.data?.jobs.filter(job => job.source === 'web' && job.session_id === sessionId)
  return <>
    <div className={styles.toolbar}>
      <Terminal size={16} aria-hidden="true" /><strong>{t('当前对话任务')}</strong>
      <span className={styles.status}>{t('运行中')} {query.data?.active_count ?? '—'} · {t('全部')} {query.data?.total ?? '—'}</span>
      <button className={styles.icon} aria-label={t('刷新任务')} onClick={() => { if (bound) void query.refetch() }} disabled={!bound || query.isFetching}><RefreshCw size={15} className={query.isFetching ? 'spin' : undefined} /></button>
    </div>
    <p className={styles.hint}>{t('仅显示当前对话空间的 Shell 后台作业；关闭标签不会停止任务。')}</p>
    {query.isError && <div className={styles.notice} role="alert">{query.error.message}<button className={styles.button} onClick={() => void query.refetch()}>{t('重试')}</button></div>}
    <div className={styles.list}>
      {query.isPending && active && bound ? <div className={styles.empty} role="status">{t('正在读取后台任务…')}</div> : null}
      {(!bound || jobs?.length === 0) && <div className={styles.empty}><Terminal size={26} /><span>{t(bound ? '暂无后台任务' : '未绑定对话')}</span></div>}
      {jobs?.map(job => <button key={job.job_id} className={styles.job} onClick={() => openWorkspaceJob(user, job)}>
        <span className={styles.jobTitle}><Terminal size={15} /><code>{job.label || `Shell · ${job.job_id.slice(-6)}`}</code><JobBadge job={job} /></span>
        <span className={styles.jobCopy}><code>{job.job_id}</code> · PID {job.pid || '—'}</span>
        <span className={styles.jobCopy}>{t(job.owner_task_id ? '所属子代理' : '所属主任务')} · {job.owner_task_id || job.root_task_id || '—'}</span>
        <span className={styles.jobCopy}>{job.working_dir || '—'}</span>
        <span className={styles.jobCopy}>{t('开始时间')}：{formatDateTime(job.started_at)}</span>
        {job.finished_at && <span className={styles.jobCopy}>{t('完成时间')}：{formatDateTime(job.finished_at)} · {t('耗时')} {duration(job)}</span>}
        {!job.finished_at && activeJob(job.status) && <span className={styles.jobCopy}>{t('耗时')} {duration(job)}</span>}
        {job.notification_enabled && !job.notification_handled && !activeJob(job.status) && <span className={styles.jobCopy}>{t('待智能体处理')}</span>}
        {job.exit_code != null && <span className={styles.jobCopy}>{t('退出码')} {job.exit_code}</span>}
      </button>)}
    </div>
    {(query.data?.total_pages ?? 1) > 1 && <div className={styles.pager}>
      <button className={styles.button} disabled={page === 1} onClick={() => setPage(page - 1)}>{t('上一页')}</button>
      <span>{page} / {query.data?.total_pages}</span>
      <button className={styles.button} disabled={page >= (query.data?.total_pages ?? 1)} onClick={() => setPage(page + 1)}>{t('下一页')}</button>
    </div>}
  </>
}
export function ShellJobView({ user, jobId, sessionId, source, active }: {
  user: string; jobId: string; sessionId: string; source: string; active: boolean
}) {
  useLanguage()
  const [stream, setStream] = useState('all')
  const query = useQuery({
    queryKey: ['workspace-job', user, jobId, sessionId, source],
    queryFn: () => getShellJob(user, jobId, sessionId, source),
    enabled: active && Boolean(user),
    retry: false,
    refetchInterval: query => active && query.state.status !== 'error' && (!query.state.data || activeJob(query.state.data.job.status)) ? 2000 : false,
  })
  const job = query.data?.job
  const streams = query.data?.streams
  const lines: TerminalLine[] = (['stdout', 'stderr'] as const).flatMap(name =>
    (stream !== 'all' && stream !== name) || !streams?.[name].text ? [] : streams[name].text.replace(/\n$/, '').split('\n').slice(-1000)
      .map((text, index) => ({ id: `${name}:${index}`, text, stream: name })))
  return <>
    <div className={styles.meta}>
      <strong>{jobId}</strong>{job && <JobBadge job={job} />}
      <span>{source || '—'} · {sessionId || t('未绑定对话')}</span>
      <span>PID {job?.pid || '—'} · {job?.shell_type || '—'}</span>
      {job?.working_dir && <span>{job.working_dir}</span>}
      {job?.exit_code != null && <span>{t('退出码')} {job.exit_code}</span>}
      {job?.finished_at && <span>{t('完成时间')}：{formatDateTime(job.finished_at)}</span>}
    </div>
    <div className={styles.toolbar}>
      <select aria-label={t('输出流')} value={stream} onChange={event => setStream(event.target.value)}>
        <option value="all">OUT + ERR</option><option value="stdout">OUT</option><option value="stderr">ERR</option>
      </select>
      <span className={styles.status}>{t('只读')} · {t('输出按流分组显示')}</span>
    </div>
    {query.isError && <div className={styles.notice} role="alert">{query.error.message}</div>}
    {job?.error?.message && <div className={styles.notice} role="status">{job.error.message}</div>}
    {streams && Object.entries(streams).map(([name, info]) => info.error
      ? <div key={name} className={styles.notice} role="alert">{name}: {info.error}</div>
      : info.truncated || info.text.split('\n').length > 1000
        ? <p key={name} className={styles.hint}>{name}: {t('每个输出流最多显示最近 64 KiB、1000 行')}</p> : null)}
    <TerminalView lines={lines} active={active} pending={query.isFetching} onRefresh={() => void query.refetch()} />
  </>
}
