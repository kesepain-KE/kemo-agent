import type { ReactNode } from 'react'
import { AlertCircle, Inbox } from 'lucide-react'

export function ModuleFrame({
  kicker,
  title,
  description,
  actions,
  children,
}: {
  kicker: string
  title: string
  description: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="view module-view active">
      <div className="module-shell">
        <div className="module-inner">
          <header className="module-header">
            <div className="module-heading">
              <div className="module-kicker">{kicker}</div>
              <h2>{title}</h2>
              <p>{description}</p>
            </div>
            {actions && <div className="module-actions">{actions}</div>}
          </header>
          {children}
        </div>
      </div>
    </div>
  )
}

export function MetricCard({
  label,
  value,
  detail,
  symbol,
  tone,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  symbol: ReactNode
  tone?: 'success' | 'warning' | 'muted'
}) {
  return (
    <article className="metric-card">
      <div className="metric-top">
        <span>{label}</span>
        <span className={`metric-symbol ${tone || ''}`}>{symbol}</span>
      </div>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </article>
  )
}

export function StatusChip({ status, children }: { status: string; children?: ReactNode }) {
  const tone = ['failed', 'cancelled', 'error', 'missing'].includes(status)
    ? 'danger'
    : ['paused', 'pending', 'warning', 'not_connected'].includes(status)
      ? 'amber'
      : ['completed', 'enabled', 'running', 'approved', 'saved', 'configured'].includes(status)
        ? 'success'
        : 'gray'
  return <span className={`status-chip ${tone}`}>{children || statusLabel(status)}</span>
}

export function EmptyPanel({
  title,
  description,
  icon,
}: {
  title: string
  description: string
  icon?: ReactNode
}) {
  return (
    <div className="empty-panel">
      <span className="empty-panel-icon">{icon || <Inbox size={20} />}</span>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  )
}

export function ModuleError({ message = '读取运行态失败，请稍后重试。' }: { message?: string }) {
  return (
    <div className="module-error" role="alert">
      <AlertCircle size={17} />
      <span>{message}</span>
    </div>
  )
}

export function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: '等待确认',
    approved: '已批准',
    running: '运行中',
    paused: '已暂停',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
    skipped: '已跳过',
    enabled: '已启用',
    saved: '已保存',
    never: '尚未运行',
    not_connected: '未连接',
    registered: '已注册',
  }
  return labels[status] || status || '未知'
}

export function formatDateTime(value: string | number) {
  if (!value) return '—'
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value < 1024) return `${value} B`
  return `${(value / 1024).toFixed(value < 10 * 1024 ? 1 : 0)} KB`
}
