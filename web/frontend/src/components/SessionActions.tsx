import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { ExternalLink, MoreHorizontal, Pencil, Pin, PinOff, Star, Trash2 } from 'lucide-react'
import type { SessionSummary } from '../types/api'
import { t, useLanguage } from '../i18n'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'
import styles from './SessionActions.module.css'

export interface SessionActionCallbacks {
  onMark?: (session: SessionSummary, changes: { pinned?: boolean; favorite?: boolean }) => Promise<void>
  onRename?: (sessionId: string, title: string) => Promise<void>
  onDelete: (sessionId: string) => Promise<void> | void
}

interface Props extends SessionActionCallbacks {
  session: SessionSummary
  openDisabled?: boolean
  deleteDisabled?: boolean
  onOpen: () => void
  children: (trigger: ReactNode, onContextMenu: (event: MouseEvent) => void) => ReactNode
}

/** One shared menu contract for the history drawer and recent-conversation sidebar. */
export function SessionActions({
  session, openDisabled, deleteDisabled, onOpen, onMark, onRename, onDelete, children,
}: Props) {
  useLanguage()
  const [anchor, setAnchor] = useState<{ x: number; y: number } | null>(null)
  const [position, setPosition] = useState({ left: 12, top: 12 })
  const [dialog, setDialog] = useState<'rename' | 'delete' | ''>('')
  const [title, setTitle] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const menuRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const restoreRef = useRef<HTMLElement | null>(null)
  const mutable = (session.source || 'web') === 'web'
  const name = session.title || t('未命名对话')
  const restoreFocus = () => {
    const element = restoreRef.current?.isConnected ? restoreRef.current : triggerRef.current
    element?.focus({ preventScroll: true })
  }
  const dismiss = () => { setAnchor(null); restoreFocus() }
  const show = (x: number, y: number) => {
    restoreRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : triggerRef.current
    setError('')
    setAnchor({ x, y })
  }

  useEffect(() => {
    if (dialog !== 'rename') return
    const frame = requestAnimationFrame(() => { inputRef.current?.focus(); inputRef.current?.select() })
    return () => cancelAnimationFrame(frame)
  }, [dialog])

  useLayoutEffect(() => {
    if (!anchor || !menuRef.current) return
    const menu = menuRef.current
    setPosition({
      left: Math.max(8, Math.min(anchor.x, window.innerWidth - menu.offsetWidth - 8)),
      top: Math.max(8, Math.min(anchor.y, window.innerHeight - menu.offsetHeight - 8)),
    })
    menu.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus({ preventScroll: true })
  }, [anchor, error])

  useEffect(() => {
    if (!anchor && !dialog) return
    const outside = (event: PointerEvent) => {
      if (anchor && !menuRef.current?.contains(event.target as Node)
        && !triggerRef.current?.contains(event.target as Node)) setAnchor(null)
    }
    const keydown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopImmediatePropagation()
        if (!pending) { setDialog(''); dismiss() }
        return
      }
      if (!anchor || !menuRef.current) return
      const items = [...menuRef.current.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')]
      const index = items.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'ArrowDown' ? (index + 1) % items.length
        : event.key === 'ArrowUp' ? (index - 1 + items.length) % items.length
          : event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : -1
      if (next >= 0) { event.preventDefault(); items[next]?.focus({ preventScroll: true }) }
      if (event.key === 'Tab') setAnchor(null)
    }
    const reposition = () => { if (anchor && !pending) setAnchor(null) }
    // Focus/scroll anchoring can emit a delayed scroll immediately after opening.
    // Dismiss on user scroll intent outside the menu, not those synthetic shifts;
    // a short viewport must still allow scrolling inside the menu itself.
    const scrollIntent = (event: Event) => {
      if (!menuRef.current?.contains(event.target as Node)) reposition()
    }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', keydown, true)
    window.addEventListener('resize', reposition)
    window.addEventListener('wheel', scrollIntent, { capture: true, passive: true })
    window.addEventListener('touchmove', scrollIntent, { capture: true, passive: true })
    return () => {
      document.removeEventListener('pointerdown', outside)
      document.removeEventListener('keydown', keydown, true)
      window.removeEventListener('resize', reposition)
      window.removeEventListener('wheel', scrollIntent, true)
      window.removeEventListener('touchmove', scrollIntent, true)
    }
  }, [anchor, dialog, pending])

  const mark = async (changes: { pinned?: boolean; favorite?: boolean }) => {
    if (pending || !onMark) return
    setPending(true)
    setError('')
    try { await onMark(session, changes); dismiss() }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('操作失败，请重试')) }
    finally { setPending(false) }
  }
  const confirm = async () => {
    if (pending) return
    setPending(true)
    setError('')
    try {
      if (dialog === 'rename') await onRename?.(session.session_id, title.trim())
      else await onDelete(session.session_id)
      setDialog('')
      restoreFocus()
    } catch (cause) { setError(cause instanceof Error ? cause.message : t('操作失败，请重试')) }
    finally { setPending(false) }
  }
  const beginDialog = (value: 'rename' | 'delete') => {
    setAnchor(null)
    setError('')
    setTitle(session.title || '')
    setDialog(value)
  }
  const trigger = <button ref={triggerRef} className={styles.trigger} type="button"
    aria-label={`${t('对话操作')} ${name}`} aria-haspopup="menu" aria-expanded={Boolean(anchor)}
    disabled={pending} onClick={(event) => {
      event.stopPropagation()
      if (anchor) dismiss()
      else { const rect = event.currentTarget.getBoundingClientRect(); show(rect.left, rect.bottom + 4) }
    }}><MoreHorizontal size={17} /></button>

  return <>
    {children(trigger, (event) => {
      event.preventDefault()
      event.stopPropagation()
      if (!pending) show(event.clientX, event.clientY)
    })}
    {anchor && createPortal(<div ref={menuRef} className={styles.menu} style={position}
      role="menu" aria-label={`${t('对话操作')} ${name}`}>
      <button role="menuitem" disabled={pending || !onMark} onClick={() => void mark({ pinned: !session.pinned })}>
        {session.pinned ? <PinOff size={16} /> : <Pin size={16} />}{t(session.pinned ? '取消置顶' : '置顶')}
      </button>
      <button role="menuitem" disabled={pending || !onMark} onClick={() => void mark({ favorite: !session.favorite })}>
        <Star size={16} fill={session.favorite ? 'currentColor' : 'none'} />{t(session.favorite ? '取消收藏' : '收藏')}
      </button>
      <div role="separator" />
      <button role="menuitem" disabled={!mutable || pending || !onRename} onClick={() => beginDialog('rename')}>
        <Pencil size={16} />{t('重命名')}
      </button>
      <button role="menuitem" disabled={openDisabled || pending} onClick={() => { dismiss(); onOpen() }}>
        <ExternalLink size={16} />{t('打开')}
      </button>
      <div role="separator" />
      <button role="menuitem" className={styles.danger} disabled={!mutable || deleteDisabled || pending}
        onClick={() => beginDialog('delete')}><Trash2 size={16} />{t('删除')}</button>
      {error && <span className={styles.error} role="alert">{error}</span>}
    </div>, document.body)}
    <GlobalConfirmDialog open={Boolean(dialog)}
      title={t(dialog === 'rename' ? '重命名对话' : '确认删除这条历史对话？')}
      detail={name}
      description={dialog === 'rename' ? <label className={styles.rename}>
        <span>{t('对话名称')}</span>
        <input ref={inputRef} aria-label={t('对话名称')} value={title} maxLength={80} disabled={pending}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.nativeEvent.isComposing && title.trim()) { event.preventDefault(); void confirm() }
          }} />
        <small>{title.trim().length}/80</small>
      </label> : t('该对话及其完整历史将被永久删除，此操作无法撤销。')}
      icon={dialog === 'rename' ? <Pencil size={21} /> : <Trash2 size={21} />}
      tone={dialog === 'rename' ? 'warning' : 'danger'} error={error}
      cancelLabel={t('取消')} confirmLabel={t(dialog === 'rename' ? '保存名称' : '确认删除')}
      confirmDisabled={dialog === 'rename' && !title.trim()}
      pending={pending} pendingLabel={t('正在保存…')}
      onCancel={() => { if (!pending) { setDialog(''); restoreFocus() } }}
      onConfirm={() => { if (dialog !== 'rename' || title.trim()) void confirm() }} />
  </>
}
