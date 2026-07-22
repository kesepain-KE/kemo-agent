import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, History, LoaderCircle, MessageSquareText, Search, Trash2, X } from 'lucide-react'
import type { SessionSummary } from '../types/api'
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
}: HistorySearchDrawerProps) {
  const [query, setQuery] = useState('')
  const [pendingSessionId, setPendingSessionId] = useState('')
  const [deleteSessionId, setDeleteSessionId] = useState('')
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<'delete' | 'delete_all' | ''>('')
  const [mutationError, setMutationError] = useState('')
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
      setMutationError('')
    }
  }, [open])

  const pendingSession = sessions.find((session) => session.session_id === pendingSessionId)
  const deleteTarget = sessions.find((session) => session.session_id === deleteSessionId)
  const closeDrawer = () => {
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

        <div className={styles.results}>
          {loading && <div className={styles.empty}><LoaderCircle className={styles.spinning} size={22} /><strong>正在加载历史对话</strong></div>}
          {error && !loading && <div className={styles.empty}><History size={22} /><strong>历史对话加载失败</strong><span>关闭后重新打开即可再次加载。</span></div>}
          {!loading && !error && filteredSessions.map((session) => {
            const active = session.session_id === activeSessionId
            const switching = session.session_id === switchingSessionId
            const displayName = sessionDisplayName(session)
            return <article
              key={session.session_id}
              className={`${styles.sessionCard} ${active ? styles.activeCard : ''}`}
            >
              <button
                type="button"
                className={styles.sessionSelect}
                disabled={chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction)}
                aria-label={`打开对话 ${displayName}`}
                onClick={() => {
                  if (!active) setPendingSessionId(session.session_id)
                }}
              >
                <span className={styles.cardIcon}>{switching ? <LoaderCircle className={styles.spinning} size={18} /> : <MessageSquareText size={18} />}</span>
                <span className={styles.cardCopy}>
                  <strong>{displayName}</strong>
                  {session.summary?.trim()
                    ? <span>{session.summary}</span>
                    : ['queued', 'processing'].includes(session.summary_status || '')
                      ? <span>正在生成摘要…</span>
                      : session.summary_status === 'failed'
                        ? <span className={styles.summaryFailed}>摘要生成失败，后台将自动重试</span>
                      : null}
                  <small>{session.rounds} 轮 · {formatDateTime(session.updated_at)}</small>
                </span>
                <span className={`${styles.stateBadge} ${active ? styles.activeBadge : ''}`}>
                  {active ? '当前对话' : session.state === 'closed' ? '已保存' : '历史对话'}
                </span>
              </button>
              <button
                type="button"
                className={styles.deleteButton}
                aria-label={`删除对话 ${displayName}`}
                title={`删除 ${displayName}`}
                disabled={Boolean(switchingSessionId) || Boolean(pendingAction) || (chatRunning && active)}
                onClick={() => beginDelete(session.session_id)}
              >
                <Trash2 size={15} />
              </button>
            </article>
          })}
          {!loading && !error && filteredSessions.length === 0 && <div className={styles.empty}>
            <Search size={22} />
            <strong>{sessions.length === 0 ? '暂无历史对话' : '没有匹配的对话'}</strong>
            <span>{sessions.length === 0 ? '完成第一轮对话后会在这里生成历史记录。' : '请尝试输入其他对话名称。'}</span>
          </div>}
        </div>
      </div>
      {pendingSession && <div className={styles.confirmLayer}>
        <section className={styles.confirmDialog} role="alertdialog" aria-modal="true" aria-labelledby="history-switch-title" aria-describedby="history-switch-description">
          <span className={styles.confirmIcon}><AlertTriangle size={22} /></span>
          <div className={styles.confirmCopy}>
            <strong id="history-switch-title">确认切换历史对话？</strong>
            <span className={styles.confirmTarget}>{sessionDisplayName(pendingSession)}</span>
            <p id="history-switch-description">切换前会保存当前对话。智能体运行期间切换可能造成状态异常，请确认当前运行已经结束。</p>
          </div>
          <div className={styles.confirmActions}>
            <button type="button" onClick={() => setPendingSessionId('')}>取消</button>
            <button type="button" className={styles.confirmButton} onClick={confirmSwitch}>确认切换</button>
          </div>
        </section>
      </div>}
      {deleteTarget && <div className={styles.confirmLayer}>
        <section className={styles.confirmDialog} role="alertdialog" aria-modal="true" aria-labelledby="history-delete-title" aria-describedby="history-delete-description">
          <span className={`${styles.confirmIcon} ${styles.deleteConfirmIcon}`}><Trash2 size={21} /></span>
          <div className={styles.confirmCopy}>
            <strong id="history-delete-title">确认删除这条历史对话？</strong>
            <span className={styles.confirmTarget}>{sessionDisplayName(deleteTarget)}</span>
            <p id="history-delete-description">该对话及其完整历史将被永久删除，此操作无法撤销。</p>
            {mutationError && <p className={styles.mutationError}>{mutationError}</p>}
          </div>
          <div className={styles.confirmActions}>
            <button type="button" disabled={Boolean(pendingAction)} onClick={() => setDeleteSessionId('')}>取消</button>
            <button type="button" className={styles.deleteConfirmButton} disabled={Boolean(pendingAction)} onClick={() => { void confirmDelete() }}>{pendingAction === 'delete' ? '正在删除…' : '确认删除'}</button>
          </div>
        </section>
      </div>}
      {deleteAllOpen && <div className={styles.confirmLayer}>
        <section className={styles.confirmDialog} role="alertdialog" aria-modal="true" aria-labelledby="history-delete-all-title" aria-describedby="history-delete-all-description">
          <span className={`${styles.confirmIcon} ${styles.deleteConfirmIcon}`}><Trash2 size={21} /></span>
          <div className={styles.confirmCopy}>
            <strong id="history-delete-all-title">确认删除全部历史对话？</strong>
            <span className={styles.confirmTarget}>共 {sessions.length} 条历史对话</span>
            <p id="history-delete-all-description">当前用户的全部 Web 历史对话及其完整内容将被永久删除，此操作无法撤销。</p>
            {mutationError && <p className={styles.mutationError}>{mutationError}</p>}
          </div>
          <div className={styles.confirmActions}>
            <button type="button" disabled={Boolean(pendingAction)} onClick={() => setDeleteAllOpen(false)}>取消</button>
            <button type="button" className={styles.deleteConfirmButton} disabled={Boolean(pendingAction)} onClick={() => { void confirmDeleteAll() }}>{pendingAction === 'delete_all' ? '正在全部删除…' : '确认全部删除'}</button>
          </div>
        </section>
      </div>}
      </>}
    </aside>
    {open && <button className="drawer-backdrop" aria-label="关闭历史对话" onClick={closeDrawer} />}
  </>
}
