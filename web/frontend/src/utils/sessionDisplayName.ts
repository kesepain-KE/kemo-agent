import { t as tr } from '../i18n'
import type { SessionSummary } from '../types/api'

/** Shared by the real history drawer and read-only archive dialog. */
export function sessionDisplayName(
  session: Pick<SessionSummary, 'title' | 'source' | 'session_id'>
    & Partial<Pick<SessionSummary, 'title_source' | 'summary_status'>>,
) {
  const source = session.source || ''
  const trustedTitle = session.title_source === 'manual'
    || session.title_source === 'summary'
    || session.summary_status === 'completed'
    || source.startsWith('background:cron:')
  const title = trustedTitle ? session.title?.trim() : ''
  if (title) return title
  if (source.startsWith('background:cron:')) {
    return `定时任务 · ${source.slice('background:cron:'.length)}`
  }
  if (source === 'web' || source === 'app' || source === 'cli') {
    return tr('未命名对话')
  }
  return tr('历史对话')
}
