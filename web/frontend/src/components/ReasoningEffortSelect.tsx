import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Gauge } from 'lucide-react'
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
  const popoverRef = useRef<HTMLDivElement>(null)
  const [position, setPosition] = useState({
    left: 12, bottom: 12 as number | undefined, top: undefined as number | undefined, width: 260, maxHeight: 320,
  })
  const floating = variant === 'composer' || variant === 'compact'
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
        rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
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

  useLayoutEffect(() => {
    if (!open || !floating) return
    // 挂到 body、测量锚点并约束视口，避免窄屏和附件/长草稿改变输入坞高度后裁切。
    const updatePosition = () => {
      const rect = rootRef.current?.querySelector('button')?.getBoundingClientRect()
      if (!rect) return
      if (variant === 'compact') {
        // 顶栏模型卡自身可滚动，选项独立浮在 body 上；两边各内缩 4px。
        const width = Math.min(Math.max(1, rect.width - 8), window.innerWidth - 24)
        const below = Math.max(0, window.innerHeight - rect.bottom - 20)
        const above = Math.max(0, rect.top - 20)
        const upward = below < 180 && above > below
        setPosition({
          left: Math.max(12, Math.min(rect.left + 4, window.innerWidth - width - 12)),
          top: upward ? undefined : rect.bottom + 8,
          bottom: upward ? window.innerHeight - rect.top + 8 : undefined,
          width,
          maxHeight: Math.min(360, upward ? above : below),
        })
        return
      }
      const width = Math.min(rect.width, window.innerWidth - 24)
      const top = Math.max(64, Math.min(rect.top, window.innerHeight - 12))
      setPosition({
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        top: undefined,
        bottom: window.innerHeight - top + 8,
        width,
        maxHeight: Math.min(360, top - 20),
      })
    }
    updatePosition()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updatePosition)
    if (rootRef.current) observer?.observe(rootRef.current)
    const composer = rootRef.current?.closest('.composer-inner')
    if (composer) observer?.observe(composer)
    const modelMenu = rootRef.current?.closest('.reference-model-menu')
    if (modelMenu) observer?.observe(modelMenu)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open, variant, floating])

  const popover = open ? <div ref={popoverRef}
    className={`reasoning-effort-popover${variant === 'composer' ? ' composer-reasoning-popover' : variant === 'compact' ? ' model-reasoning-popover' : ''}`}
    style={floating ? position : undefined}
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
        rootRef.current?.querySelector<HTMLButtonElement>('button')?.focus()
      }}>
      <span><strong>{option.title}</strong><small>{option.description}</small></span>
      {option.value === value ? <Check size={15} /> : <i aria-hidden="true" />}
    </button>)}
  </div> : null

  return <div className={`reasoning-effort-select ${variant === 'composer' ? 'reasoning-effort-composer' : variant} ${open ? 'open' : ''}`} ref={rootRef}>
    <button
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
    {floating && popover ? createPortal(popover, document.body) : popover}
  </div>
}
