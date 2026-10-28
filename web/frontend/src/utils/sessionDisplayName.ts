import type { SessionSummary } from '../types/api'

/** Shared by the real history drawer and read-only archive dialog. */
export function sessionDisplayName(session: Pick<SessionSummary, 'title' | 'source' | 'session_id'>) {
  const title = session.title?.trim()
  if (title) return title
  if (session.source?.startsWith('background:cron:')) {
    return `定时任务 · ${session.source.slice('background:cron:'.length)}`
  }
  const id = session.session_id
  return id.startsWith('web_') && id.length > 16 ? `Web 会话 · ${id.slice(4, 12)}` : id
}
