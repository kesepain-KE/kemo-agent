import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, Search } from 'lucide-react'
import type { SettingsModelOption } from './settingsSupport'

export function ModelSelectField({
  label,
  value,
  placeholder,
  models,
  enabled,
  onChange,
}: {
  label: string
  value: string
  placeholder: string
  models: SettingsModelOption[]
  enabled: boolean
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const [position, setPosition] = useState({
    left: 12, top: 12 as number | undefined, bottom: undefined as number | undefined,
    width: 360, maxHeight: 430,
  })
  const listboxId = useId()
  const pickerLabel = label === '模型' ? '主模型' : label
  const availableModels = models
  const normalizedQuery = query.trim().toLowerCase()
  const filteredModels = availableModels.filter((item) => {
    if (!normalizedQuery) return true
    return `${item.id} ${item.provider_id} ${item.provider_model}`.toLowerCase().includes(normalizedQuery)
  })

  const close = () => {
    setOpen(false)
    triggerRef.current?.focus()
  }

  useLayoutEffect(() => {
    if (!open) return
    const update = () => {
      const rect = rootRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(Math.max(rect.width, 360), window.innerWidth - 24)
      const above = rect.top - 20
      const below = window.innerHeight - rect.bottom - 20
      const upward = below < 300 && above > below
      setPosition({
        left: Math.max(12, Math.min(rect.right - width, window.innerWidth - width - 12)),
        width,
        top: upward ? undefined : rect.bottom + 8,
        bottom: upward ? window.innerHeight - rect.top + 8 : undefined,
        maxHeight: Math.max(80, Math.min(430, upward ? above : below)),
      })
    }
    update()
    searchRef.current?.focus()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(update)
    if (rootRef.current) observer?.observe(rootRef.current)
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const closeOnPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)
        && !popoverRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        setOpen(false)
        triggerRef.current?.focus()
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
    if (!enabled) setOpen(false)
  }, [enabled])

  if (!enabled) {
    return <input className="config-field" aria-label={label} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
  }

  return <div className={`model-select-wrap ${open ? 'open' : ''}`} ref={rootRef}>
    <div className="model-field-shell">
      <input
        className="config-field model-field-input"
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'Enter') {
            event.preventDefault()
            setOpen(true)
          }
          if (event.key === 'Escape') setOpen(false)
        }}
      />
      <button
        ref={triggerRef}
        type="button"
        className={`model-picker-trigger ${open ? 'open' : ''}`}
        aria-label={`${pickerLabel}选择模型`}
        aria-controls={open ? listboxId : undefined}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => { setQuery(''); setOpen((current) => !current) }}
      >
        <span>选择模型</span><ChevronDown size={14} aria-hidden="true" />
      </button>
    </div>
    {open ? createPortal(<div ref={popoverRef} className="model-select-popover config-select-popover"
      style={{ ...position, position: 'fixed', zIndex: 85 }} role="dialog" aria-label={`${pickerLabel}模型选择`}>
      <div className="model-select-popover-head">
        <span><strong>{pickerLabel}选择</strong><small>{models[0]?.source === 'chat' ? 'Chat · Provider 模型 API' : 'KEMO · 内置模型目录'}</small></span>
        <em>{filteredModels.length} 个可用</em>
      </div>
      <div className="model-select-search">
        <Search size={15} aria-hidden="true" />
        <input ref={searchRef} aria-label={`${pickerLabel}筛选`} value={query} placeholder="搜索模型…"
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'ArrowDown') {
              event.preventDefault()
              popoverRef.current?.querySelector<HTMLButtonElement>('[role="option"]')?.focus()
            }
          }} />
      </div>
      <div className="model-select-options" id={listboxId} role="listbox" aria-label={`${pickerLabel}选项`}
        onKeyDown={(event) => {
          if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
          event.preventDefault()
          const options = Array.from(popoverRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])
          if (!options.length) return
          const index = options.indexOf(document.activeElement as HTMLButtonElement)
          const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length
          options[next]?.focus()
        }}>
        {filteredModels.length ? filteredModels.map((item) => <button
          type="button"
          role="option"
          aria-selected={value === item.id}
          className={value === item.id ? 'active' : ''}
          key={`${item.provider_id}:${item.id}`}
          onClick={() => { onChange(item.id); close(); setQuery('') }}
        >
          <span><strong>{item.id}</strong><small>{item.provider_id}{item.provider_model && item.provider_model !== item.id ? ` · ${item.provider_model}` : ''}</small></span>
          {value === item.id ? <Check size={15} /> : <i aria-hidden="true" />}
        </button>) : <div className="model-select-empty">没有匹配的模型；仍可直接输入模型名。</div>}
      </div>
    </div>, document.body) : null}
  </div>
}

