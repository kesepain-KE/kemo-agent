import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import {
  AlertTriangle,
  CalendarDays,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  History,
  LoaderCircle,
  MessageSquareText,
  RotateCcw,
  Search,
  Star,
  Pin,
  Trash2,
  X,
} from 'lucide-react'
import type { SessionSummary } from '../types/api'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'
import { formatDateTime } from './ModuleUi'
import { ReadOnlySessionHistoryDialog } from './ReadOnlySessionHistoryDialog'
import { sessionDisplayName } from '../utils/sessionDisplayName'
import { t, useLanguage } from '../i18n'
import { SessionActions, type SessionActionCallbacks } from './SessionActions'
import styles from './HistorySearchDrawer.module.css'

export interface HistorySearchDrawerProps {
  favoritesOnly?: boolean
  favoritesCount?: number
  onFavoritesChange?: (value: boolean) => void
  onSearchChange?: (value: string) => void
  onMarkSession?: SessionActionCallbacks['onMark']
  onRenameSession?: SessionActionCallbacks['onRename']
  user?: string
  open: boolean
  sessions: SessionSummary[]
  activeSessionId: string
  loading?: boolean
  loadingMore?: boolean
  hasMore?: boolean
  error?: boolean
  chatRunning?: boolean
  switchingSessionId?: string
  actionError?: string
  selectedDate?: string
  onClose: () => void
  onDateChange?: (date: string) => void
  onSelectSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => Promise<void> | void
  onDeleteAllSessions: () => Promise<void> | void
  onRetrySummary: (sessionId: string) => Promise<void> | void
  onLoadMore?: () => Promise<void> | void
}

interface SummaryPreview {
  text: string
  top: number
  left: number
  width: number
  placement: 'above' | 'below'
}

const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日']

function padDatePart(value: number): string {
  return String(value).padStart(2, '0')
}

function dateValue(year: number, month: number, day: number): string {
  return `${year}-${padDatePart(month + 1)}-${padDatePart(day)}`
}

function shanghaiDateValue(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(now)
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

function monthFromDate(value: string): { year: number; month: number } {
  const [year, month] = value.split('-').map(Number)
  return {
    year: Number.isFinite(year) ? year : 1970,
    month: Number.isFinite(month) ? month - 1 : 0,
  }
}

function archiveDateLabel(value: string): string {
  if (!value) return ''
  const [year, month, day] = value.split('-').map(Number)
  return `${year}年${month}月${day}日`
}

function monthGrid(year: number, month: number) {
  const firstWeekday = (new Date(Date.UTC(year, month, 1)).getUTCDay() + 6) % 7
  return Array.from({ length: 42 }, (_, index) => {
    const date = new Date(Date.UTC(year, month, index - firstWeekday + 1))
    const itemYear = date.getUTCFullYear()
    const itemMonth = date.getUTCMonth()
    const day = date.getUTCDate()
    return {
      year: itemYear,
      month: itemMonth,
      day,
      value: dateValue(itemYear, itemMonth, day),
      inMonth: itemMonth === month,
    }
  })
}

export function HistorySearchDrawer({
  user = '',
  favoritesOnly = false, favoritesCount, onFavoritesChange, onSearchChange, onMarkSession, onRenameSession,
  open,
  sessions,
  activeSessionId,
  loading = false,
  loadingMore = false,
  hasMore = false,
  error = false,
  chatRunning = false,
  switchingSessionId = '',
  actionError = '',
  selectedDate = '',
  onClose,
  onDateChange = () => undefined,
  onSelectSession,
  onDeleteSession,
  onDeleteAllSessions,
  onRetrySummary,
  onLoadMore = () => undefined,
}: HistorySearchDrawerProps) {
  useLanguage()
  const [query, setQuery] = useState('')
  const [pendingSessionId, setPendingSessionId] = useState('')
  const [deleteSessionId, setDeleteSessionId] = useState('')
  const [deleteAllOpen, setDeleteAllOpen] = useState(false)
  const [pendingAction, setPendingAction] = useState<'delete' | 'delete_all' | ''>('')
  const [retryingSessionId, setRetryingSessionId] = useState('')
  const [mutationError, setMutationError] = useState('')
  const [summaryPreview, setSummaryPreview] = useState<SummaryPreview | null>(null)
  const [readOnlySession, setReadOnlySession] = useState<SessionSummary | null>(null)
  const [calendarOpen, setCalendarOpen] = useState(false)
  const todayDate = shanghaiDateValue()
  const [visibleMonth, setVisibleMonth] = useState(() => monthFromDate(selectedDate || todayDate))
  const searchRef = useRef<HTMLInputElement>(null)
  const drawerRef = useRef<HTMLElement>(null)
  const dateToggleRef = useRef<HTMLButtonElement>(null)
  const calendarRef = useRef<HTMLDivElement>(null)
  const backdropRef = useRef<HTMLButtonElement>(null)
  const calendarId = useId()
  const [calendarPosition, setCalendarPosition] = useState({ left: 12, top: 12, width: 300 })
  const normalizedQuery = query.trim().toLocaleLowerCase()
  const orderedSessions = useMemo(
    () => [...sessions].sort((left, right) => {
      const pinOrder = (right.pinned_at || '').localeCompare(left.pinned_at || '')
      if (pinOrder) return pinOrder
      const leftTime = Date.parse(left.updated_at) || 0
      const rightTime = Date.parse(right.updated_at) || 0
      if (leftTime !== rightTime) return rightTime - leftTime
      const sessionIdOrder = right.session_id.localeCompare(left.session_id)
      if (sessionIdOrder) return sessionIdOrder
      return (right.source || 'web').localeCompare(left.source || 'web')
    }),
    [sessions],
  )
  const filteredSessions = useMemo(
    () => normalizedQuery && !onSearchChange
      ? orderedSessions.filter((session) => [
          sessionDisplayName(session),
          session.summary || '',
          session.session_id,
          session.source || 'web',
          session.bound_platform || '',
        ].join(' ').toLocaleLowerCase().includes(normalizedQuery))
      : orderedSessions,
    [normalizedQuery, orderedSessions, onSearchChange],
  )
  const calendarDays = useMemo(
    () => monthGrid(visibleMonth.year, visibleMonth.month),
    [visibleMonth],
  )
  const selectedDateText = archiveDateLabel(selectedDate)

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
      setReadOnlySession(null)
      setCalendarOpen(false)
    }
  }, [open])

  useEffect(() => {
    if (selectedDate) setVisibleMonth(monthFromDate(selectedDate))
  }, [selectedDate])

  useLayoutEffect(() => {
    if (!open || !calendarOpen) return
    // 浮层脱离抽屉的裁切容器；以抽屉最终位置为锚点，不跟随进场动画横移。
    const updatePosition = () => {
      const trigger = dateToggleRef.current?.getBoundingClientRect()
      const drawer = drawerRef.current?.getBoundingClientRect()
      const calendar = calendarRef.current
      if (!trigger || !drawer || !calendar) return
      const margin = 12
      const gap = 10
      // 原半宽面板整体放大 50%，窄屏仍限制在视口内。
      const width = Math.min((trigger.width || 400) * .75, window.innerWidth - margin * 2)
      const drawerLeft = window.innerWidth - drawer.width
      const besideLeft = drawerLeft - gap - width
      const left = besideLeft >= margin
        ? besideLeft
        : Math.max(margin, Math.min(trigger.left, window.innerWidth - width - margin))
      const top = Math.max(margin, Math.min(trigger.top, window.innerHeight - calendar.offsetHeight - margin))
      setCalendarPosition((current) => current.left === left && current.top === top && current.width === width
        ? current : { left, top, width })
    }
    updatePosition()
    calendarRef.current?.querySelector<HTMLButtonElement>(
      '[aria-pressed="true"]:not(:disabled), [data-today="true"]:not(:disabled)',
    )?.focus()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updatePosition)
    for (const element of [drawerRef.current, dateToggleRef.current, calendarRef.current]) {
      if (element) observer?.observe(element)
    }
    const frame = requestAnimationFrame(updatePosition)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, calendarOpen])

  useEffect(() => {
    if (!open || !calendarOpen) return
    const dismissOnPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      // 遮罩点击单独处理，避免收起日历后同一次点击又关闭整个历史抽屉。
      if (!calendarRef.current?.contains(target) && !dateToggleRef.current?.contains(target)
        && !backdropRef.current?.contains(target)) setCalendarOpen(false)
    }
    const dismissOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setCalendarOpen(false)
      dateToggleRef.current?.focus()
    }
    document.addEventListener('pointerdown', dismissOnPointerDown)
    document.addEventListener('keydown', dismissOnEscape, true)
    return () => {
      document.removeEventListener('pointerdown', dismissOnPointerDown)
      document.removeEventListener('keydown', dismissOnEscape, true)
    }
  }, [open, calendarOpen])

  useEffect(() => {
    setSummaryPreview(null)
  }, [normalizedQuery, sessions])

  const pendingSession = sessions.find((session) => (session.source || 'web') === 'web' && session.session_id === pendingSessionId)
  const deleteTarget = sessions.find((session) => (session.source || 'web') === 'web' && session.session_id === deleteSessionId)
  const webSessions = sessions.filter((session) => (session.source || 'web') === 'web')
  const appSessions = sessions.filter((session) => session.source === 'app')
  const closeDrawer = () => {
    setSummaryPreview(null)
    setPendingSessionId('')
    setDeleteSessionId('')
    setDeleteAllOpen(false)
    setCalendarOpen(false)
    onClose()
  }
  const moveCalendarMonth = (offset: number) => {
    setVisibleMonth((current) => {
      const next = new Date(Date.UTC(current.year, current.month + offset, 1))
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() }
    })
  }
  const chooseArchiveDate = (value: string) => {
    if (value > todayDate) return
    onDateChange(value)
    setCalendarOpen(false)
    dateToggleRef.current?.focus()
  }
  const clearArchiveDate = () => {
    onDateChange('')
    setVisibleMonth(monthFromDate(todayDate))
    setCalendarOpen(false)
    dateToggleRef.current?.focus()
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
    if (pendingAction) return
    setPendingAction('delete_all')
    setMutationError('')
    try {
      await onDeleteAllSessions()
      setReadOnlySession(null)
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
    <aside ref={drawerRef} className={`drawer ${styles.drawer} ${open ? 'show' : ''}`} inert={!open} role="dialog" aria-modal="true" aria-label="历史对话" aria-hidden={!open}>
      {open && <>
      <div className="drawer-head">
        <div className="context-drawer-heading">
          <strong>历史对话</strong>
          <span>{sessions.length} 条跨渠道对话 · Web 可切换，定时任务与其他渠道只读查看</span>
        </div>
        <button className="icon-btn" type="button" onClick={closeDrawer} aria-label="关闭历史对话"><X size={17} /></button>
      </div>
      <div className={`drawer-body ${styles.body}`}>
        <form className={styles.searchBox} data-has-query={Boolean(query)} onSubmit={(event) => { event.preventDefault(); onSearchChange?.(query.trim()) }}>
          <Search size={17} aria-hidden="true" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索历史对话名称…"
            aria-label="搜索历史对话名称"
          />
          <button type="button" className={styles.searchClear}
            disabled={!query} aria-hidden={!query} tabIndex={query ? 0 : -1}
            onClick={() => { setQuery(''); onSearchChange?.(''); searchRef.current?.focus() }}
            aria-label="清空历史搜索"><X size={15} /></button>
          {onSearchChange && <button type="submit" className={styles.searchSubmit} aria-label={t('搜索历史对话')}><Search size={17} /></button>}
        </form>

        <section className={styles.dateFilter} aria-label="历史归档日期筛选">
          <button
            ref={dateToggleRef}
            type="button"
            className={`${styles.dateFilterToggle} ${selectedDate ? styles.dateFilterActive : ''}`}
            aria-label="按日期筛选历史归档"
            aria-expanded={calendarOpen}
            aria-haspopup="dialog"
            aria-controls={calendarOpen ? calendarId : undefined}
            onClick={() => setCalendarOpen((value) => !value)}
          >
            <CalendarDays size={17} />
            <span>
              <strong>{selectedDateText || '选择归档日期'}</strong>
              <small>{selectedDate ? '正在查看该自然日的历史对话' : '按上海自然日筛选历史归档'}</small>
            </span>
            <ChevronDown className={calendarOpen ? styles.dateFilterChevronOpen : ''} size={17} />
          </button>
        </section>

        <div className={styles.filterChips}>
          <button type="button" aria-pressed={favoritesOnly} className={favoritesOnly ? styles.favoriteActive : ''}
            onClick={() => onFavoritesChange?.(!favoritesOnly)}>
            <Star size={14} fill={favoritesOnly ? 'currentColor' : 'none'} />{t('收藏')}
            {favoritesCount !== undefined && <span>{favoritesCount}</span>}
          </button>
        </div>

        <div className={styles.actionBar}>
          <span>{selectedDateText ? `${selectedDateText} · ` : ''}{sessions.length} 条历史对话 · {webSessions.length} 条 Web{appSessions.length ? ` · ${appSessions.length} 条 APP` : ''}</span>
          <button
            type="button"
            disabled={loading || error || (sessions.length === 0 && !selectedDate && !favoritesOnly && !normalizedQuery) || chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction)}
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
            const source = session.source || 'web'
            const mutable = source === 'web'
            const active = mutable && session.session_id === activeSessionId
            const switching = session.session_id === switchingSessionId
            const displayName = sessionDisplayName(session)
            const summaryStatus = session.summary_status || ''
            const canRetrySummary = ['failed', 'retry_wait', 'exhausted'].includes(summaryStatus)
            const attempt = Math.max(0, session.summary_attempt_count || session.summary_retry_count || 0)
            const maxAttempts = Math.max(1, session.summary_max_attempts || 5)
            const completedChunks = Math.max(0, session.summary_checkpoint_next_chunk || 0)
            const totalChunks = Math.max(0, session.summary_checkpoint_total_chunks || 0)
            const retryAt = session.summary_retry_at ? formatDateTime(session.summary_retry_at) : ''
            const sourceLabel = source === 'web'
              ? '网页版'
              : source === 'app'
                ? 'APP版'
                  : source === 'cli'
                    ? 'CLI'
                    : source.startsWith('background:cron:')
                      ? '定时任务'
                    : source.startsWith('message:')
                    ? session.bound_platform || source.slice(8) || '外部消息'
                    : source
            const memoryStatus = session.memory_status || 'unknown'
            const memoryProcessed = Math.max(0, session.memory_processed_round || 0)
            const memoryTarget = Math.max(0, session.memory_target_round || session.rounds || 0)
            return <SessionActions key={`${source}:${session.session_id}`} session={session}
              onMark={onMarkSession} onRename={onRenameSession} onDelete={onDeleteSession}
              onOpen={() => { setSummaryPreview(null); if (!mutable) setReadOnlySession(session); else if (!active) setPendingSessionId(session.session_id); else onSelectSession(session.session_id) }}
              openDisabled={mutable && (chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction))}
              deleteDisabled={Boolean(switchingSessionId) || Boolean(pendingAction) || (chatRunning && active)}>
              {(menuTrigger, onContextMenu) => <article onContextMenu={onContextMenu}
              className={`${styles.sessionCard} ${!mutable ? styles.readOnlyCard : ''} ${active ? styles.activeCard : ''}`}
            >
              <button
                type="button"
                className={styles.sessionSelect}
                disabled={mutable && (chatRunning || Boolean(switchingSessionId) || Boolean(pendingAction))}
                aria-label={`${mutable ? '打开' : '只读查看'}对话 ${displayName}`}
                onFocus={(event) => {
                  if (session.summary?.trim()) showSummaryPreview(session.summary, event.currentTarget)
                }}
                onBlur={() => setSummaryPreview(null)}
                onClick={() => {
                  setSummaryPreview(null)
                  if (!mutable) setReadOnlySession(session)
                  else if (!active) setPendingSessionId(session.session_id)
                }}
              >
                <span className={styles.cardIcon}>{switching ? <LoaderCircle className={styles.spinning} size={18} /> : <MessageSquareText size={18} />}</span>
                <span className={styles.cardCopy}>
                  <strong><b>{displayName}</b>{session.pinned && <Pin size={13} aria-label={t('已置顶')} />}{session.favorite && <Star size={13} fill="currentColor" aria-label={t('已收藏')} />}</strong>
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
                  <small>{sourceLabel} · {session.rounds} 轮 · {formatDateTime(session.updated_at)}</small>
                  <small className={memoryStatus === 'failed' ? styles.memoryFailed : styles.memoryState}>记忆 {memoryStatus} · {memoryProcessed}/{memoryTarget}</small>
                </span>
                <span className={`${styles.stateBadge} ${active ? styles.activeBadge : ''}`}>
                  {active ? '当前对话' : !mutable ? (session.run_state === 'running' ? '渠道使用中' : '只读') : session.state === 'closed' ? '已保存' : '历史对话'}
                </span>
              </button>
              <span className={styles.cardActions}>
                {menuTrigger}
                {mutable && <>
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
              </>}
              </span>
            </article>}
            </SessionActions>
          })}
          {!loading && !error && (!normalizedQuery || Boolean(onSearchChange)) && hasMore && <button
            type="button"
            className={styles.loadMoreButton}
            disabled={loadingMore}
            onClick={() => { void onLoadMore() }}
          >
            {loadingMore && <LoaderCircle className={styles.spinning} size={15} />}
            {loadingMore ? '正在加载更早对话…' : '加载更早对话'}
          </button>}
          {!loading && !error && filteredSessions.length === 0 && <div className={styles.empty}>
            <Search size={22} />
            <strong>{sessions.length === 0 ? (favoritesOnly ? t('暂无收藏的对话') : selectedDate ? '该日期没有历史对话' : '暂无历史对话') : '没有匹配的对话'}</strong>
            <span>{sessions.length === 0 ? (selectedDate ? '请选择其他日期，或清除日期筛选查看全部归档。' : 'Web、CLI、定时任务或外部消息完成对话后会在这里生成历史记录。') : '请尝试输入其他对话名称或渠道。'}</span>
          </div>}
        </div>
      </div>
      </>}
    </aside>
    {open && <button ref={backdropRef} className="drawer-backdrop" aria-label="关闭历史对话"
      onClick={() => { if (calendarOpen) setCalendarOpen(false); else closeDrawer() }} />}
    {open && calendarOpen && createPortal(
      <div ref={calendarRef} id={calendarId} className={styles.calendar}
        style={calendarPosition} role="dialog" aria-label="选择历史归档日期">
        <header className={styles.calendarHeader}>
          <button type="button" onClick={() => moveCalendarMonth(-1)} aria-label="上一个月"><ChevronLeft size={21} /></button>
          <strong>{visibleMonth.year}年{visibleMonth.month + 1}月</strong>
          <button type="button" onClick={() => moveCalendarMonth(1)} aria-label="下一个月"><ChevronRight size={21} /></button>
        </header>
        <div className={styles.calendarGrid}>
          {WEEKDAY_LABELS.map((label) => <span key={label} className={styles.weekday}>{label}</span>)}
          {calendarDays.map((item) => {
            const selected = item.value === selectedDate
            const today = item.value === todayDate
            return <button
              key={item.value}
              type="button"
              className={`${styles.calendarDay} ${!item.inMonth ? styles.calendarDayOutside : ''} ${today ? styles.calendarDayToday : ''} ${selected ? styles.calendarDaySelected : ''}`}
              disabled={item.value > todayDate}
              aria-label={`${item.year}年${item.month + 1}月${item.day}日`}
              aria-pressed={selected}
              data-today={today || undefined}
              onClick={() => chooseArchiveDate(item.value)}
            >{item.day}</button>
          })}
        </div>
        <footer className={styles.calendarFooter}>
          <button type="button" onClick={clearArchiveDate}>清除</button>
          <button type="button" onClick={() => chooseArchiveDate(todayDate)}>今天</button>
        </footer>
      </div>,
      document.body,
    )}
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
    <ReadOnlySessionHistoryDialog user={user} session={readOnlySession} onClose={() => setReadOnlySession(null)} />
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
      detail="全部渠道 · 包含只读对话"
      description="Web、APP、CLI、定时任务及外部消息的完整历史（含未加载记录）将永久删除，包括已置顶和收藏的对话，不受搜索、日期或收藏筛选限制，且无法撤销。不删除已提取的记忆、附件文件或定时任务本身。"
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
