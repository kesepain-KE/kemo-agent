import { useEffect, useId, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { reasoningEffortOptions, type ReasoningEffort } from '../reasoningEffort'

interface ReasoningEffortSelectProps {
  value: ReasoningEffort
  onChange: (value: ReasoningEffort) => void
  ariaLabel: string
  variant?: 'settings' | 'compact'
  disabled?: boolean
}

export function ReasoningEffortSelect({
  value,
  onChange,
  ariaLabel,
  variant = 'settings',
  disabled = false,
}: ReasoningEffortSelectProps) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const selected = reasoningEffortOptions.find((option) => option.value === value) ?? reasoningEffortOptions[2]

  useEffect(() => {
    if (!open) return
    const closeOnPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeOnPointerDown)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnPointerDown)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

  useEffect(() => {
    if (disabled) setOpen(false)
  }, [disabled])

  return <div className={`reasoning-effort-select ${variant} ${open ? 'open' : ''}`} ref={rootRef}>
    <button
      type="button"
      className="reasoning-effort-trigger"
      role="combobox"
      aria-label={ariaLabel}
      aria-controls={listboxId}
      aria-expanded={open}
      aria-haspopup="listbox"
      disabled={disabled}
      onClick={() => setOpen((current) => !current)}
      onKeyDown={(event) => {
        if (!open && event.key === 'ArrowDown') {
          event.preventDefault()
          setOpen(true)
        }
      }}
    >
      <span>{variant === 'settings' ? selected.settingsLabel : selected.label}</span>
      <ChevronDown size={16} />
    </button>
    {open ? <div className="reasoning-effort-popover" id={listboxId} role="listbox" aria-label={`${ariaLabel}选项`}>
      {reasoningEffortOptions.map((option) => <button
        type="button"
        role="option"
        aria-selected={option.value === value}
        className={option.value === value ? 'active' : ''}
        key={option.value}
        onClick={() => {
          onChange(option.value)
          setOpen(false)
        }}
      >
        <span><strong>{option.title}</strong><small>{option.description}</small></span>
        {option.value === value ? <Check size={15} /> : <i aria-hidden="true" />}
      </button>)}
    </div> : null}
  </div>
}
