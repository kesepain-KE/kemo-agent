import {
  CheckCircle2,
  CircleAlert,
  CircleDashed,
  Database,
  Filter,
  Layers3,
  LoaderCircle,
  Sparkles,
  Target,
  type LucideIcon,
} from 'lucide-react'
import styles from './GlobalSenseStatus.module.css'

export type SenseStatus = 'idle' | 'running' | 'success' | 'warning' | 'error'

export interface GlobalSenseStatusProps {
  sourceCount: number
  enabledSourceCount: number
  registeredDataCount: number
  injectedTokens: number
  registeredPassedCount?: number
  filteredSourceCount?: number
  status?: SenseStatus
  refreshing?: boolean
  onMetricClick?: (type: 'sources' | 'enabled' | 'data' | 'tokens') => void
}

interface MetricCardProps {
  icon: LucideIcon
  label: string
  value: string
  description: string
  status: SenseStatus
  onClick?: () => void
}

interface PipelineStepProps {
  icon: LucideIcon
  title: string
  value: string
  status: SenseStatus
}

const statusClasses: Record<SenseStatus, string> = {
  idle: styles.idle,
  running: styles.running,
  success: styles.success,
  warning: styles.warning,
  error: styles.error,
}

function formatTokenCount(value: number): string {
  if (value < 1000) return String(value)
  const result = value / 1000
  return `${Number.isInteger(result) ? result : result.toFixed(1)}K`
}

function StatusIcon({ status }: { status: SenseStatus }) {
  if (status === 'running') return <LoaderCircle className={styles.spinning} size={15} />
  if (status === 'success') return <CheckCircle2 size={15} />
  if (status === 'warning' || status === 'error') return <CircleAlert size={15} />
  return <CircleDashed size={15} />
}

function MetricCard({ icon: Icon, label, value, description, status, onClick }: MetricCardProps) {
  const content = <>
    <span className={styles.metricCopy}>
      <strong>{value}</strong>
      <span>{description}</span>
    </span>
    <span className={styles.metricIcon}><Icon size={19} strokeWidth={2} /></span>
    <span className={`${styles.metricState} ${statusClasses[status]}`} aria-label={`${label}状态：${status}`}><StatusIcon status={status} /></span>
  </>

  return onClick
    ? <button type="button" className={`${styles.metric} ${styles.metricButton} ${statusClasses[status]}`} aria-label={`${label}：${value}`} onClick={onClick}>{content}</button>
    : <article className={`${styles.metric} ${statusClasses[status]}`} aria-label={`${label}：${value}`}>{content}</article>
}

function PipelineStep({ icon: Icon, title, value, status }: PipelineStepProps) {
  return <div className={`${styles.pipelineStep} ${statusClasses[status]}`}>
    <span className={styles.pipelineIcon}><Icon size={20} strokeWidth={2} /></span>
    <span className={styles.pipelineCopy}><strong>{title}</strong><span>{value}</span></span>
    <span className={styles.pipelineState}><StatusIcon status={status} /></span>
  </div>
}

export function GlobalSenseStatus({
  sourceCount,
  enabledSourceCount,
  registeredDataCount,
  injectedTokens,
  registeredPassedCount = registeredDataCount,
  filteredSourceCount = enabledSourceCount,
  status = 'success',
  refreshing = false,
  onMetricClick,
}: GlobalSenseStatusProps) {
  const resolvedStatus: SenseStatus = refreshing ? 'running' : status
  const discoveryStatus: SenseStatus = refreshing ? 'running' : sourceCount ? 'success' : 'idle'
  const registrationStatus: SenseStatus = refreshing ? 'running' : registeredDataCount ? 'success' : sourceCount ? 'warning' : 'idle'
  const filterStatus: SenseStatus = refreshing ? 'running' : enabledSourceCount ? 'success' : sourceCount ? 'warning' : 'idle'
  const injectionStatus: SenseStatus = refreshing ? 'running' : injectedTokens ? resolvedStatus : enabledSourceCount ? 'warning' : 'idle'

  return <section className={styles.root} aria-label="全局感知状态">
    <div className={styles.metrics}>
      <MetricCard icon={Layers3} label="感知来源" value={`${sourceCount} 个来源`} description="已接入感知源总数" status={discoveryStatus} onClick={onMetricClick ? () => onMetricClick('sources') : undefined} />
      <MetricCard icon={Target} label="当前启用" value={`${enabledSourceCount} 个启用`} description="当前启用的来源" status={filterStatus} onClick={onMetricClick ? () => onMetricClick('enabled') : undefined} />
      <MetricCard icon={Database} label="注册数据项" value={`${registeredDataCount} 个数据项`} description="已注册的数据文件" status={registrationStatus} onClick={onMetricClick ? () => onMetricClick('data') : undefined} />
      <MetricCard icon={Sparkles} label="预计注入" value={`注入 ${formatTokenCount(injectedTokens)} tokens`} description="预计注入系统提示词" status={injectionStatus} onClick={onMetricClick ? () => onMetricClick('tokens') : undefined} />
    </div>

    <div className={styles.pipeline} aria-label="感知数据处理流程">
      <PipelineStep icon={Layers3} title="感知源发现" value={refreshing ? '正在扫描来源' : `${sourceCount} 个来源`} status={discoveryStatus} />
      <span className={`${styles.connector} ${statusClasses[registrationStatus]}`} aria-hidden="true" />
      <PipelineStep icon={Database} title="数据注册" value={refreshing ? '正在注册数据' : `${registeredPassedCount} 项通过`} status={registrationStatus} />
      <span className={`${styles.connector} ${statusClasses[filterStatus]}`} aria-hidden="true" />
      <PipelineStep icon={Filter} title="用户过滤" value={refreshing ? '正在检查范围' : `${filteredSourceCount} 个通过`} status={filterStatus} />
      <span className={`${styles.connector} ${statusClasses[injectionStatus]}`} aria-hidden="true" />
      <PipelineStep icon={Sparkles} title="Prompt 注入" value={refreshing ? '正在生成内容' : injectedTokens ? `${formatTokenCount(injectedTokens)} tokens` : '尚未注入'} status={injectionStatus} />
    </div>
  </section>
}
