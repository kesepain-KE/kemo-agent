import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Gauge } from 'lucide-react'
import { useAnchoredPopover } from '../hooks/useAnchoredPopover'
import {
  reasoningEffortOptions,
  type ReasoningEffort,
  type ReasoningEffortOption,
} from '../reasoningEffort'

interface ReasoningEffortSelectProps {
  value: ReasoningEffort
  onChange: (value: ReasoningEffort) => void
  ariaLabel: string
  variant?: 'settings' | 'compact' | 'composer'
  disabled?: boolean
  options?: readonly ReasoningEffortOption[]
  emptyLabel?: string
}

export function ReasoningEffortSelect({
  value,
  onChange,
  ariaLabel,
  variant = 'settings',
  disabled = false,
  options = reasoningEffortOptions,
  emptyLabel = '思考能力不可用',
}: ReasoningEffortSelectProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const position = useAnchoredPopover(open, triggerRef, {
    minWidth: variant === 'composer' ? 0 : variant === 'compact' ? 280 : 260,
    maxHeight: 360,
    preferAbove: variant === 'composer',
    align: variant === 'composer' ? 'start' : 'end',
  })
  const listboxId = useId()
  const selected = options.find((option) => option.value === value)
    ?? options.find((option) => option.value === 'medium')
    ?? options[0]
  const unavailable = options.length === 0

  useEffect(() => {
    if (!open) return
    const closeOnPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)
        && !popoverRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
        triggerRef.current?.focus({ preventScroll: true })
      }
    }
    document.addEventListener('pointerdown', closeOnPointerDown)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnPointerDown)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  useEffect(() => {
    if (disabled || unavailable) setOpen(false)
  }, [disabled, unavailable])

  const popover = open ? <div ref={popoverRef}
    className={`reasoning-effort-popover${variant === 'composer' ? ' composer-reasoning-popover' : variant === 'compact' ? ' model-reasoning-popover' : ''}`}
    style={position}
    id={listboxId} role="listbox" aria-label={`${ariaLabel}选项`}
    onKeyDown={(event) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
      event.preventDefault()
      const buttons = Array.from(popoverRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
      buttons[next]?.focus()
    }}>
    {options.map((option) => <button
      type="button" role="option" aria-selected={option.value === value}
      className={option.value === value ? 'active' : ''} key={option.value}
      onClick={() => {
        onChange(option.value)
        setOpen(false)
        triggerRef.current?.focus({ preventScroll: true })
      }}>
      <span><strong>{option.title}</strong><small>{option.description}</small></span>
      {option.value === value ? <Check size={15} /> : <i aria-hidden="true" />}
    </button>)}
  </div> : null

  return <div className={`reasoning-effort-select ${variant === 'composer' ? 'reasoning-effort-composer' : variant} ${open ? 'open' : ''}`} ref={rootRef}>
    <button
      ref={triggerRef}
      type="button"
      className={`reasoning-effort-trigger${variant === 'composer' ? ' model-pill' : ''}`}
      role="combobox"
      aria-label={ariaLabel}
      aria-controls={listboxId}
      aria-expanded={open}
      aria-haspopup="listbox"
      disabled={disabled || unavailable}
      onClick={() => setOpen((current) => !current)}
      onKeyDown={(event) => {
        if (!open && event.key === 'ArrowDown') {
          event.preventDefault()
          setOpen(true)
        }
      }}
    >
      {variant === 'composer' ? <Gauge size={13} /> : null}
      <span>{variant === 'composer' ? '思考档位 · ' : ''}{selected ? (variant === 'settings' ? selected.settingsLabel : selected.label) : emptyLabel}</span>
      <ChevronDown size={16} />
    </button>
    {popover ? createPortal(popover, document.body) : null}
  </div>
}
