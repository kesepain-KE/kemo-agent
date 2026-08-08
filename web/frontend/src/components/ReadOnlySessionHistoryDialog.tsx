import { useInfiniteQuery } from '@tanstack/react-query'
import { createPortal } from 'react-dom'
import { Archive, LoaderCircle, X } from 'lucide-react'
import { getHistory } from '../api/client'
import type { HistoryMessage, SessionSummary } from '../types/api'
import { formatDateTime } from './ModuleUi'
import { sessionDisplayName } from './SessionHistoryPanel'
import styles from './ReadOnlySessionHistoryDialog.module.css'

const PAGE_SIZE = 40

function sourceLabel(session: SessionSummary) {
  const source = session.source || 'web'
  if (source === 'web') return '网页版'
  if (source === 'cli') return 'CLI'
  if (source.startsWith('message:')) return session.bound_platform || source.slice(8) || '外部消息'
  return source
}

function roleLabel(role: string) {
  if (role === 'user') return '用户'
  if (role === 'assistant') return '智能体'
  if (role === 'tool') return '工具'
  if (role === 'system') return '系统'
  return role || '消息'
}

function messageKey(message: HistoryMessage, index: number) {
  return `${message.role}:${index}:${message.content.slice(0, 32)}`
}

export function ReadOnlySessionHistoryDialog({
  user,
  session,
  onClose,
}: {
  user: string
  session: SessionSummary | null
  onClose: () => void
}) {
  if (!session) return null
  return <ReadOnlySessionHistoryDialogContent user={user} session={session} onClose={onClose} />
}

function ReadOnlySessionHistoryDialogContent({
  user,
  session,
  onClose,
}: {
  user: string
  session: SessionSummary
  onClose: () => void
}) {
  const source = session.source || 'web'
  const history = useInfiniteQuery({
    queryKey: ['readonly-history', user, source, session.session_id],
    queryFn: ({ pageParam }) => getHistory(user, session.session_id, {
      source,
      limit: PAGE_SIZE,
      before: pageParam,
    }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled: Boolean(user),
    retry: false,
  })

  if (typeof document === 'undefined') return null
  const pages = history.data?.pages || []
  const messages = [...pages].reverse().flatMap((page) => page.messages || [])
  const memoryStatus = session.memory_status || 'unknown'
  const processed = Math.max(0, session.memory_processed_round || 0)
  const target = Math.max(0, session.memory_target_round || session.rounds || 0)

  return createPortal(
    <div className={styles.layer} onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <section className={styles.dialog} role="dialog" aria-modal="true" aria-label="只读历史归档">
        <header className={styles.header}>
          <span className={styles.icon}><Archive size={19} /></span>
          <span className={styles.heading}>
            <strong>{sessionDisplayName(session)}</strong>
            <small>{sourceLabel(session)} · {session.rounds} 轮 · {formatDateTime(session.updated_at)}</small>
          </span>
          <button type="button" onClick={onClose} aria-label="关闭历史归档"><X size={18} /></button>
        </header>
        <div className={styles.statusGrid}>
          <span><small>来源</small><strong>{sourceLabel(session)}</strong></span>
          <span><small>会话状态</small><strong>{session.state === 'closed' ? '已归档' : session.run_state === 'running' ? '渠道使用中' : '已保存'}</strong></span>
          <span><small>记忆状态</small><strong>{memoryStatus}</strong></span>
          <span><small>记忆进度</small><strong>{processed}/{target}</strong></span>
        </div>
        {session.summary?.trim() && <div className={styles.summary}><small>历史摘要</small><p>{session.summary}</p></div>}
        {session.memory_last_error?.message && <div className={styles.error}>记忆整理失败：{session.memory_last_error.message}</div>}
        <div className={styles.messages}>
          {history.isLoading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={20} />正在读取归档…</div>}
          {history.isError && <div className={`${styles.empty} ${styles.error}`}>历史归档读取失败。</div>}
          {!history.isLoading && !history.isError && history.hasNextPage && <button
            type="button"
            className={styles.loadMore}
            disabled={history.isFetchingNextPage}
            onClick={() => { void history.fetchNextPage() }}
          >
            {history.isFetchingNextPage && <LoaderCircle className={styles.spinning} size={14} />}
            {history.isFetchingNextPage ? '读取中…' : '读取更早记录'}
          </button>}
          {!history.isLoading && !history.isError && messages.map((message, index) => <article
            key={messageKey(message, index)}
            className={`${styles.message} ${styles[`role_${message.role}`] || ''}`}
          >
            <small>{roleLabel(message.role)}</small>
            <pre>{message.content}</pre>
            {message.attachments?.length ? <div className={styles.attachments}>{message.attachments.map((attachment) => <span key={attachment.asset_id || attachment.relative_path}>{attachment.name}</span>)}</div> : null}
          </article>)}
          {!history.isLoading && !history.isError && messages.length === 0 && <div className={styles.empty}>这条归档没有可显示的消息正文。</div>}
        </div>
        <footer>只读查看：网页不会接管、续写或修改来自 CLI 与外部消息渠道的会话。</footer>
      </section>
    </div>,
    document.body,
  )
}
