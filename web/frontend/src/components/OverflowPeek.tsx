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

interface PeekAttachment {
  name: string
  kind: 'image' | 'audio' | 'video' | 'file'
  previewUrl?: string
}

export function OverflowPeek({
  text,
  attachmentNames = [],
  attachments,
  className = '',
  children,
}: {
  text: string
  attachmentNames?: string[]
  attachments?: PeekAttachment[]
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
  const names = attachments?.length ? attachments.map(item => item.name) : attachmentNames
  const mediaDialog = pinned && Boolean(attachments?.some(item => item.previewUrl))

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
    if (pinned && !nextPinned || !measureOverflow()) return
    updatePosition()
    setPinned(nextPinned)
    setOpen(true)
  }

  const closePreview = () => {
    if (tooltipRef.current?.contains(document.activeElement)) anchorRef.current?.focus()
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
  }, [text, names.join('|')])

  useEffect(() => {
    if (mediaDialog) tooltipRef.current?.focus()
  }, [mediaDialog])

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
    if (!overflowing || event.target !== event.currentTarget) return
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

  const label = names.length
    ? (text || '仅附件消息') + '；附件：' + names.join('、')
    : text

  return <>
    <div
      ref={anchorRef}
      className={[styles.anchor, className].filter(Boolean).join(' ')}
      data-overflow={overflowing ? 'true' : 'false'}
      tabIndex={overflowing ? 0 : undefined}
      aria-describedby={open && label ? tooltipId : undefined}
      onLoadCapture={measureOverflow}
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
        role={mediaDialog ? 'dialog' : 'tooltip'}
        aria-label={mediaDialog ? '完整消息跟进' : undefined}
        tabIndex={mediaDialog ? -1 : undefined}
        style={{ top: position.top, left: position.left, width: position.width }}
      >
        <small>完整消息跟进</small>
        {mediaDialog ? <button type="button" className={styles.close} onClick={closePreview}>关闭预览</button> : null}
        {text ? <p>{text}</p> : null}
        {names.length ? <div className={styles.attachments}>
          <strong>附件</strong>
          <ul>{names.map((name, index) => {
            const item = attachments?.[index]
            const url = item?.previewUrl
            return <li key={name + '_' + index}>
              {name}
              {url && item.kind === 'image' ? <img src={url} alt={name} /> : null}
              {url && item.kind === 'audio' ? <audio src={url} controls preload="metadata"
                tabIndex={pinned ? undefined : -1} aria-label={name} /> : null}
              {url && item.kind === 'video' ? <video src={url} controls preload="metadata"
                tabIndex={pinned ? undefined : -1} aria-label={name} /> : null}
            </li>
          })}</ul>
        </div> : null}
      </div>,
      document.body,
    ) : null}
  </>
}
