import { Terminal } from 'lucide-react'
import { t, useLanguage } from '../../i18n'
import { openWorkspaceJob } from '../../store/workspace'
import styles from './Workspace.module.css'

/** Inspect bounded structured tool results only; never execute or trust paths in them. */
export function shellJobId(value: unknown, depth = 0): string | undefined {
  if (depth > 4) return undefined
  if (typeof value === 'string') {
    if (value.length > 200_000) return undefined
    try { return shellJobId(JSON.parse(value), depth + 1) } catch { return undefined }
  }
  if (!value || typeof value !== 'object') return undefined
  const record = value as Record<string, unknown>
  if (typeof record.job_id === 'string' && /^job_[a-f0-9]{32}$/.test(record.job_id)) return record.job_id
  for (const key of ['result', 'data', 'content', 'output']) {
    const id = shellJobId(record[key], depth + 1)
    if (id) return id
  }
  return undefined
}
export function ShellJobLink({ user, sessionId, name, result, resultText }: {
  user: string; sessionId: string; name?: string; result: unknown; resultText?: string
}) {
  useLanguage()
  if (!user || !/(?:^|[._/])shell$/.test(name || '')) return null
  const jobId = shellJobId(result) || shellJobId(resultText)
  if (!jobId) return null
  return <div className={styles.toolbar}><button className={styles.button}
    onClick={() => openWorkspaceJob(user, { job_id: jobId, session_id: sessionId, source: 'web' })}>
    <Terminal size={15} />{t('查看后台任务')}
  </button></div>
}
