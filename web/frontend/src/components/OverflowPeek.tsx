import {
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
  useEffect,
  useId,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'
import styles from './OverflowPeek.module.css'

interface PreviewPosition {
  top: number
  left: number
  width: number
  placement: 'above' | 'below'
}

export function OverflowPeek({
  text,
  attachmentNames = [],
  className = '',
  children,
}: {
  text: string
  attachmentNames?: string[]
  className?: string
  children: ReactNode
}) {
  const anchorRef = useRef<HTMLDivElement>(null)
  const tooltipRef = useRef<HTMLDivElement>(null)
  const tooltipId = useId()
  const [overflowing, setOverflowing] = useState(false)
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [position, setPosition] = useState<PreviewPosition | null>(null)

  const measureOverflow = () => {
    const anchor = anchorRef.current
    const next = Boolean(anchor && (
      anchor.scrollHeight > anchor.clientHeight + 1
      || anchor.scrollWidth > anchor.clientWidth + 1
    ))
    setOverflowing(next)
    return next
  }

  const updatePosition = () => {
    const anchor = anchorRef.current
    if (!anchor) return
    const rect = anchor.getBoundingClientRect()
    const viewportWidth = window.innerWidth
    const viewportHeight = window.innerHeight
    const width = Math.min(520, Math.max(240, viewportWidth - 16))
    const halfWidth = width / 2
    const left = Math.min(
      viewportWidth - halfWidth - 8,
      Math.max(halfWidth + 8, rect.left + rect.width / 2),
    )
    const placement = rect.top >= Math.min(220, viewportHeight * .35) ? 'above' : 'below'
    setPosition({
      top: placement === 'above' ? rect.top - 8 : rect.bottom + 8,
      left,
      width,
      placement,
    })
  }

  const showPreview = (nextPinned = false) => {
    if (!measureOverflow()) return
    updatePosition()
    setPinned(nextPinned)
    setOpen(true)
  }

  const closePreview = () => {
    setOpen(false)
    setPinned(false)
  }

  useEffect(() => {
    measureOverflow()
    const anchor = anchorRef.current
    if (!anchor || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measureOverflow)
    observer.observe(anchor)
    return () => observer.disconnect()
  }, [text, attachmentNames.join('|')])

  useEffect(() => {
    if (!open) return
    const reposition = () => updatePosition()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
    }
  }, [open])

  useEffect(() => {
    if (!pinned) return
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (anchorRef.current?.contains(target) || tooltipRef.current?.contains(target)) return
      closePreview()
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closePreview()
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [pinned])

  const interactiveTarget = (target: EventTarget | null) => (
    target instanceof Element
    && Boolean(target.closest('a, button, input, textarea, select, [role="button"]'))
  )

  const handleClick = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (interactiveTarget(event.target) || !measureOverflow()) return
    if (pinned) closePreview()
    else showPreview(true)
  }

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!overflowing) return
    if (event.key === 'Escape') {
      closePreview()
      return
    }
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (pinned) closePreview()
      else showPreview(true)
    }
  }

  const label = attachmentNames.length
    ? (text || '仅附件消息') + '；附件：' + attachmentNames.join('、')
    : text

  return <>
    <div
      ref={anchorRef}
      className={[styles.anchor, className].filter(Boolean).join(' ')}
      data-overflow={overflowing ? 'true' : 'false'}
      tabIndex={overflowing ? 0 : undefined}
      aria-describedby={open ? tooltipId : undefined}
      onMouseEnter={() => showPreview(false)}
      onMouseLeave={() => { if (!pinned) setOpen(false) }}
      onFocus={() => showPreview(false)}
      onBlur={(event) => {
        if (!pinned && !event.currentTarget.contains(event.relatedTarget)) setOpen(false)
      }}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
    >
      {children}
    </div>
    {open && position && label ? createPortal(
      <div
        ref={tooltipRef}
        id={tooltipId}
        className={[
          styles.tooltip,
          position.placement === 'above' ? styles.above : styles.below,
          pinned ? styles.interactive : '',
        ].filter(Boolean).join(' ')}
        role="tooltip"
        style={{ top: position.top, left: position.left, width: position.width }}
      >
        <small>完整消息跟进</small>
        {text ? <p>{text}</p> : null}
        {attachmentNames.length ? <div className={styles.attachments}>
          <strong>附件</strong>
          <ul>{attachmentNames.map((name, index) => <li key={name + '_' + index}>{name}</li>)}</ul>
        </div> : null}
      </div>,
      document.body,
    ) : null}
  </>
}
