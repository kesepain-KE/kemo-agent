import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import styles from './HoverPreview.module.css'

interface HoverPreviewProps {
  children: ReactNode
  preview: ReactNode
  className?: string
  ariaLabel?: string
}

export function HoverPreview({ children, preview, className = '', ariaLabel }: HoverPreviewProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef<HTMLDivElement>(null)
  const openTimerRef = useRef<number | null>(null)
  const closeTimerRef = useRef<number | null>(null)
  const [position, setPosition] = useState<{ left: number; top: number; width: number; above: boolean } | null>(null)

  const cancelTimers = () => {
    if (openTimerRef.current !== null) window.clearTimeout(openTimerRef.current)
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current)
    openTimerRef.current = null
    closeTimerRef.current = null
  }
  const closeNow = () => {
    cancelTimers()
    setPosition(null)
  }
  const close = () => {
    if (openTimerRef.current !== null) window.clearTimeout(openTimerRef.current)
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current)
    openTimerRef.current = null
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null
      setPosition(null)
    }, 80)
  }
  const open = () => {
    cancelTimers()
    if (position) return
    openTimerRef.current = window.setTimeout(() => {
      openTimerRef.current = null
      const host = hostRef.current
      if (!host || host.scrollHeight <= host.clientHeight + 1) return
      const rect = host.getBoundingClientRect()
      const width = Math.min(Math.max(rect.width, 288), Math.max(288, window.innerWidth - 24))
      const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
      const above = rect.bottom + 340 > window.innerHeight && rect.top > 340
      setPosition({
        left,
        top: above ? rect.top - 8 : rect.bottom + 8,
        width,
        above,
      })
    }, 200)
  }

  useEffect(() => {
    if (!position) return
    const dismiss = () => closeNow()
    const dismissScroll = (event: Event) => {
      if (previewRef.current?.contains(event.target as Node)) return
      closeNow()
    }
    window.addEventListener('resize', dismiss)
    window.addEventListener('scroll', dismissScroll, true)
    return () => {
      window.removeEventListener('resize', dismiss)
      window.removeEventListener('scroll', dismissScroll, true)
    }
  }, [position])

  useEffect(() => () => {
    cancelTimers()
  }, [])

  return <>
    <div
      ref={hostRef}
      className={className}
      tabIndex={0}
      aria-label={ariaLabel}
      onPointerEnter={open}
      onPointerLeave={close}
      onFocus={open}
      onBlur={close}
    >{children}</div>
    {position ? createPortal(
      <div
        ref={previewRef}
        role="tooltip"
        className={`${styles.preview} ${position.above ? styles.above : ''}`}
        style={{ left: position.left, top: position.top, width: position.width }}
        onPointerEnter={cancelTimers}
        onPointerLeave={close}
      >{preview}</div>,
      document.body,
    ) : null}
  </>
}
