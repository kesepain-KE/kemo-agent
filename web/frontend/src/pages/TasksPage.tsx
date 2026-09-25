import { useEffect, useMemo, useState } from 'react'
import { Check, CheckCircle2, ChevronLeft, ChevronRight, CirclePause, ClipboardList, Eye, History, Pencil, Play, RotateCcw, Send, TimerReset, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { commandPlan, createCron, deleteCron, deletePlan, editPlan, getCron, getPlanRevision, getPlanRevisions, getTasks, retryPlanStep, rollbackPlan, updateCron } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatDateTime, MetricCard, ModuleError, ModuleFrame, RefreshActionButton, StatusChip } from '../components/ModuleUi'
import { PlanRevisionPanel } from '../components/PlanRevisionPanel'
import type { CronTaskSummary, PlanRevisionSnapshot, PlanRevisionSummary, PlanSummary, SessionsResponse } from '../types/api'
import styles from './TasksPage.module.css'
import themeStyles from './TasksPageTheme.module.css'

type TaskTab = 'plans' | 'cron' | 'history'
type ExecutionRecord = {
  key: string
  id: string
  title: string
  kind: 'plan' | 'cron'
  kindLabel: string
  status: string
  updatedAt: string
  plan?: PlanSummary
  cron?: CronTaskSummary
  result?: unknown
  error?: unknown
  durationMs?: number
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const TASK_PAGE_SIZE = 6
const inspectable = (plan: PlanSummary) => ['pending', 'approved', 'paused', 'completed', 'failed', 'cancelled'].includes(plan.status)

function parseTimestamp(value: string) {
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : Number.POSITIVE_INFINITY
}

function sortCronTasks(tasks: CronTaskSummary[]) {
  return [...tasks].sort((left, right) => {
    const leftTime = parseTimestamp(left.next_run_at)
    const rightTime = parseTimestamp(right.next_run_at)
    if (leftTime !== rightTime) return leftTime - rightTime
    const leftFallback = Date.parse(left.latest_run_at || left.created_at) || 0
    const rightFallback = Date.parse(right.latest_run_at || right.created_at) || 0
    if (leftFallback !== rightFallback) return rightFallback - leftFallback
    return left.task_id.localeCompare(right.task_id)
  })
}

function hasDetailValue(value: unknown) {
  if (value === null || value === undefined || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  if (typeof value === 'object') return Object.keys(value as Record<string, unknown>).length > 0
  return true
}

function DetailData({ value }: { value: unknown }) {
  return <pre className={styles.detailData}>{JSON.stringify(value, null, 2)}</pre>
}

function Pagination({ page, totalPages, totalItems, label, itemLabel, onChange }: { page: number; totalPages: number; totalItems: number; label: string; itemLabel: string; onChange: (page: number) => void }) {
  return <nav className={styles.pagination} aria-label={`${label}分页`}>
    <span>共 {totalItems} 个{itemLabel} · 每页最多 {TASK_PAGE_SIZE} 个</span>
    <div>
      <button type="button" aria-label={`上一页${label}`} disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft size={15} /></button>
      <strong>{page} / {totalPages}</strong>
      <button type="button" aria-label={`下一页${label}`} disabled={page >= totalPages} onClick={() => onChange(page + 1)}><ChevronRight size={15} /></button>
    </div>
  </nav>
}

function Progress({ plan }: { plan: PlanSummary }) {
  const completed = plan.progress.completed
  return <div className={styles.progress}><div className={styles.progressHead}><strong>整体进度</strong><span>{completed} / {plan.progress.total}{plan.status !== 'pending' && <b>{plan.progress.percent}%</b>}</span></div>{plan.status !== 'pending' && <div className={styles.progressTrack}><i style={{ width: `${plan.progress.percent}%` }} /></div>}</div>
}

function PlanCard({ plan, selected, onSelect, onModify, onPause, onRetryStep, onDelete }: { plan: PlanSummary; selected: boolean; onSelect: () => void; onModify: () => void; onPause: () => void; onRetryStep: (stepId: string) => void; onDelete: () => void }) {
  const canInspect = inspectable(plan)
  const retryableStep = plan.steps.find((step) => ['failed', 'cancelled'].includes(step.status))
  return <article className={`${styles.planCard} ${themeStyles.surface} ${selected ? styles.selected : ''} ${!canInspect ? styles.runningCard : ''}`} onClick={() => canInspect && onSelect()} role={canInspect ? 'button' : undefined} tabIndex={canInspect ? 0 : -1}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${plan.status === 'running' ? styles.runningAvatar : ''} ${plan.status === 'completed' ? styles.completedAvatar : ''}`}>{plan.status === 'running' ? 'R' : 'P'}</span><div><h3>{plan.title}</h3><p>{plan.plan_id}<span>·</span>更新于 {formatDateTime(plan.updated_at)}</p></div></div><div className={styles.planActions}><StatusChip status={plan.status} />{['pending', 'approved', 'paused', 'failed'].includes(plan.status) && <button type="button" onClick={(event) => { event.stopPropagation(); onModify() }}><Pencil size={14} />修改</button>}{['paused', 'failed'].includes(plan.status) && retryableStep && <button type="button" onClick={(event) => { event.stopPropagation(); onRetryStep(retryableStep.step_id) }}><RotateCcw size={14} />重试步骤</button>}{['pending', 'approved', 'paused'].includes(plan.status) && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}{plan.status === 'running' && <button type="button" onClick={(event) => { event.stopPropagation(); onPause() }}><CirclePause size={15} />暂停</button>}{TERMINAL_STATUSES.has(plan.status) && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <Progress plan={plan} />
    <div className={styles.stepChips}>{plan.steps.map((step, index) => <span key={step.step_id} className={`${styles.stepChip} ${step.status === 'running' ? styles.activeStep : ''} ${step.status === 'completed' ? styles.doneStep : ''}`}><b>{step.status === 'completed' ? <Check size={12} /> : index + 1}</b>{step.title}</span>)}</div>
  </article>
}

function PlanDetailPanel({ plan, historyOpen, revisions, selectedRevision, snapshot, historyLoading, historyError, historyFeedback, rollbackPending, onToggleHistory, onSelectRevision, onRollback }: { plan?: PlanSummary; historyOpen: boolean; revisions: PlanRevisionSummary[]; selectedRevision: number; snapshot?: PlanRevisionSnapshot; historyLoading: boolean; historyError: string; historyFeedback: string; rollbackPending: boolean; onToggleHistory: () => void; onSelectRevision: (revision: number) => void; onRollback: (revision: number) => void }) {
  return <aside className={`${styles.detailPanel} ${themeStyles.surface} ${themeStyles.detail} ${!plan ? themeStyles.emptyDetailPanel : ''}`}>
    <header><div><h2>计划详情</h2><p>查看计划说明、步骤约束与执行结果</p></div>{plan && <button type="button" className={styles.historyButton} aria-pressed={historyOpen} onClick={onToggleHistory}><History size={14} />历史版本</button>}</header>
    {!plan ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><ClipboardList size={25} /><strong>选择一个计划</strong><span>点击未运行或已完成的计划查看详情。</span></div> : <div className={styles.detailBody}>
      <div className={styles.detailTitle}><span className={styles.planAvatar}>P</span><strong>{plan.title}</strong><StatusChip status={plan.status} /></div>
      <dl>
        <div><dt>计划 ID</dt><dd>{plan.plan_id}</dd></div>
        <div><dt>状态</dt><dd><StatusChip status={plan.status} /></dd></div>
        <div><dt>整体进度</dt><dd>{plan.progress.completed} / {plan.progress.total} · {plan.progress.percent}%</dd></div>
        <div><dt>当前步骤</dt><dd>{plan.current_step || '—'}</dd></div>
        <div><dt>对话来源</dt><dd>{plan.source || '—'} / {plan.session_id || '—'}</dd></div>
        <div><dt>当前版本</dt><dd>revision {plan.revision}</dd></div>
        <div><dt>自动接受</dt><dd>{plan.auto_accept ? '已开启' : '已关闭'}</dd></div>
        <div><dt>创建时间</dt><dd>{formatDateTime(plan.created_at)}</dd></div>
        <div><dt>更新时间</dt><dd>{formatDateTime(plan.updated_at)}</dd></div>
      </dl>
      <section><h3>计划描述</h3><p>{plan.description || '暂无描述'}</p></section>
      {plan.reminder && <section><h3>执行提醒</h3><p>{plan.reminder}</p></section>}
      <section><h3>步骤详情</h3><div className={styles.detailSteps}>{plan.steps.map((step, index) => <article className={styles.detailStepCard} key={step.step_id}>
        <header><b>{index + 1}</b><strong>{step.title}</strong><StatusChip status={step.status} /></header>
        <p>{step.description || '暂无步骤说明'}</p>
        <dl className={styles.stepMetadata}>
          <div><dt>步骤 ID</dt><dd>{step.step_id}</dd></div>
          <div><dt>前置依赖</dt><dd>{step.depends_on.length ? step.depends_on.join('、') : '无'}</dd></div>
          <div><dt>建议工具</dt><dd>{step.tool_name || '自动判断'}</dd></div>
          <div><dt>关键步骤</dt><dd>{step.critical ? '是' : '否'}</dd></div>
          <div><dt>开始时间</dt><dd>{formatDateTime(step.started_at)}</dd></div>
          <div><dt>完成时间</dt><dd>{formatDateTime(step.finished_at)}</dd></div>
        </dl>
        {hasDetailValue(step.tool_arguments) && <div className={styles.stepPayload}><span>工具参数</span><DetailData value={step.tool_arguments} /></div>}
        {hasDetailValue(step.result) && <div className={styles.stepPayload}><span>执行结果</span><DetailData value={step.result} /></div>}
        {hasDetailValue(step.error) && <div className={`${styles.stepPayload} ${styles.errorPayload}`}><span>错误信息</span><DetailData value={step.error} /></div>}
      </article>)}</div></section>
      <PlanRevisionPanel open={historyOpen} revisions={revisions} selectedRevision={selectedRevision} snapshot={snapshot} loading={historyLoading} error={historyError} feedback={historyFeedback} rollbackPending={rollbackPending} rollbackAllowed={['pending', 'paused', 'failed'].includes(plan.status)} onSelect={onSelectRevision} onRollback={onRollback} />
    </div>}
  </aside>
}

function scheduleLabel(task: CronTaskSummary) {
  const times = task.times?.length ? task.times.join('、') : task.time || '—'
  const range = task.start_date || task.end_date ? ` · ${task.start_date || '不限'} 至 ${task.end_date || '不限'}` : ''
  const count = task.max_runs ? ` · ${task.successful_runs || 0}/${task.max_runs} 次` : ''
  if (task.type === 'daily') return `每天 ${times}${range}${count}`
  if (task.type === 'weekly') return `每周 ${task.weekdays?.map((day) => `周${'一二三四五六日'[day - 1]}`).join('、') || '—'} ${times}${range}${count}`
  if (task.type === 'monthly') return `每月 ${task.month_days?.map((day) => `${day} 日`).join('、') || '—'} ${times}${range}${count}`
  if (task.type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (task.type === 'recurring') {
    const seconds = Number(task.interval_seconds || 0)
    return `${seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.max(1, Math.round(seconds / 60))} 分钟`}${range}${count}`
  }
  return '未配置调度'
}

function parseNumberList(value: string) {
  return [...new Set(value.split(/[,，\s]+/).map((item) => Number(item)).filter((item) => Number.isInteger(item)))].sort((a, b) => a - b)
}

function promptCronDraft(task?: CronTaskSummary): Record<string, unknown> | null {
  const title = window.prompt('定时任务标题', task?.title || '')
  if (title === null) return null
  const prompt = window.prompt(task ? '新的执行内容（留空表示保留原内容）' : '执行内容（请写成每次运行都能独立理解的完整要求）', '')
  if (prompt === null) return null
  const type = window.prompt('任务类型：once / daily / weekly / monthly / recurring', task?.type || 'daily')?.trim()
  if (!type) return null
  const body: Record<string, unknown> = { title, type }
  if (!task || prompt.trim()) body.prompt = prompt
  if (type === 'once') {
    const next = window.prompt('单次执行时间（北京时间 ISO，例如 2026-12-31T09:00:00+08:00）', task?.next_run_at || '')
    if (next === null) return null
    const normalizedNext = next.trim()
    if (!normalizedNext) {
      window.alert('单次定时任务必须填写执行时间。')
      return null
    }
    body.next_run_at = normalizedNext
  } else if (type === 'recurring') {
    const interval = window.prompt('重复间隔秒数（至少 60）', String(task?.interval_seconds || 3600))
    if (interval === null) return null
    const intervalSeconds = Number(interval.trim())
    if (!Number.isSafeInteger(intervalSeconds) || intervalSeconds < 60) {
      window.alert('重复间隔必须是至少 60 秒的整数。')
      return null
    }
    body.interval_seconds = intervalSeconds
  } else {
    const times = window.prompt('执行时刻，可填写一个或多个 HH:MM，多个用逗号分隔', task?.times?.join(',') || task?.time || '09:00')
    if (times === null) return null
    const values = [...new Set(times.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean))].sort()
    if (values.length === 1) body.time = values[0]
    else body.times = values
    if (type === 'weekly') {
      const weekdays = window.prompt('星期（1=周一，7=周日），多个用逗号分隔', task?.weekdays?.join(',') || '1')
      if (weekdays === null) return null
      body.weekdays = parseNumberList(weekdays)
    }
    if (type === 'monthly') {
      const monthDays = window.prompt('每月日期（1-31），多个用逗号分隔；不存在的日期会跳过', task?.month_days?.join(',') || '1')
      if (monthDays === null) return null
      body.month_days = parseNumberList(monthDays)
    }
  }
  if (type !== 'once') {
    const startDate = window.prompt('生效日期（YYYY-MM-DD，留空表示立即生效）', task?.start_date || '')
    if (startDate === null) return null
    const endDate = window.prompt('结束日期（YYYY-MM-DD，首尾包含；留空表示长期有效）', task?.end_date || '')
    if (endDate === null) return null
    const maxRuns = window.prompt('最大成功执行次数（留空表示不限）', task?.max_runs ? String(task.max_runs) : '')
    if (maxRuns === null) return null
    body.start_date = startDate.trim()
    body.end_date = endDate.trim()
    const normalizedMaxRuns = maxRuns.trim()
    if (!normalizedMaxRuns) {
      body.max_runs = null
    } else {
      const maxRunCount = Number(normalizedMaxRuns)
      if (!Number.isSafeInteger(maxRunCount) || maxRunCount < 1) {
        window.alert('最大成功执行次数必须是大于等于 1 的整数，或留空表示不限。')
        return null
      }
      body.max_runs = maxRunCount
    }
  }
  return body
}

function CronCard({ task, selected, onSelect, onTogglePause, onEdit, onRetry, onDelete }: { task: CronTaskSummary; selected: boolean; onSelect: () => void; onTogglePause: () => void; onEdit: () => void; onRetry: () => void; onDelete: () => void }) {
  const running = task.status === 'running'
  const paused = task.status === 'paused'
  const terminal = TERMINAL_STATUSES.has(task.status)
  return <article className={`${styles.planCard} ${themeStyles.surface} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${running ? styles.runningAvatar : ''} ${terminal ? styles.completedAvatar : ''}`}>C</span><div><h3>{task.title}</h3><p>{task.task_id}<span>·</span>创建于 {formatDateTime(task.created_at)}</p></div></div><div className={styles.planActions}><StatusChip status={task.status} />{task.user_defined && !running && !['completed', 'cancelled'].includes(task.status) && <button type="button" onClick={(event) => { event.stopPropagation(); onEdit() }}><Pencil size={14} />编辑</button>}{task.status === 'failed' && <button type="button" onClick={(event) => { event.stopPropagation(); onRetry() }}><RotateCcw size={14} />重新启用</button>}{!terminal && <button type="button" onClick={(event) => { event.stopPropagation(); onTogglePause() }}>{paused ? <><RotateCcw size={14} />恢复</> : <><CirclePause size={14} />暂停</>}</button>}{!running && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <div className={styles.cronFacts}><div><span>调度规则</span><strong>{scheduleLabel(task)}</strong></div><div><span>最近运行</span><strong>{formatDateTime(task.latest_run_at)}</strong></div><div><span>下次运行</span><strong>{formatDateTime(task.next_run_at)}</strong></div></div>
  </article>
}

function CronDetailPanel({ task, loading, error }: { task?: CronTaskSummary; loading: boolean; error: string }) {
  return <aside className={`${styles.detailPanel} ${themeStyles.surface} ${themeStyles.detail} ${!task ? themeStyles.emptyDetailPanel : ''}`}>
    <header><div><h2>定时任务详情</h2><p>查看执行内容、调度配置和运行范围</p></div>{task && <span className={styles.readonlyBadge}><Eye size={13} />详情</span>}</header>
    {!task ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><TimerReset size={25} /><strong>选择一个定时任务</strong><span>点击左侧任务查看完整调度信息。</span></div> : <div className={styles.detailBody}>
      <div className={styles.detailTitle}><span className={styles.planAvatar}>C</span><strong>{task.title}</strong><StatusChip status={task.status} /></div>
      <dl>
        <div><dt>任务 ID</dt><dd>{task.task_id}</dd></div>
        <div><dt>状态</dt><dd><StatusChip status={task.status} /></dd></div>
        <div><dt>调度类型</dt><dd>{task.type}</dd></div>
        <div><dt>调度规则</dt><dd>{scheduleLabel(task)}</dd></div>
        <div><dt>生效范围</dt><dd>{task.start_date || '不限'} 至 {task.end_date || '不限'}</dd></div>
        <div><dt>成功次数</dt><dd>{task.successful_runs || 0}{task.max_runs ? ` / ${task.max_runs}` : ' / 不限'}</dd></div>
        <div><dt>最近运行</dt><dd>{formatDateTime(task.latest_run_at)}</dd></div>
        <div><dt>下次运行</dt><dd>{formatDateTime(task.next_run_at)}</dd></div>
        <div><dt>最近状态</dt><dd>{task.last_state || '—'}</dd></div>
        <div><dt>执行模式</dt><dd>{task.exec_mode || (task.user_defined ? 'agent' : 'system')}</dd></div>
        <div><dt>创建时间</dt><dd>{formatDateTime(task.created_at)}</dd></div>
      </dl>
      <section><h3>执行内容</h3>{loading ? <p>正在加载任务详情…</p> : error ? <p className={styles.detailError}>{error}</p> : task.user_defined ? <p className={styles.promptDetail}>{task.prompt || '暂无执行内容'}</p> : <p>系统内置任务不回显内部执行内容。</p>}</section>
      <section><h3>任务来源</h3><p>{task.user_defined ? '当前用户创建的定时任务；执行内容仅在选中任务后按需加载，凭据样内容会自动脱敏。' : '系统内置定时任务'}</p></section>
    </div>}
    <footer><Eye size={15} />用户任务可从左侧卡片直接编辑；系统任务保持只读</footer>
  </aside>
}

function ExecutionCard({ record, selected, onSelect, onDelete }: { record: ExecutionRecord; selected: boolean; onSelect: () => void; onDelete?: () => void }) {
  return <article className={`${styles.planCard} ${themeStyles.surface} ${styles.executionCard} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><div><h3>{record.title}</h3><p>{record.kindLabel}<span>·</span>{record.id}</p></div></div><div className={styles.planActions}><StatusChip status={record.status} />{onDelete && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <div className={styles.executionSummary}><span>完成时间</span><strong>{formatDateTime(record.updatedAt)}</strong></div>
  </article>
}

function ExecutionDetailPanel({ record }: { record?: ExecutionRecord }) {
  return <aside className={`${styles.detailPanel} ${themeStyles.surface} ${themeStyles.detail} ${!record ? themeStyles.emptyDetailPanel : ''}`}><header><div><h2>执行记录查看</h2><p>查看任务计划终态与每次 Cron 执行结果</p></div>{record && <span className={styles.readonlyBadge}><Eye size={13} />查看</span>}</header>{!record ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><CheckCircle2 size={25} /><strong>选择一条执行记录</strong><span>点击左侧记录查看只读详情。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><strong>{record.title}</strong><StatusChip status={record.status} /></div><dl><div><dt>记录类型</dt><dd>{record.kindLabel}</dd></div><div><dt>任务 ID</dt><dd>{record.id}</dd></div><div><dt>执行状态</dt><dd><StatusChip status={record.status} /></dd></div>{record.durationMs !== undefined && <div><dt>执行耗时</dt><dd>{record.durationMs} ms</dd></div>}<div><dt>执行时间</dt><dd>{formatDateTime(record.updatedAt)}</dd></div></dl>{record.plan && <><section><h3>任务描述</h3><p>{record.plan.description || '暂无描述'}</p></section><section><h3>步骤结果</h3><div className={styles.detailSteps}>{record.plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span><StatusChip status={step.status} /></div>)}</div></section></>}{record.cron && <section><h3>调度信息</h3><p>{scheduleLabel(record.cron)} · 最近运行 {formatDateTime(record.cron.latest_run_at)}</p></section>}{Boolean(record.error) && <section><h3>错误信息</h3><pre>{JSON.stringify(record.error, null, 2)}</pre></section>}{Boolean(record.result) && <section><h3>结果摘要</h3><pre>{JSON.stringify(record.result, null, 2)}</pre></section>}</div>}<footer><Eye size={15} />执行日志只读保存，不会因删除任务定义而自动删除</footer></aside>
}

export function TasksPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [tab, setTab] = useState<TaskTab>('plans')
  const [selectedPlanId, setSelectedPlanId] = useState('')
  const [planPage, setPlanPage] = useState(1)
  const [selectedCronId, setSelectedCronId] = useState('')
  const [cronPage, setCronPage] = useState(1)
  const [selectedHistoryKey, setSelectedHistoryKey] = useState('')
  const [executionPage, setExecutionPage] = useState(1)
  const [historyPlanId, setHistoryPlanId] = useState('')
  const [selectedRevision, setSelectedRevision] = useState(0)
  const [historyFeedback, setHistoryFeedback] = useState('')
  const [planMutationFeedback, setPlanMutationFeedback] = useState('')
  const [cronMutationFeedback, setCronMutationFeedback] = useState('')
  const query = useQuery({
    queryKey: ['tasks', user],
    queryFn: () => getTasks(user),
    enabled: Boolean(user),
    refetchInterval: (state) => state.state.data?.plans.some((plan) => ['approved', 'running'].includes(plan.status)) ? 1200 : false,
  })
  const data = query.data
  const refresh = () => client.invalidateQueries({ queryKey: ['tasks', user] })
  const planPause = useMutation({
    mutationFn: (plan: PlanSummary) => commandPlan(user, plan.plan_id, 'pause', plan.session_id, plan.source || 'web'),
    onSuccess: refresh,
  })
  const planDelete = useMutation({
    mutationFn: (plan: PlanSummary) => deletePlan(user, plan.plan_id, plan.session_id, plan.source || 'web'),
    onSuccess: refresh,
  })
  const cronUpdate = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => updateCron(user, id, body),
    onSuccess: async (_response, variables) => {
      setCronMutationFeedback('定时任务已更新。')
      await Promise.all([
        refresh(),
        client.invalidateQueries({ queryKey: ['cron-detail', user, variables.id] }),
      ])
    },
    onError: (error) => setCronMutationFeedback(error instanceof Error ? error.message : '定时任务更新失败。'),
  })
  const cronCreate = useMutation({
    mutationFn: (body: Record<string, unknown>) => createCron(user, body),
    onSuccess: async () => {
      setCronMutationFeedback('定时任务已创建。')
      setTab('cron')
      await refresh()
    },
    onError: (error) => setCronMutationFeedback(error instanceof Error ? error.message : '定时任务创建失败。'),
  })
  const cronDelete = useMutation({
    mutationFn: (id: string) => deleteCron(user, id),
    onSuccess: async () => { setCronMutationFeedback('定时任务已删除。'); await refresh() },
    onError: (error) => setCronMutationFeedback(error instanceof Error ? error.message : '定时任务删除失败。'),
  })
  const plans = data?.plans || []
  const cronTasks = data?.cron_tasks || []
  const planTotalPages = Math.max(1, Math.ceil(plans.length / TASK_PAGE_SIZE))
  const visiblePlans = plans.slice((planPage - 1) * TASK_PAGE_SIZE, planPage * TASK_PAGE_SIZE)
  const sortedCronTasks = useMemo(() => sortCronTasks(cronTasks), [cronTasks])
  const cronTotalPages = Math.max(1, Math.ceil(sortedCronTasks.length / TASK_PAGE_SIZE))
  const visibleCronTasks = sortedCronTasks.slice((cronPage - 1) * TASK_PAGE_SIZE, cronPage * TASK_PAGE_SIZE)
  const inspectablePlans = plans.filter(inspectable)
  const latestSessionId = client.getQueryData<SessionsResponse>(['sessions', user])?.sessions[0]?.session_id || ''
  const historyPlan = plans.find((plan) => plan.plan_id === historyPlanId)
  const revisionsQuery = useQuery({
    queryKey: ['plan-revisions', user, historyPlanId],
    queryFn: () => getPlanRevisions(user, historyPlan!.plan_id, historyPlan!.session_id),
    enabled: Boolean(user && historyPlan),
  })
  const revisionQuery = useQuery({
    queryKey: ['plan-revision', user, historyPlanId, selectedRevision],
    queryFn: () => getPlanRevision(user, historyPlan!.plan_id, selectedRevision, historyPlan!.session_id),
    enabled: Boolean(user && historyPlan && selectedRevision > 0),
  })
  const cronDetailQuery = useQuery({
    queryKey: ['cron-detail', user, selectedCronId],
    queryFn: () => getCron(user, selectedCronId),
    enabled: Boolean(user && selectedCronId),
  })
  const rollbackMutation = useMutation({
    mutationFn: ({ plan, revision }: { plan: PlanSummary; revision: number }) => rollbackPlan(user, plan.plan_id, revision, plan.revision, plan.session_id),
    onSuccess: async (response) => {
      setHistoryFeedback(`已回滚到 revision ${response.target_revision}，并生成 revision ${response.plan.revision}`)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['tasks', user] }),
        client.invalidateQueries({ queryKey: ['plan-revisions', user, response.plan_id] }),
      ])
    },
    onError: (error) => setHistoryFeedback(error instanceof Error ? error.message : '计划回滚失败'),
  })
  const history = useMemo<ExecutionRecord[]>(() => [
    ...plans.filter((item) => TERMINAL_STATUSES.has(item.status)).map((item) => ({ key: `plan:${item.plan_id}`, id: item.plan_id, title: item.title, kind: 'plan' as const, kindLabel: '任务计划', status: item.status, updatedAt: item.updated_at, plan: item })),
    ...(data?.executions || []).filter((item) => item.kind === 'cron' && item.user_defined !== false).map((item, index) => ({
      key: `cron:${item.record_id || item.task_id}:${item.updated_at}:${index}`,
      id: item.task_id,
      title: item.title || item.task_id,
      kind: 'cron' as const,
      kindLabel: '定时任务执行',
      status: item.status,
      updatedAt: item.updated_at,
      cron: cronTasks.find((task) => task.task_id === item.task_id),
      result: item.result,
      error: item.error,
      durationMs: item.duration_ms,
    })),
  ].sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0)), [cronTasks, data?.executions, plans])
  const executionTotalPages = Math.max(1, Math.ceil(history.length / TASK_PAGE_SIZE))
  const visibleHistory = history.slice((executionPage - 1) * TASK_PAGE_SIZE, executionPage * TASK_PAGE_SIZE)

  useEffect(() => { setPlanPage((current) => Math.min(current, planTotalPages)) }, [planTotalPages])
  useEffect(() => {
    if (!visiblePlans.some((plan) => plan.plan_id === selectedPlanId)) {
      setSelectedPlanId(visiblePlans.find(inspectable)?.plan_id || '')
    }
  }, [selectedPlanId, visiblePlans])
  useEffect(() => { if (!sortedCronTasks.some((task) => task.task_id === selectedCronId)) setSelectedCronId(sortedCronTasks[0]?.task_id || '') }, [sortedCronTasks, selectedCronId])
  useEffect(() => { setCronPage((current) => Math.min(current, cronTotalPages)) }, [cronTotalPages])
  useEffect(() => {
    const selectedIndex = sortedCronTasks.findIndex((task) => task.task_id === selectedCronId)
    if (selectedIndex >= 0) setCronPage(Math.floor(selectedIndex / TASK_PAGE_SIZE) + 1)
  }, [selectedCronId, sortedCronTasks])
  useEffect(() => { setExecutionPage((current) => Math.min(current, executionTotalPages)) }, [executionTotalPages])
  useEffect(() => {
    if (!visibleHistory.some((record) => record.key === selectedHistoryKey)) {
      setSelectedHistoryKey(visibleHistory[0]?.key || '')
    }
  }, [selectedHistoryKey, visibleHistory])
  useEffect(() => {
    const revisions = revisionsQuery.data?.revisions || []
    if (revisions.length && !revisions.some((item) => item.revision === selectedRevision)) setSelectedRevision(revisions[0].revision)
  }, [revisionsQuery.data, selectedRevision])
  useEffect(() => {
    if (historyPlanId && !plans.some((plan) => plan.plan_id === historyPlanId)) {
      setHistoryPlanId('')
      setSelectedRevision(0)
    }
  }, [historyPlanId, plans])

  const selectedPlan = inspectablePlans.find((plan) => plan.plan_id === selectedPlanId)
  const selectedCronSummary = sortedCronTasks.find((task) => task.task_id === selectedCronId)
  const selectedCron = cronDetailQuery.data?.cron_task.task_id === selectedCronId
    ? { ...selectedCronSummary, ...cronDetailQuery.data.cron_task }
    : selectedCronSummary
  const selectedHistory = history.find((record) => record.key === selectedHistoryKey)
  const changePlanPage = (nextPage: number) => {
    const normalizedPage = Math.min(planTotalPages, Math.max(1, nextPage))
    const firstInspectable = plans
      .slice((normalizedPage - 1) * TASK_PAGE_SIZE, normalizedPage * TASK_PAGE_SIZE)
      .find(inspectable)
    setPlanPage(normalizedPage)
    setSelectedPlanId(firstInspectable?.plan_id || '')
  }
  const changeCronPage = (nextPage: number) => {
    const normalizedPage = Math.min(cronTotalPages, Math.max(1, nextPage))
    setCronPage(normalizedPage)
    setSelectedCronId(sortedCronTasks[(normalizedPage - 1) * TASK_PAGE_SIZE]?.task_id || '')
  }
  const changeExecutionPage = (nextPage: number) => {
    const normalizedPage = Math.min(executionTotalPages, Math.max(1, nextPage))
    setExecutionPage(normalizedPage)
    setSelectedHistoryKey(history[(normalizedPage - 1) * TASK_PAGE_SIZE]?.key || '')
  }
  const conversationUrl = (prompt: string, sessionId = latestSessionId) => `/chat?user=${encodeURIComponent(user)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}&prompt=${encodeURIComponent(prompt)}`
  const modify = async (plan: PlanSummary) => {
    const title = window.prompt('修改计划标题', plan.title)
    if (title === null) return
    const description = window.prompt('修改计划描述', plan.description)
    if (description === null) return
    const stepPatchText = window.prompt(
      '可选：输入步骤修正 JSON 数组（支持 step_id、tool_name、tool_arguments、depends_on、critical；留空则不修改步骤）',
      '',
    )
    if (stepPatchText === null) return
    const fallbackPrompt = `请修改任务计划：${plan.title}（${plan.plan_id}）。新标题：${title}。新描述：${description}${stepPatchText.trim() ? `。步骤修改：${stepPatchText}` : ''}`
    let steps: unknown
    if (stepPatchText.trim()) {
      try {
        steps = JSON.parse(stepPatchText)
        if (!Array.isArray(steps)) throw new Error('步骤修改必须是数组')
      } catch {
        navigate(conversationUrl(fallbackPrompt, plan.session_id || latestSessionId))
        return
      }
    }
    try {
      const response = await editPlan(user, plan.plan_id, {
        revision: plan.revision,
        title,
        description,
        ...(steps ? { steps } : {}),
      }, plan.session_id, plan.source || 'web')
      setPlanMutationFeedback(response.activated
        ? '计划已修正并自动恢复执行。'
        : response.reason === 'fix_incomplete'
          ? '计划修改已保存，仍有未修正的失败步骤。'
          : '计划已修正；当前未自动激活，等待用户继续。')
      await refresh()
    } catch {
      navigate(conversationUrl(fallbackPrompt, plan.session_id || latestSessionId))
    }
  }
  const retryStep = async (plan: PlanSummary, stepId: string) => {
    try {
      const response = await retryPlanStep(user, plan.plan_id, stepId, plan.revision, plan.session_id, plan.source || 'web')
      setPlanMutationFeedback(response.activated
        ? '失败步骤已重置，计划已自动恢复执行。'
        : response.reason === 'fix_incomplete'
          ? '当前步骤已重置，仍有其他失败步骤需要修正。'
          : '失败步骤已重置，计划等待继续。')
      await refresh()
    } catch {
      navigate(conversationUrl(`请修正并重试任务计划 ${plan.plan_id} 的步骤 ${stepId}`, plan.session_id || latestSessionId))
    }
  }
  const toggleHistory = (plan: PlanSummary) => {
    if (historyPlanId === plan.plan_id) {
      setHistoryPlanId('')
      setSelectedRevision(0)
      setHistoryFeedback('')
      return
    }
    setHistoryPlanId(plan.plan_id)
    setSelectedRevision(0)
    setHistoryFeedback('')
  }
  const rollbackRevision = (plan: PlanSummary, revision: number) => {
    if (!window.confirm(`确定回滚到 revision ${revision}？回滚会生成一个新版本，现有历史不会被覆盖。`)) return
    setHistoryFeedback('')
    rollbackMutation.mutate({ plan, revision })
  }
  const removePlan = (plan: PlanSummary) => { if (window.confirm(`删除任务计划“${plan.title}”？`)) planDelete.mutate(plan) }
  const removeCron = (task: CronTaskSummary) => { if (window.confirm(`删除定时任务“${task.title}”？`)) cronDelete.mutate(task.task_id) }
  const addCron = () => {
    setCronMutationFeedback('')
    const body = promptCronDraft()
    if (body) cronCreate.mutate(body)
  }
  const editCronTask = (task: CronTaskSummary) => {
    setCronMutationFeedback('')
    const body = promptCronDraft(task)
    if (body) cronUpdate.mutate({ id: task.task_id, body })
  }
  const removeHistory = (record: ExecutionRecord) => {
    if (!window.confirm(`删除执行记录“${record.title}”？`)) return
    if (record.kind === 'plan' && record.plan) planDelete.mutate(record.plan)
    else return
  }

  return <ModuleFrame kicker="Task Orchestration" title="任务中枢" description="统一查看计划任务、周期调度和每次执行结果；定时任务可直接创建和编辑。" actions={<><RefreshActionButton pending={query.isFetching} label="刷新状态" pendingLabel="刷新中…" onClick={() => { void query.refetch() }} /><button className="module-btn" onClick={addCron} disabled={cronCreate.isPending}><TimerReset size={15} />{cronCreate.isPending ? '创建中…' : '新建定时任务'}</button><button className="module-btn primary" onClick={() => navigate(conversationUrl('请根据我的目标创建一份任务计划'))}><Send size={15} />通过对话创建计划</button></>}>
    {query.isError && <ModuleError />}
    <section className={styles.stats}><MetricCard label="活动计划" value={plans.length} detail="总创建计划" symbol={<ClipboardList size={16} />} /><MetricCard label="执行中" value={plans.filter((plan) => plan.status === 'running').length} detail="正在执行" symbol={<Play size={16} />} /><MetricCard label="已完成" value={plans.filter((plan) => plan.status === 'completed').length} detail="已完成计划" symbol={<CheckCircle2 size={16} />} tone="success" /></section>
    <div className="module-toolbar"><div className="module-tabs">{(['plans', 'cron', 'history'] as const).map((item) => <button key={item} className={`module-tab-btn ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'plans' ? '任务计划' : item === 'cron' ? '定时任务' : '执行记录'}</button>)}</div><div className="toolbar-spacer" /></div>
    {tab === 'plans' && planMutationFeedback && <div className={styles.mutationFeedback} role="status">{planMutationFeedback}</div>}
    {tab === 'cron' && cronMutationFeedback && <div className={styles.mutationFeedback} role="status">{cronMutationFeedback}</div>}
    {tab === 'plans' && <div className={styles.planLayout}>
      <main className={`${styles.listPanel} ${themeStyles.surface}`} aria-label="任务计划列表">
        <header className={styles.listPanelHeader}><div><h2>任务计划</h2><p>集中查看计划状态、步骤进度与版本详情</p></div><strong>{plans.length}</strong></header>
        <div className={`${styles.planList} ${plans.length ? styles.populatedList : ''}`} aria-label="任务计划卡片">
          {plans.length ? visiblePlans.map((plan) => <PlanCard key={plan.plan_id} plan={plan} selected={plan.plan_id === selectedPlanId} onSelect={() => setSelectedPlanId(plan.plan_id)} onModify={() => { void modify(plan) }} onPause={() => planPause.mutate(plan)} onRetryStep={(stepId) => { void retryStep(plan, stepId) }} onDelete={() => removePlan(plan)} />) : <EmptyPanel title="暂无任务计划" description="在对话中描述目标，kemo-agent 会生成计划草案并等待确认。" icon={<ClipboardList size={21} />} />}
        </div>
        <Pagination page={planPage} totalPages={planTotalPages} totalItems={plans.length} label="任务计划" itemLabel="计划" onChange={changePlanPage} />
      </main>
      <PlanDetailPanel plan={selectedPlan} historyOpen={Boolean(selectedPlan && historyPlanId === selectedPlan.plan_id)} revisions={revisionsQuery.data?.revisions || []} selectedRevision={selectedRevision} snapshot={revisionQuery.data?.plan} historyLoading={revisionsQuery.isLoading || revisionQuery.isLoading} historyError={(revisionsQuery.error || revisionQuery.error) instanceof Error ? String((revisionsQuery.error || revisionQuery.error)?.message || '') : ''} historyFeedback={historyFeedback} rollbackPending={rollbackMutation.isPending} onToggleHistory={() => selectedPlan && toggleHistory(selectedPlan)} onSelectRevision={(revision) => { setSelectedRevision(revision); setHistoryFeedback('') }} onRollback={(revision) => selectedPlan && rollbackRevision(selectedPlan, revision)} />
    </div>}
    {tab === 'cron' && <div className={styles.planLayout}>
      <main className={`${styles.listPanel} ${themeStyles.surface}`} aria-label="定时任务列表">
        <header className={styles.listPanelHeader}><div><h2>定时任务</h2><p>按下次执行时间由近到远排列，未排期任务置后</p></div><strong>{sortedCronTasks.length}</strong></header>
        <div className={`${styles.planList} ${sortedCronTasks.length ? styles.populatedList : ''}`} aria-label="定时任务卡片">
          {sortedCronTasks.length ? visibleCronTasks.map((task) => <CronCard key={task.task_id} task={task} selected={task.task_id === selectedCronId} onSelect={() => setSelectedCronId(task.task_id)} onEdit={() => editCronTask(task)} onRetry={() => cronUpdate.mutate({ id: task.task_id, body: { status: 'enabled' } })} onTogglePause={() => cronUpdate.mutate({ id: task.task_id, body: { status: task.status === 'paused' ? 'enabled' : 'paused' } })} onDelete={() => removeCron(task)} />) : <EmptyPanel title="暂无定时任务" description="当前用户没有可显示的定时任务，可从页面顶部直接创建。" icon={<TimerReset size={21} />} />}
        </div>
        <Pagination page={cronPage} totalPages={cronTotalPages} totalItems={sortedCronTasks.length} label="定时任务" itemLabel="任务" onChange={changeCronPage} />
      </main>
      <CronDetailPanel task={selectedCron} loading={cronDetailQuery.isFetching} error={cronDetailQuery.error instanceof Error ? cronDetailQuery.error.message : ''} />
    </div>}
    {tab === 'history' && <div className={styles.planLayout}>
      <main className={`${styles.listPanel} ${themeStyles.surface}`} aria-label="执行记录列表">
        <header className={styles.listPanelHeader}><div><h2>执行记录</h2><p>按最近执行时间由新到旧排列，集中查看终态结果</p></div><strong>{history.length}</strong></header>
        <div className={`${styles.planList} ${history.length ? styles.populatedList : ''}`} aria-label="执行记录卡片">
          {history.length ? visibleHistory.map((record) => <ExecutionCard key={record.key} record={record} selected={record.key === selectedHistoryKey} onSelect={() => setSelectedHistoryKey(record.key)} onDelete={record.kind === 'plan' ? () => removeHistory(record) : undefined} />) : <EmptyPanel title="暂无执行记录" description="任务计划终态和用户定时任务的每次执行都会出现在这里。" icon={<CheckCircle2 size={21} />} />}
        </div>
        <Pagination page={executionPage} totalPages={executionTotalPages} totalItems={history.length} label="执行记录" itemLabel="记录" onChange={changeExecutionPage} />
      </main>
      <ExecutionDetailPanel record={selectedHistory} />
    </div>}
  </ModuleFrame>
}
