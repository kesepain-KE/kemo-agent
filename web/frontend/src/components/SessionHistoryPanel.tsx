import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, MouseEvent as ReactMouseEvent } from 'react'
import { createPortal } from 'react-dom'
import {
  History,
  Maximize2,
  MessageSquareText,
  Pencil,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import type { SessionSummary } from '../types/api'
import { formatDateTime } from './ModuleUi'
import styles from './SessionHistoryPanel.module.css'

const COMPACT_SESSION_LIMIT = 8

export interface SessionHistoryPanelProps {
  sessions: SessionSummary[]
  activeSessionId: string
  collapsed?: boolean
  loading?: boolean
  error?: boolean
  onSelectSession: (sessionId: string) => void
  onRenameSession: (sessionId: string, title: string) => Promise<void>
  onDeleteSession: (sessionId: string) => Promise<void>
  onDeleteAllSessions: () => Promise<void>
}

interface ContextMenuState {
  session: SessionSummary
  x: number
  y: number
}

function fallbackSessionLabel(sessionId: string) {
  if (sessionId.startsWith('web_') && sessionId.length > 16) {
    return `Web 会话 · ${sessionId.slice(4, 12)}`
  }
  return sessionId
}

export function sessionDisplayName(session: SessionSummary) {
  return session.title?.trim() || fallbackSessionLabel(session.session_id)
}

function sessionSearchText(session: SessionSummary) {
  return [
    sessionDisplayName(session),
    session.session_id,
    `${session.rounds} 轮`,
    formatDateTime(session.updated_at),
  ].join(' ').toLocaleLowerCase()
}

export function SessionHistoryPanel({
  sessions,
  activeSessionId,
  collapsed = false,
  loading = false,
  error = false,
  onSelectSession,
  onRenameSession,
  onDeleteSession,
  onDeleteAllSessions,
}: SessionHistoryPanelProps) {
  const [expanded, setExpanded] = useState(false)
  const [query, setQuery] = useState('')
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null)
  const [renameTarget, setRenameTarget] = useState<SessionSummary | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<SessionSummary | null>(null)
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<'rename' | 'delete' | 'delete_all' | null>(null)
  const [mutationError, setMutationError] = useState('')
  const contextMenuRef = useRef<HTMLDivElement>(null)

  const compactSessions = sessions.slice(0, COMPACT_SESSION_LIMIT)
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const filteredSessions = useMemo(
    () => normalizedQuery
      ? sessions.filter((session) => sessionSearchText(session).includes(normalizedQuery))
      : sessions,
    [normalizedQuery, sessions],
  )

  useEffect(() => {
    const handlePointerDown = (event: PointerEvent) => {
      if (contextMenuRef.current?.contains(event.target as Node)) return
      setContextMenu(null)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      if (renameTarget) setRenameTarget(null)
      else if (deleteTarget) setDeleteTarget(null)
      else if (deleteAllOpen) setDeleteAllOpen(false)
      else if (contextMenu) setContextMenu(null)
      else if (expanded) setExpanded(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [contextMenu, deleteAllOpen, deleteTarget, expanded, renameTarget])

  useEffect(() => {
    if (!expanded && !renameTarget && !deleteTarget && !deleteAllOpen) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [deleteAllOpen, deleteTarget, expanded, renameTarget])

  useEffect(() => {
    if (sessions.length !== 0) return
    setExpanded(false)
    setQuery('')
    setContextMenu(null)
    setRenameTarget(null)
    setDeleteTarget(null)
    setDeleteAllOpen(false)
  }, [sessions.length])

  const openContextMenu = (event: ReactMouseEvent, session: SessionSummary) => {
    event.preventDefault()
    event.stopPropagation()
    const width = 190
    const height = 108
    setContextMenu({
      session,
      x: Math.max(10, Math.min(event.clientX, window.innerWidth - width - 10)),
      y: Math.max(10, Math.min(event.clientY, window.innerHeight - height - 10)),
    })
  }

  const beginRename = (session: SessionSummary) => {
    setRenameTarget(session)
    setRenameValue(sessionDisplayName(session))
    setMutationError('')
    setContextMenu(null)
  }

  const beginDelete = (session: SessionSummary) => {
    setDeleteTarget(session)
    setMutationError('')
    setContextMenu(null)
  }

  const beginDeleteAll = () => {
    if (loading || error || sessions.length === 0) return
    setMutationError('')
    setDeleteAllOpen(true)
  }

  const submitRename = async (event: FormEvent) => {
    event.preventDefault()
    if (!renameTarget || pendingAction) return
    const title = renameValue.trim()
    if (!title) {
      setMutationError('对话名称不能为空')
      return
    }
    setPendingAction('rename')
    setMutationError('')
    try {
      await onRenameSession(renameTarget.session_id, title)
      setRenameTarget(null)
    } catch (caught) {
      setMutationError(caught instanceof Error ? caught.message : '重命名失败，请稍后重试')
    } finally {
      setPendingAction(null)
    }
  }

  const confirmDelete = async () => {
    if (!deleteTarget || pendingAction) return
    setPendingAction('delete')
    setMutationError('')
    try {
      await onDeleteSession(deleteTarget.session_id)
      if (deleteTarget.session_id === activeSessionId) {
        setExpanded(false)
        setQuery('')
      }
      setDeleteTarget(null)
    } catch (caught) {
      setMutationError(caught instanceof Error ? caught.message : '删除失败，请稍后重试')
    } finally {
      setPendingAction(null)
    }
  }

  const confirmDeleteAll = async () => {
    if (pendingAction || sessions.length === 0) return
    setPendingAction('delete_all')
    setMutationError('')
    try {
      await onDeleteAllSessions()
      setExpanded(false)
      setQuery('')
      setDeleteAllOpen(false)
    } catch (caught) {
      setMutationError(caught instanceof Error ? caught.message : '全部删除失败，请稍后重试')
    } finally {
      setPendingAction(null)
    }
  }

  const selectSession = (sessionId: string) => {
    setExpanded(false)
    setQuery('')
    onSelectSession(sessionId)
  }

  const compactList = (
    <>
      {loading && <span className="sidebar-note">正在加载…</span>}
      {error && <span className="sidebar-note error">会话加载失败</span>}
      {!loading && !error && compactSessions.map((session) => (
        <button
          key={session.session_id}
          type="button"
          className={`recent-btn ${styles.compactItem} ${session.session_id === activeSessionId ? styles.compactActive : ''}`}
          onClick={() => selectSession(session.session_id)}
          onContextMenu={(event) => openContextMenu(event, session)}
          title="打开对话；右键可编辑名称或删除"
        >
          <strong>{sessionDisplayName(session)}</strong>
          <span>{session.rounds} 轮 · {formatDateTime(session.updated_at)}</span>
        </button>
      ))}
      {!loading && !error && sessions.length === 0 && (
        <span className="sidebar-note">暂无 Web 会话</span>
      )}
    </>
  )

  const portal = typeof document === 'undefined' ? null : createPortal(
    <>
      {expanded && (
        <div
          className={styles.historyLayer}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setExpanded(false)
          }}
        >
          <section className={styles.historyDialog} role="dialog" aria-modal="true" aria-label="全部历史对话">
            <header className={styles.dialogHeader}>
              <span className={styles.dialogIcon}><History size={19} /></span>
              <span className={styles.dialogHeading}>
                <strong>全部历史对话</strong>
                <small>共 {sessions.length} 条 · 右键可管理名称或删除</small>
              </span>
              <button type="button" className={styles.iconButton} onClick={() => setExpanded(false)} aria-label="关闭全部历史"><X size={18} /></button>
            </header>
            <label className={styles.searchBox}>
              <Search size={17} />
              <input
                autoFocus
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索对话名称、会话 ID 或时间…"
                aria-label="搜索历史对话"
              />
              {query && <button type="button" onClick={() => setQuery('')} aria-label="清空搜索"><X size={15} /></button>}
            </label>
            <div className={styles.historyResults}>
              {filteredSessions.map((session) => (
                <button
                  key={session.session_id}
                  type="button"
                  className={`${styles.historyItem} ${session.session_id === activeSessionId ? styles.historyItemActive : ''}`}
                  onClick={() => selectSession(session.session_id)}
                  onContextMenu={(event) => openContextMenu(event, session)}
                  title="打开对话；右键可编辑名称或删除"
                >
                  <span className={styles.itemIcon}><MessageSquareText size={17} /></span>
                  <span className={styles.itemCopy}>
                    <strong>{sessionDisplayName(session)}</strong>
                    <small>{session.session_id}</small>
                  </span>
                  <span className={styles.itemMeta}>
                    <strong>{session.rounds} 轮</strong>
                    <small>{formatDateTime(session.updated_at)}</small>
                  </span>
                </button>
              ))}
              {filteredSessions.length === 0 && (
                <div className={styles.emptyResult}>
                  <Search size={21} />
                  <strong>没有匹配的历史对话</strong>
                  <span>换一个名称、会话 ID 或时间关键词试试</span>
                </div>
              )}
            </div>
          </section>
        </div>
      )}

      {contextMenu && (
        <div
          ref={contextMenuRef}
          className={styles.contextMenu}
          role="menu"
          aria-label={`${sessionDisplayName(contextMenu.session)} 操作`}
          style={{ left: contextMenu.x, top: contextMenu.y }}
        >
          <button type="button" role="menuitem" onClick={() => beginRename(contextMenu.session)}><Pencil size={15} /><span>编辑名字</span></button>
          <button type="button" role="menuitem" className={styles.dangerMenuItem} onClick={() => beginDelete(contextMenu.session)}><Trash2 size={15} /><span>删除</span></button>
        </div>
      )}

      {renameTarget && (
        <div className={styles.confirmLayer} onMouseDown={(event) => { if (event.target === event.currentTarget && !pendingAction) setRenameTarget(null) }}>
          <form className={styles.confirmDialog} role="dialog" aria-modal="true" aria-label="编辑对话名字" onSubmit={submitRename}>
            <div className={styles.confirmIcon}><Pencil size={18} /></div>
            <div className={styles.confirmHeading}><strong>编辑对话名字</strong><span>新名称会保存到该用户的历史记录中。</span></div>
            <label className={styles.renameField} htmlFor="session-history-title">
              <span>对话名称</span>
              <input id="session-history-title" aria-label="对话名称" autoFocus maxLength={80} value={renameValue} onChange={(event) => setRenameValue(event.target.value)} />
              <small>{renameValue.trim().length}/80</small>
            </label>
            {mutationError && <div className={styles.dialogError}>{mutationError}</div>}
            <div className={styles.confirmActions}>
              <button type="button" onClick={() => setRenameTarget(null)} disabled={Boolean(pendingAction)}>取消</button>
              <button type="submit" className={styles.primaryAction} disabled={!renameValue.trim() || Boolean(pendingAction)}>{pendingAction === 'rename' ? '正在保存…' : '保存名称'}</button>
            </div>
          </form>
        </div>
      )}

      {deleteTarget && (
        <div className={styles.confirmLayer} onMouseDown={(event) => { if (event.target === event.currentTarget && !pendingAction) setDeleteTarget(null) }}>
          <section className={styles.confirmDialog} role="alertdialog" aria-modal="true" aria-label="删除历史对话">
            <div className={`${styles.confirmIcon} ${styles.deleteIcon}`}><Trash2 size={18} /></div>
            <div className={styles.confirmHeading}>
              <strong>删除这条历史对话？</strong>
              <span>“{sessionDisplayName(deleteTarget)}”及其完整历史将被永久删除，此操作无法撤销。</span>
            </div>
            {mutationError && <div className={styles.dialogError}>{mutationError}</div>}
            <div className={styles.confirmActions}>
              <button type="button" onClick={() => setDeleteTarget(null)} disabled={Boolean(pendingAction)}>取消</button>
              <button type="button" className={styles.deleteAction} onClick={() => { void confirmDelete() }} disabled={Boolean(pendingAction)}>{pendingAction === 'delete' ? '正在删除…' : '确认删除'}</button>
            </div>
          </section>
        </div>
      )}

      {deleteAllOpen && (
        <div className={styles.confirmLayer} onMouseDown={(event) => { if (event.target === event.currentTarget && !pendingAction) setDeleteAllOpen(false) }}>
          <section className={styles.confirmDialog} role="alertdialog" aria-modal="true" aria-label="删除全部历史对话">
            <div className={`${styles.confirmIcon} ${styles.deleteIcon}`}><Trash2 size={18} /></div>
            <div className={styles.confirmHeading}>
              <strong>删除全部历史对话？</strong>
              <span>当前用户的 {sessions.length} 条 Web 历史对话及其完整内容将被永久删除，此操作无法撤销。</span>
            </div>
            {mutationError && <div className={styles.dialogError}>{mutationError}</div>}
            <div className={styles.confirmActions}>
              <button type="button" onClick={() => setDeleteAllOpen(false)} disabled={Boolean(pendingAction)}>取消</button>
              <button type="button" className={styles.deleteAction} onClick={() => { void confirmDeleteAll() }} disabled={Boolean(pendingAction)}>{pendingAction === 'delete_all' ? '正在删除…' : '确认全部删除'}</button>
            </div>
          </section>
        </div>
      )}
    </>,
    document.body,
  )

  return (
    <section className="recent-block">
      <div className="recent-title">最近对话</div>
      {!collapsed && (
        <button
          type="button"
          className={styles.deleteAllButton}
          onClick={beginDeleteAll}
          disabled={loading || error || sessions.length === 0}
          title={sessions.length === 0 ? '没有可删除的 Web 会话' : `删除全部 ${sessions.length} 条 Web 会话`}
        >
          <Trash2 size={14} />
          <span>全部删除</span>
          {sessions.length > 0 && <small>{sessions.length}</small>}
        </button>
      )}
      <div className="recent-list">{compactList}</div>
      {sessions.length > COMPACT_SESSION_LIMIT && (
        <button type="button" className={styles.expandButton} onClick={() => setExpanded(true)}>
          <Maximize2 size={14} />
          <span>展开全部</span>
          <small>{sessions.length}</small>
        </button>
      )}
      {portal}
    </section>
  )
}
