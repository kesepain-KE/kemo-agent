import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import styles from './GlobalConfirmDialog.module.css'

export interface GlobalConfirmDialogProps {
  open: boolean
  title: string
  description: ReactNode
  detail?: ReactNode
  error?: ReactNode
  icon: ReactNode
  tone?: 'warning' | 'danger'
  confirmLabel: string
  pendingLabel?: string
  cancelLabel?: string
  pending?: boolean
  onConfirm: () => void
  onCancel: () => void
}

const focusableSelector = [
  'button:not(:disabled)',
  '[href]',
  'input:not(:disabled)',
  'select:not(:disabled)',
  'textarea:not(:disabled)',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

export function GlobalConfirmDialog({
  open,
  title,
  description,
  detail,
  error,
  icon,
  tone = 'warning',
  confirmLabel,
  pendingLabel,
  cancelLabel = '取消',
  pending = false,
  onConfirm,
  onCancel,
}: GlobalConfirmDialogProps) {
  const titleId = useId()
  const descriptionId = useId()
  const dialogRef = useRef<HTMLElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)
  const pendingRef = useRef(pending)
  const onCancelRef = useRef(onCancel)

  pendingRef.current = pending
  onCancelRef.current = onCancel

  useEffect(() => {
    if (!open || typeof document === 'undefined') return

    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const frame = requestAnimationFrame(() => confirmRef.current?.focus())

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (!pendingRef.current) onCancelRef.current()
        return
      }
      if (event.key !== 'Tab' || !dialogRef.current) return

      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(focusableSelector))
        .filter((element) => !element.hasAttribute('disabled'))
      if (focusable.length === 0) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      cancelAnimationFrame(frame)
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [open])

  if (!open || typeof document === 'undefined') return null

  return createPortal(
    <div
      className={styles.overlay}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !pending) onCancel()
      }}
    >
      <section
        ref={dialogRef}
        className={`${styles.dialog} ${styles[tone]}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
      >
        <span className={styles.icon}>{icon}</span>
        <div className={styles.copy}>
          <strong id={titleId}>{title}</strong>
          {detail ? <span className={styles.detail}>{detail}</span> : null}
          <p id={descriptionId}>{description}</p>
        </div>
        {error ? <div className={styles.error} role="alert">{error}</div> : null}
        <footer className={styles.actions}>
          <button type="button" disabled={pending} onClick={onCancel}>{cancelLabel}</button>
          <button ref={confirmRef} type="button" className={styles.confirm} disabled={pending} onClick={onConfirm}>
            {pending ? (pendingLabel || confirmLabel) : confirmLabel}
          </button>
        </footer>
      </section>
    </div>,
    document.body,
  )
}
