import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertTriangle, History, LoaderCircle, MessageSquareText, RotateCcw, Search, Trash2, X } from 'lucide-react'
import type { SessionSummary } from '../types/api'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'
import { formatDateTime } from './ModuleUi'
import { sessionDisplayName } from './SessionHistoryPanel'
import styles from './HistorySearchDrawer.module.css'

export interface HistorySearchDrawerProps {
  open: boolean
  sessions: SessionSummary[]
  activeSessionId: string
  loading?: boolean
  error?: boolean
  chatRunning?: boolean
  switchingSessionId?: string
  actionError?: string
  onClose: () => void
  onSelectSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => Promise<void> | void
  onDeleteAllSessions: () => Promise<void> | void
  onRetrySummary: (sessionId: string) => Promise<void> | void
}

interface SummaryPreview {
  text: string
  top: number
  left: number
  width: number
  placement: 'above' | 'below'
}

export function HistorySearchDrawer({
  open,
  sessions,
  activeSessionId,
  loading = false,
  error = false,
  chatRunning = false,
  switchingSessionId = '',
  actionError = '',
  onClose,
  onSelectSession,
  onDeleteSession,
  onDeleteAllSessions,
  onRetrySummary,
}: HistorySearchDrawerProps) {
  const [query, setQuery] = useState('')
  const [pendingSessionId, setPendingSessionId] = useState('')
  const [deleteSessionId, setDeleteSessionId] = useState('')
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<'delete' | 'delete_all' | ''>('')
  const [retryingSessionId, setRetryingSessionId] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [summaryPreview, setSummaryPreview] = useState<SummaryPreview | null>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredSessions = useMemo(
    () => normalizedQuery
      ? sessions.filter((session) => [
          sessionDisplayName(session),
          session.summary || '',
          session.session_id,
        ].join(' ').toLocaleLowerCase().includes(normalizedQuery))
      : sessions,
    [normalizedQuery, sessions],
  )

  useEffect(() => {
    if (open) requestAnimationFrame(() => searchRef.current?.focus())
    else {
      setQuery('')
      setPendingSessionId('')
      setDeleteSessionId('')
      setDeleteAllOpen(false)
      setPendingAction('')
      setRetryingSessionId('')
      setMutationError('')
      setSummaryPreview(null)
    }
  }, [open])

  useEffect(() => {
    setSummaryPreview(null)
  }, [normalizedQuery, sessions])

  const pendingSession = sessions.find((session) => session.session_id === pendingSessionId)
  const deleteTarget = sessions.find((session) => session.session_id === deleteSessionId)
  const closeDrawer = () => {
    setSummaryPreview(null)
    setPendingSessionId('')
    setDeleteSessionId('')
    setDeleteAllOpen(false)
    onClose()
  }
  const confirmSwitch = () => {
    if (!pendingSessionId || chatRunning || switchingSessionId) return
    const targetSessionId = pendingSessionId
    setPendingSessionId('')
    onSelectSession(targetSessionId)
  }
  const beginDelete = (sessionId: string) => {
    setPendingSessionId('')
    setDeleteAllOpen(false)
    setMutationError('')
    setDeleteSessionId(sessionId)
  }
  const confirmDelete = async () => {
    if (!deleteSessionId || pendingAction) return
    setPendingAction('delete')
    setMutationError('')
    try {
      await onDeleteSession(deleteSessionId)
      setDeleteSessionId('')
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '删除历史对话失败')
    } finally {
      setPendingAction('')
    }
  }
  const confirmDeleteAll = async () => {
    if (sessions.length === 0 || pendingAction) return
    setPendingAction('delete_all')
    setMutationError('')
    try {
      await onDeleteAllSessions()
      setDeleteAllOpen(false)
      setQuery('')
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '删除全部历史对话失败')
    } finally {
      setPendingAction('')
    }
  }
  const retrySummary = async (sessionId: string) => {
    if (retryingSessionId) return
    setRetryingSessionId(sessionId)
    setMutationError('')
    try {
      await onRetrySummary(sessionId)
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : '重新生成历史摘要失败')
    } finally {
      setRetryingSessionId('')
    }
  }
  const showSummaryPreview = (text: string, element: HTMLElement) => {
    const rect = element.getBoundingClientRect()
    const viewportWidth = window.innerWidth
    const width = Math.min(420, Math.max(240, viewportWidth - 24))
    const halfWidth = width / 2
    const left = Math.min(
      viewportWidth - halfWidth - 12,
      Math.max(halfWidth + 12, rect.left + rect.width / 2),
    )
    const placement = rect.top >= 180 ? 'above' : 'below'
    setSummaryPreview({
      text,
      top: placement === 'above' ? rect.top - 8 : rect.bottom + 8,
      left,
      width,
      placement,
    })
  }

  return <>
    <aside className={`drawer ${styles.drawer} ${open ? 'show' : ''}`} inert={!open} role="dialog" aria-modal="true" aria-label="历史对话" aria-hidden={!open}>
      {open && <>
      <div className="drawer-head">
        <div className="context-drawer-heading">
          <strong>历史对话</strong>
          <span>{sessions.length} 条已归档对话 · 点击卡片确认后打开</span>
        </div>
        <button className="icon-btn" type="button" onClick={closeDrawer} aria-label="关闭历史对话"><X size={17} /></button>
      </div>
      <div className={`drawer-body ${styles.body}`}>
        <label className={styles.searchBox}>
          <Search size={17} />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索历史对话名称…"
            aria-label="搜索历史对话名称"
          />
          {query && <button type="button" onClick={() => setQuery('')} aria-label="清空历史搜索"><X size={15} /></button>}
        </label>

        <div className={styles.actionBar}>
          <span>{sessions.length} 条历史对话</span>
          <button
            type="button"
            disabled={loading || error || sessions.length === 0 || chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction)}
            onClick={() => {
              setPendingSessionId('')
              setDeleteSessionId('')
              setMutationError('')
              setDeleteAllOpen(true)
            }}
          >
            <Trash2 size={14} />
            <span>全部删除</span>
          </button>
        </div>

        {chatRunning && <div className={styles.notice}>当前对话正在运行，结束或停止后才能切换历史对话。</div>}
        {switchingSessionId && <div className={styles.progressNotice}><LoaderCircle size={15} />正在保存当前对话并切换…</div>}
        {actionError && <div className={styles.errorNotice}>{actionError}</div>}
        {mutationError && !deleteTarget && !deleteAllOpen && <div className={styles.errorNotice}>{mutationError}</div>}

        <div className={styles.results} onScroll={() => setSummaryPreview(null)}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>正在加载历史对话</strong></div>}
          {error && !loading && <div className={styles.empty}><History size={22} /><strong>历史对话加载失败</strong><span>关闭后重新打开即可再次加载。</span></div>}
          {!loading && !error && filteredSessions.map((session) => {
            const active = session.session_id === activeSessionId
            const switching = session.session_id === switchingSessionId
            const displayName = sessionDisplayName(session)
            const summaryStatus = session.summary_status || ''
            const canRetrySummary = ['failed', 'retry_wait', 'exhausted'].includes(summaryStatus)
            const attempt = Math.max(0, session.summary_attempt_count || session.summary_retry_count || 0)
            const maxAttempts = Math.max(1, session.summary_max_attempts || 5)
            const completedChunks = Math.max(0, session.summary_checkpoint_next_chunk || 0)
            const totalChunks = Math.max(0, session.summary_checkpoint_total_chunks || 0)
            const retryAt = session.summary_retry_at ? formatDateTime(session.summary_retry_at) : ''
            return <article
              key={session.session_id}
              className={`${styles.sessionCard} ${active ? styles.activeCard : ''}`}
            >
              <button
                type="button"
                className={styles.sessionSelect}
                disabled={chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction)}
                aria-label={`打开对话 ${displayName}`}
                onFocus={(event) => {
                  if (session.summary?.trim()) showSummaryPreview(session.summary, event.currentTarget)
                }}
                onBlur={() => setSummaryPreview(null)}
                onClick={() => {
                  setSummaryPreview(null)
                  if (!active) setPendingSessionId(session.session_id)
                }}
              >
                <span className={styles.cardIcon}>{switching ? <LoaderCircle className={styles.spinning} size={18} /> : <MessageSquareText size={18} />}</span>
                <span className={styles.cardCopy}>
                  <strong>{displayName}</strong>
                  {['queued', 'processing'].includes(summaryStatus)
                    ? <span>正在生成摘要{totalChunks > 1 ? ` · ${Math.min(completedChunks, totalChunks)}/${totalChunks}` : ''}…</span>
                    : summaryStatus === 'exhausted'
                      ? <span className={styles.summaryFailed}>摘要生成失败 · 已停止自动重试</span>
                      : ['failed', 'retry_wait'].includes(summaryStatus)
                        ? <span className={styles.summaryFailed}>摘要生成失败 · 第 {attempt}/{maxAttempts} 次{retryAt ? ` · ${retryAt} 自动重试` : ' · 等待自动重试'}</span>
                        : session.summary?.trim()
                          ? <span
                              className={styles.summaryText}
                              onMouseEnter={(event) => showSummaryPreview(session.summary || '', event.currentTarget)}
                              onMouseLeave={() => setSummaryPreview(null)}
                            >{session.summary}</span>
                          : null}
                  <small>{session.rounds} 轮 · {formatDateTime(session.updated_at)}</small>
                </span>
                <span className={`${styles.stateBadge} ${active ? styles.activeBadge : ''}`}>
                  {active ? '当前对话' : session.state === 'closed' ? '已保存' : '历史对话'}
                </span>
              </button>
              <span className={styles.cardActions}>
                {canRetrySummary && <button
                  type="button"
                  className={styles.retryButton}
                  aria-label={`重新生成摘要 ${displayName}`}
                  title="立即重新生成摘要"
                  disabled={Boolean(retryingSessionId) || Boolean(pendingAction)}
                  onClick={() => { void retrySummary(session.session_id) }}
                >
                  <RotateCcw className={retryingSessionId === session.session_id ? styles.spinning : ''} size={15} />
                </button>}
                <button
                  type="button"
                  className={styles.deleteButton}
                  aria-label={`删除对话 ${displayName}`}
                  title={`删除 ${displayName}`}
                  disabled={Boolean(switchingSessionId) || Boolean(pendingAction) || Boolean(retryingSessionId) || (chatRunning && active)}
                  onClick={() => beginDelete(session.session_id)}
                >
                  <Trash2 size={15} />
                </button>
              </span>
            </article>
          })}
          {!loading && !error && filteredSessions.length === 0 && <div className={styles.empty}>
            <Search size={22} />
            <strong>{sessions.length === 0 ? '暂无历史对话' : '没有匹配的对话'}</strong>
            <span>{sessions.length === 0 ? '完成第一轮对话后会在这里生成历史记录。' : '请尝试输入其他对话名称。'}</span>
          </div>}
        </div>
      </div>
      </>}
    </aside>
    {open && <button className="drawer-backdrop" aria-label="关闭历史对话" onClick={closeDrawer} />}
    {open && summaryPreview ? createPortal(
      <span
        className={`${styles.summaryTooltip} ${summaryPreview.placement === 'above' ? styles.tooltipAbove : styles.tooltipBelow}`}
        role="tooltip"
        style={{ top: summaryPreview.top, left: summaryPreview.left, width: summaryPreview.width }}
      >
        <small>历史摘要</small>
        <strong>{summaryPreview.text}</strong>
      </span>,
      document.body,
    ) : null}
    <GlobalConfirmDialog
      open={Boolean(pendingSession)}
      title="确认切换历史对话？"
      detail={pendingSession ? sessionDisplayName(pendingSession) : ''}
      description="切换前会保存当前对话。智能体运行期间切换可能造成状态异常，请确认当前运行已经结束。"
      icon={<AlertTriangle size={22} />}
      confirmLabel="确认切换"
      onCancel={() => setPendingSessionId('')}
      onConfirm={confirmSwitch}
    />
    <GlobalConfirmDialog
      open={Boolean(deleteTarget)}
      title="确认删除这条历史对话？"
      detail={deleteTarget ? sessionDisplayName(deleteTarget) : ''}
      description="该对话及其完整历史将被永久删除，此操作无法撤销。"
      error={mutationError}
      icon={<Trash2 size={21} />}
      tone="danger"
      confirmLabel="确认删除"
      pendingLabel="正在删除…"
      pending={pendingAction === 'delete'}
      onCancel={() => { setDeleteSessionId(''); setMutationError('') }}
      onConfirm={() => { void confirmDelete() }}
    />
    <GlobalConfirmDialog
      open={deleteAllOpen}
      title="确认删除全部历史对话？"
      detail={`共 ${sessions.length} 条历史对话`}
      description="当前用户的全部 Web 历史对话及其完整内容将被永久删除，此操作无法撤销。"
      error={mutationError}
      icon={<Trash2 size={21} />}
      tone="danger"
      confirmLabel="确认全部删除"
      pendingLabel="正在全部删除…"
      pending={pendingAction === 'delete_all'}
      onCancel={() => { setDeleteAllOpen(false); setMutationError('') }}
      onConfirm={() => { void confirmDeleteAll() }}
    />
  </>
}
