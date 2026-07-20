import { Check, CheckCircle2, ChevronDown, ChevronUp, Circle, ClipboardList, Info, LoaderCircle, Pencil, Play, RotateCcw, Square, X, XCircle } from 'lucide-react'
import type { PlanStepSummary, PlanSummary } from '../types/api'
import styles from './TaskPlanBubble.module.css'

export type TaskPlanStatus = 'pending' | 'approved' | 'paused' | 'running' | 'completed' | 'failed' | 'rejected' | 'cancelled'
export type TaskStepStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'

export interface TaskPlanStep { id: string; title: string; description?: string; dependency?: string | null; status?: TaskStepStatus }
export interface TaskPlanBubbleProps {
  title: string
  description?: string
  status: TaskPlanStatus
  steps: TaskPlanStep[]
  autoAccept?: boolean
  collapsed?: boolean
  className?: string
  onToggleCollapse?: () => void
  onReject?: () => void
  onModify?: () => void
  onApprove?: () => void
  onStop?: () => void
  onRetry?: () => void
}

const planLabels: Record<TaskPlanStatus, string> = { pending: '等待批准', approved: '已批准', paused: '已暂停', running: '执行中', completed: '已完成', failed: '执行失败', rejected: '已拒绝', cancelled: '已取消' }
const stepLabels: Record<TaskStepStatus, string> = { pending: '等待执行', running: '正在执行', completed: '已完成', failed: '执行失败', skipped: '已跳过', cancelled: '已取消' }
function cx(...names: Array<string | false | null | undefined>) { return names.filter(Boolean).join(' ') }
function icon(status: TaskStepStatus) {
  if (status === 'running') return <LoaderCircle size={16} className={styles.loadingIcon} />
  if (status === 'completed') return <Check size={16} />
  if (status === 'failed' || status === 'cancelled') return <X size={16} />
  if (status === 'skipped') return <Circle size={9} />
  return null
}
function notice(status: TaskPlanStatus, autoAccept: boolean, current?: TaskPlanStep) {
  if (status === 'pending') return autoAccept ? '当前 auto_accept 为 true，任务计划确认后将自动开始执行。' : '当前 auto_accept 为 false，需要手动批准后才会执行。'
  if (status === 'running') return current ? `正在执行：${current.title}` : '任务计划正在执行。'
  if (status === 'completed') return '任务计划中的全部步骤均已执行完成。'
  if (status === 'failed') return current ? `步骤“${current.title}”执行失败，请检查执行结果。` : '任务执行失败，请检查任务日志。'
  if (status === 'rejected' || status === 'cancelled') return '该任务计划已停止，不会继续执行。'
  return '任务计划已批准，等待运行时执行。'
}

export function TaskPlanBubble({ title, description = '已创建任务计划，请确认后执行以下步骤', status, steps, autoAccept = false, collapsed = false, className, onToggleCollapse, onReject, onModify, onApprove, onStop, onRetry }: TaskPlanBubbleProps) {
  const completed = steps.filter((step) => step.status === 'completed').length
  const current = steps.find((step) => step.status === 'running' || step.status === 'failed')
  const progress = steps.length ? Math.round(completed * 100 / steps.length) : 0
  const normalizedStatus: TaskPlanStatus = status === 'approved' ? 'pending' : status
  return <section className={cx(styles.taskPlanBubble, styles[`planStatus_${normalizedStatus}`], collapsed && styles.collapsed, className)} aria-label={`任务计划：${title}`}>
    <div className={styles.header}>
      <div className={styles.headerIdentity}><div className={styles.planIcon}><ClipboardList size={22} /></div><div className={styles.headerText}><h3 className={styles.title}>{title}</h3>{!collapsed && <p className={styles.description}>{description}</p>}</div></div>
      <div className={styles.headerMeta}><div className={cx(styles.statusPill, styles[`statusPill_${normalizedStatus}`])}><span>状态：</span><strong>{planLabels[status]}</strong></div><div className={styles.stepCountPill}>步骤数：{steps.length} 步</div>{onToggleCollapse && <button type="button" className={styles.iconButton} onClick={onToggleCollapse} aria-label={collapsed ? '展开任务计划' : '折叠任务计划'}>{collapsed ? <ChevronDown size={18} /> : <ChevronUp size={18} />}</button>}</div>
    </div>
    {collapsed ? <div className={styles.collapsedSummary}>{status === 'running' ? <><LoaderCircle size={17} className={styles.loadingIcon} /><span>正在执行{current ? `：${current.title}` : ''}</span><strong>{completed}/{steps.length}</strong></> : status === 'completed' ? <><CheckCircle2 size={17} /><span>任务计划已完成</span><strong>{completed}/{steps.length}</strong></> : status === 'failed' ? <><XCircle size={17} /><span>任务计划执行失败</span></> : <><Info size={17} /><span>{status === 'pending' ? '任务计划等待用户批准' : `任务计划${planLabels[status]}`}</span></>}</div> : <>
      {status === 'running' && <div className={styles.progressSection}><div className={styles.progressHeader}><span>执行进度 {completed}/{steps.length}</span><strong>{progress}%</strong></div><div className={styles.progressTrack}><div className={styles.progressBar} style={{ width: `${progress}%` }} /></div></div>}
      <div className={styles.stepList}>{steps.map((step, index) => { const stepStatus = step.status ?? 'pending'; return <div key={step.id} className={cx(styles.stepRow, styles[`stepStatus_${stepStatus}`])}><div className={styles.stepNumber}>{stepStatus === 'pending' ? index + 1 : icon(stepStatus)}</div><div className={styles.stepId}>{step.id}</div><div className={styles.stepContent}><div className={styles.stepTitle}>{step.title}</div>{step.description && <div className={styles.stepDescription}>{step.description}</div>}</div><div className={styles.stepMeta}>{step.dependency && <span className={styles.dependency}>依赖 {step.dependency}</span>}{stepStatus !== 'pending' && <span className={styles.stepStatusLabel}>{stepLabels[stepStatus]}</span>}</div></div> })}</div>
      <div className={cx(styles.notice, styles[`notice_${normalizedStatus}`])}><Info size={17} /><span>{notice(status, autoAccept, current)}</span></div>
      {status === 'pending' && <div className={styles.actionBar}><button type="button" className={cx(styles.actionButton, styles.rejectButton)} onClick={onReject}><X size={17} />拒绝</button><button type="button" className={cx(styles.actionButton, styles.modifyButton)} onClick={onModify}><Pencil size={16} />修改</button><button type="button" className={cx(styles.actionButton, styles.approveButton)} onClick={onApprove}><Play size={17} fill="currentColor" />批准执行</button></div>}
      {status === 'running' && <div className={cx(styles.actionBar, styles.singleActionBar)}><button type="button" className={cx(styles.actionButton, styles.stopButton)} onClick={onStop}><Square size={15} fill="currentColor" />停止任务</button></div>}
      {status === 'failed' && <div className={styles.actionBar}><button type="button" className={cx(styles.actionButton, styles.modifyButton)} onClick={onModify}><Pencil size={16} />修改计划</button><button type="button" className={cx(styles.actionButton, styles.approveButton)} onClick={onRetry}><RotateCcw size={17} />重新执行</button></div>}
    </>}
    <div className={styles.bottomPointer} aria-hidden="true" />
  </section>
}

export function taskPlanFromSummary(plan: PlanSummary): TaskPlanBubbleProps {
  return { title: plan.title, description: plan.description || undefined, status: plan.status as TaskPlanStatus, autoAccept: plan.auto_accept, steps: plan.steps.map((step: PlanStepSummary) => ({ id: step.step_id, title: step.title, description: step.description || undefined, dependency: step.depends_on.join(', ') || null, status: (step.status || 'pending') as TaskStepStatus })) }
}
