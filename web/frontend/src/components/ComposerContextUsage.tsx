import { useEffect, useId, useLayoutEffect, useRef, useState, type MouseEvent } from 'react'
import { createPortal } from 'react-dom'
import { t, useLanguage } from '../i18n'
import type { OverviewResponse } from '../types/api'
import styles from './ComposerContextUsage.module.css'

interface ComposerContextUsageProps {
  snapshot?: OverviewResponse['context_snapshot']
  capacityTokens?: number
  cacheHitRate?: number | null
}

function isTokenCount(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
}

export function ComposerContextUsage({ snapshot, capacityTokens, cacheHitRate }: ComposerContextUsageProps) {
  const language = useLanguage()
  const [open, setOpen] = useState(false)
  const controlsRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const openingTriggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const popoverId = useId()
  const [position, setPosition] = useState({
    left: 12, bottom: 12 as number | undefined, top: undefined as number | undefined, width: 340, maxHeight: 420,
  })
  const capacity = isTokenCount(snapshot?.capacity_tokens) && snapshot.capacity_tokens > 0
    ? snapshot.capacity_tokens : capacityTokens
  const available = Boolean(snapshot?.available && isTokenCount(snapshot.total_tokens)
    && isTokenCount(capacity) && capacity > 0)
  const total = available ? snapshot!.total_tokens : undefined
  const fraction = available ? total! / capacity! : undefined
  const ringPercent = Math.min(100, (fraction ?? 0) * 100)
  const estimated = ['estimated', 'local_estimate'].includes(snapshot?.measurement ?? '')
  const compact = (value: number | undefined, estimate = false) => isTokenCount(value)
    ? `${estimate ? '~' : ''}${new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)}`
    : '—'
  const exact = (value: number | undefined) => isTokenCount(value)
    ? new Intl.NumberFormat(language).format(value) : '—'
  const percent = (value: number | undefined) => typeof value === 'number' && Number.isFinite(value)
    ? new Intl.NumberFormat(language, { style: 'percent', maximumFractionDigits: 1 }).format(value) : '—'
  const cacheRate = typeof cacheHitRate === 'number' && Number.isFinite(cacheHitRate)
    && cacheHitRate >= 0 && cacheHitRate <= 1 ? cacheHitRate : undefined
  const cacheRateLabel = cacheRate === undefined ? '—' : new Intl.NumberFormat(language, {
    style: 'percent', minimumFractionDigits: 2, maximumFractionDigits: 2,
  }).format(cacheRate)
  const togglePopover = (event: MouseEvent<HTMLButtonElement>) => {
    openingTriggerRef.current = event.currentTarget
    setOpen(current => !current)
  }
  const rows = [
    { label: '系统提示词', value: snapshot?.system_prompt_tokens, segment: 'system', className: styles.system },
    { label: '工具定义', value: snapshot?.tool_schema_tokens, segment: 'tools', className: styles.tools },
    { label: '历史对话', value: snapshot
      ? snapshot.conversation_tokens + snapshot.summary_tokens : undefined, segment: 'history', className: styles.history },
    ...(snapshot && snapshot.other_tokens > 0
      ? [{ label: '动态上下文', value: snapshot.other_tokens, segment: 'dynamic', className: styles.dynamic }] : []),
  ]

  useLayoutEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(340, window.innerWidth - 24)
      const above = Math.max(0, rect.top - 20)
      const below = Math.max(0, window.innerHeight - rect.bottom - 20)
      const upward = above >= 240 || above >= below
      setPosition({
        width,
        left: Math.max(12, Math.min(rect.left + (rect.width - width) / 2, window.innerWidth - width - 12)),
        bottom: upward ? window.innerHeight - rect.top + 8 : undefined,
        top: upward ? undefined : rect.bottom + 8,
        maxHeight: Math.max(40, Math.min(420, upward ? above : below)),
      })
    }
    updatePosition()
    popoverRef.current?.focus({ preventScroll: true })
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updatePosition)
    if (triggerRef.current) observer?.observe(triggerRef.current)
    const composer = triggerRef.current?.closest('.composer-inner')
    if (composer) observer?.observe(composer)
    const layout = triggerRef.current?.closest('.chat-layout')
    if (layout) observer?.observe(layout)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node
      if (!controlsRef.current?.contains(target) && !popoverRef.current?.contains(target)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      openingTriggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape, true)
    }
  }, [open])

  return <>
    <div ref={controlsRef} className={styles.controls}>
      <button type="button" className={styles.cacheTrigger}
        aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? popoverId : undefined}
        title={t('当前对话空间累计')} onClick={togglePopover}>
        {t('缓存命中 {rate}', { rate: cacheRateLabel })}
      </button>
      <button ref={triggerRef} type="button" className={styles.trigger}
        aria-label={t('查看上下文用量')} aria-haspopup="dialog" aria-expanded={open}
        aria-controls={open ? popoverId : undefined}
        title={`${t('上下文已用')} ${percent(fraction)} · ${compact(total, estimated)} / ${compact(capacity)}`}
        onClick={togglePopover}>
        <span className="context-usage-ring" aria-hidden="true">
          <svg viewBox="0 0 36 36">
            <circle className="context-usage-ring-track" cx="18" cy="18" r="15" pathLength="100" />
            <circle className="context-usage-ring-fill" cx="18" cy="18" r="15" pathLength="100"
              strokeDasharray={`${ringPercent} 100`} />
          </svg>
        </span>
      </button>
    </div>
    {open && createPortal(
      <div ref={popoverRef} id={popoverId} className={styles.popover} style={position}
        role="dialog" aria-label={t('上下文用量')} tabIndex={-1}>
        <div className={styles.heading}>
          <span>{t('上下文已用')} <strong>{percent(fraction)}</strong></span>
          <strong title={`${exact(total)} / ${exact(capacity)} Token`}>
            {compact(total, estimated)} / {compact(capacity)}
          </strong>
        </div>
        <div className={styles.track} role={available ? 'progressbar' : undefined}
          aria-label={available ? t('上下文窗口占用') : undefined}
          aria-valuemin={available ? 0 : undefined} aria-valuemax={available ? 100 : undefined}
          aria-valuenow={available ? ringPercent : undefined}>
          {available && rows.map(row => <i key={row.label} data-segment={row.segment}
            className={row.className}
            style={{ width: `${isTokenCount(row.value) ? row.value / Math.max(capacity!, total!) * 100 : 0}%` }} />)}
        </div>
        <dl className={styles.breakdown}>
          {rows.map(row => <div key={row.label}>
            <dt><i className={row.className} aria-hidden="true" />{t(row.label)}</dt>
            <dd title={available ? `${exact(row.value)} Token` : undefined}>
              {compact(available ? row.value : undefined, estimated)}
            </dd>
          </div>)}
        </dl>
        <div className={styles.cache}>
          <div><span>{t('Token 总命中率')}</span><strong>{cacheRateLabel}</strong></div>
          <small>{t('当前对话空间累计')}</small>
        </div>
      </div>, document.body,
    )}
  </>
}
