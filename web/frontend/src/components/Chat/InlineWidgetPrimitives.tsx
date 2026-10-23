import type { InlineWidgetTone } from './inlineWidgetProtocol'
import styles from './InlineWidget.module.css'

export function toneClass(tone: InlineWidgetTone | undefined): string {
  return styles[tone || 'neutral'] || styles.neutral
}

export function displayValue(value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

export function WidgetHeader({ title, description, status }: {
  title?: string
  description?: string
  status?: { label: string; tone?: InlineWidgetTone }
}) {
  if (!title && !description && !status) return null
  return (
    <header className={styles.header}>
      <div className={styles.heading}>
        {title ? <h3>{title}</h3> : null}
        {description ? <p>{description}</p> : null}
      </div>
      {status ? <span className={`${styles.status} ${toneClass(status.tone)}`}>{status.label}</span> : null}
    </header>
  )
}

