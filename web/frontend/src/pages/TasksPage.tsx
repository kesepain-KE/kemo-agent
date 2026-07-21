import { useEffect, useMemo, useState } from 'react'
import { Check, CheckCircle2, CirclePause, ClipboardList, Eye, Info, Pencil, Play, RefreshCw, RotateCcw, Send, TimerReset, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { deleteCron, deletePlan, getTasks, updateCron, updatePlan } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatDateTime, MetricCard, ModuleError, ModuleFrame, StatusChip } from '../components/ModuleUi'
import type { CronTaskSummary, PlanSummary, SessionsResponse } from '../types/api'
import styles from './TasksPage.module.css'

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
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const inspectable = (plan: PlanSummary) => ['pending', 'approved', 'completed', 'cancelled'].includes(plan.status)

function Progress({ plan }: { plan: PlanSummary }) {
  const completed = plan.progress.completed
  return <div className={styles.progress}><div className={styles.progressHead}><strong>整体进度</strong><span>{completed} / {plan.progress.total}{plan.status !== 'pending' && <b>{plan.progress.percent}%</b>}</span></div>{plan.status !== 'pending' && <div className={styles.progressTrack}><i style={{ width: `${plan.progress.percent}%` }} /></div>}</div>
}

function PlanCard({ plan, selected, onSelect, onModify, onPause, onDelete }: { plan: PlanSummary; selected: boolean; onSelect: () => void; onModify: () => void; onPause: () => void; onDelete: () => void }) {
  const canInspect = inspectable(plan)
  return <article className={`${styles.planCard} ${selected ? styles.selected : ''} ${!canInspect ? styles.runningCard : ''}`} onClick={() => canInspect && onSelect()} role={canInspect ? 'button' : undefined} tabIndex={canInspect ? 0 : -1}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${plan.status === 'running' ? styles.runningAvatar : ''} ${plan.status === 'completed' ? styles.completedAvatar : ''}`}>{plan.status === 'running' ? 'R' : 'P'}</span><div><h3>{plan.title}</h3><p>{plan.plan_id}<span>·</span>更新于 {formatDateTime(plan.updated_at)}</p></div></div><div className={styles.planActions}><StatusChip status={plan.status} />{['pending', 'approved', 'paused'].includes(plan.status) && <><button type="button" onClick={(event) => { event.stopPropagation(); onModify() }}><Pencil size={14} />修改</button><button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button></>}{plan.status === 'running' && <button type="button" onClick={(event) => { event.stopPropagation(); onPause() }}><CirclePause size={15} />暂停</button>}{TERMINAL_STATUSES.has(plan.status) && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <Progress plan={plan} />
    <div className={styles.stepChips}>{plan.steps.map((step, index) => <span key={step.step_id} className={`${styles.stepChip} ${step.status === 'running' ? styles.activeStep : ''} ${step.status === 'completed' ? styles.doneStep : ''}`}><b>{step.status === 'completed' ? <Check size={12} /> : index + 1}</b>{step.title}</span>)}</div>
  </article>
}

function PlanDetailPanel({ plan }: { plan?: PlanSummary }) {
  return <aside className={styles.detailPanel}><header><div><h2>计划查看</h2><p>可查看未运行 / 已完成计划</p></div></header>{!plan ? <div className={styles.detailEmpty}><ClipboardList size={25} /><strong>选择一个计划</strong><span>点击未运行或已完成的计划查看详情。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={styles.planAvatar}>P</span><strong>{plan.title}</strong><StatusChip status={plan.status} /></div><dl><div><dt>计划 ID</dt><dd>{plan.plan_id}</dd></div><div><dt>状态</dt><dd><StatusChip status={plan.status} /></dd></div><div><dt>步骤总数</dt><dd>{plan.steps.length}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(plan.created_at)}</dd></div><div><dt>更新时间</dt><dd>{formatDateTime(plan.updated_at)}</dd></div></dl><section><h3>计划描述</h3><p>{plan.description || '暂无描述'}</p></section><section><h3>步骤概览</h3><div className={styles.detailSteps}>{plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span>{step.status === 'completed' && <Check size={14} />}</div>)}</div></section></div>}<footer><Info size={15} />提示：选择未运行或已完成的计划查看详情</footer></aside>
}

function scheduleLabel(task: CronTaskSummary) {
  if (task.type === 'daily') return `每天 ${task.time || '—'}`
  if (task.type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (task.type === 'recurring') {
    const seconds = Number(task.interval_seconds || 0)
    return seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.max(1, Math.round(seconds / 60))} 分钟`
  }
  return '未配置调度'
}

function CronCard({ task, selected, onSelect, onTogglePause, onDelete }: { task: CronTaskSummary; selected: boolean; onSelect: () => void; onTogglePause: () => void; onDelete: () => void }) {
  const running = task.status === 'running'
  const paused = task.status === 'paused'
  const terminal = TERMINAL_STATUSES.has(task.status)
  return <article className={`${styles.planCard} ${styles.cronCard} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${running ? styles.runningAvatar : ''} ${terminal ? styles.completedAvatar : ''}`}>C</span><div><h3>{task.title}</h3><p>{task.task_id}<span>·</span>创建于 {formatDateTime(task.created_at)}</p></div></div><div className={styles.planActions}><StatusChip status={task.status} />{!terminal && <button type="button" onClick={(event) => { event.stopPropagation(); onTogglePause() }}>{paused ? <><RotateCcw size={14} />恢复</> : <><CirclePause size={14} />暂停</>}</button>}{!running && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <div className={styles.cronFacts}><div><span>调度规则</span><strong>{scheduleLabel(task)}</strong></div><div><span>最近运行</span><strong>{formatDateTime(task.latest_run_at)}</strong></div><div><span>下次运行</span><strong>{formatDateTime(task.next_run_at)}</strong></div></div>
  </article>
}

function CronDetailPanel({ task }: { task?: CronTaskSummary }) {
  return <aside className={styles.detailPanel}><header><div><h2>定时任务查看</h2><p>查看调度配置和运行时间</p></div>{task && <span className={styles.readonlyBadge}><Eye size={13} />查看</span>}</header>{!task ? <div className={styles.detailEmpty}><TimerReset size={25} /><strong>选择一个定时任务</strong><span>点击左侧任务查看完整调度信息。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={styles.planAvatar}>C</span><strong>{task.title}</strong><StatusChip status={task.status} /></div><dl><div><dt>任务 ID</dt><dd>{task.task_id}</dd></div><div><dt>状态</dt><dd><StatusChip status={task.status} /></dd></div><div><dt>调度规则</dt><dd>{scheduleLabel(task)}</dd></div><div><dt>最近运行</dt><dd>{formatDateTime(task.latest_run_at)}</dd></div><div><dt>下次运行</dt><dd>{formatDateTime(task.next_run_at)}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(task.created_at)}</dd></div></dl><section><h3>任务来源</h3><p>{task.user_defined ? '当前用户创建的定时任务' : '系统内置定时任务'}</p></section></div>}<footer><Eye size={15} />只读查看，不在此处修改定时任务内容</footer></aside>
}

function ExecutionCard({ record, selected, onSelect, onDelete }: { record: ExecutionRecord; selected: boolean; onSelect: () => void; onDelete: () => void }) {
  return <article className={`${styles.planCard} ${styles.executionCard} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><div><h3>{record.title}</h3><p>{record.kindLabel}<span>·</span>{record.id}</p></div></div><div className={styles.planActions}><StatusChip status={record.status} /><button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button></div></header>
    <div className={styles.executionSummary}><span>完成时间</span><strong>{formatDateTime(record.updatedAt)}</strong></div>
  </article>
}

function ExecutionDetailPanel({ record }: { record?: ExecutionRecord }) {
  return <aside className={styles.detailPanel}><header><div><h2>执行记录查看</h2><p>按完成时间查看任务终态</p></div>{record && <span className={styles.readonlyBadge}><Eye size={13} />查看</span>}</header>{!record ? <div className={styles.detailEmpty}><CheckCircle2 size={25} /><strong>选择一条执行记录</strong><span>点击左侧记录查看只读详情。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><strong>{record.title}</strong><StatusChip status={record.status} /></div><dl><div><dt>记录类型</dt><dd>{record.kindLabel}</dd></div><div><dt>任务 ID</dt><dd>{record.id}</dd></div><div><dt>最终状态</dt><dd><StatusChip status={record.status} /></dd></div><div><dt>完成时间</dt><dd>{formatDateTime(record.updatedAt)}</dd></div></dl>{record.plan && <><section><h3>任务描述</h3><p>{record.plan.description || '暂无描述'}</p></section><section><h3>步骤结果</h3><div className={styles.detailSteps}>{record.plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span><StatusChip status={step.status} /></div>)}</div></section></>}{record.cron && <section><h3>调度信息</h3><p>{scheduleLabel(record.cron)} · 最近运行 {formatDateTime(record.cron.latest_run_at)}</p></section>}</div>}<footer><Eye size={15} />只读记录，不提供编辑操作</footer></aside>
}

export function TasksPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [tab, setTab] = useState<TaskTab>('plans')
  const [selectedPlanId, setSelectedPlanId] = useState('')
  const [selectedCronId, setSelectedCronId] = useState('')
  const [selectedHistoryKey, setSelectedHistoryKey] = useState('')
  const query = useQuery({ queryKey: ['tasks', user], queryFn: () => getTasks(user), enabled: Boolean(user) })
  const data = query.data
  const refresh = () => client.invalidateQueries({ queryKey: ['tasks', user] })
  const planUpdate = useMutation({ mutationFn: ({ id, status, revision }: { id: string; status: string; revision: number }) => updatePlan(user, id, { status, revision }), onSuccess: refresh })
  const planDelete = useMutation({ mutationFn: (id: string) => deletePlan(user, id), onSuccess: refresh })
  const cronUpdate = useMutation({ mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => updateCron(user, id, body), onSuccess: refresh })
  const cronDelete = useMutation({ mutationFn: (id: string) => deleteCron(user, id), onSuccess: refresh })
  const plans = data?.plans || []
  const cronTasks = data?.cron_tasks || []
  const inspectablePlans = plans.filter(inspectable)
  const latestSessionId = client.getQueryData<SessionsResponse>(['sessions', user])?.sessions[0]?.session_id || ''
  const history = useMemo<ExecutionRecord[]>(() => [
    ...plans.filter((item) => TERMINAL_STATUSES.has(item.status)).map((item) => ({ key: `plan:${item.plan_id}`, id: item.plan_id, title: item.title, kind: 'plan' as const, kindLabel: '任务计划', status: item.status, updatedAt: item.updated_at, plan: item })),
    ...cronTasks.filter((item) => TERMINAL_STATUSES.has(item.status)).map((item) => ({ key: `cron:${item.task_id}`, id: item.task_id, title: item.title, kind: 'cron' as const, kindLabel: '定时任务', status: item.status, updatedAt: item.latest_run_at || item.created_at, cron: item })),
  ].sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0)), [cronTasks, plans])

  useEffect(() => { if (!inspectablePlans.some((plan) => plan.plan_id === selectedPlanId)) setSelectedPlanId(inspectablePlans[0]?.plan_id || '') }, [inspectablePlans, selectedPlanId])
  useEffect(() => { if (!cronTasks.some((task) => task.task_id === selectedCronId)) setSelectedCronId(cronTasks[0]?.task_id || '') }, [cronTasks, selectedCronId])
  useEffect(() => { if (!history.some((record) => record.key === selectedHistoryKey)) setSelectedHistoryKey(history[0]?.key || '') }, [history, selectedHistoryKey])

  const selectedPlan = inspectablePlans.find((plan) => plan.plan_id === selectedPlanId)
  const selectedCron = cronTasks.find((task) => task.task_id === selectedCronId)
  const selectedHistory = history.find((record) => record.key === selectedHistoryKey)
  const conversationUrl = (prompt: string, sessionId = latestSessionId) => `/chat?user=${encodeURIComponent(user)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}&prompt=${encodeURIComponent(prompt)}`
  const modify = (plan: PlanSummary) => navigate(conversationUrl(`请修改任务计划：${plan.title}（${plan.plan_id}）`, plan.session_id || latestSessionId))
  const removePlan = (plan: PlanSummary) => { if (window.confirm(`删除任务计划“${plan.title}”？`)) planDelete.mutate(plan.plan_id) }
  const removeCron = (task: CronTaskSummary) => { if (window.confirm(`删除定时任务“${task.title}”？`)) cronDelete.mutate(task.task_id) }
  const removeHistory = (record: ExecutionRecord) => {
    if (!window.confirm(`删除执行记录“${record.title}”？`)) return
    if (record.kind === 'plan') planDelete.mutate(record.id)
    else cronDelete.mutate(record.id)
  }

  return <ModuleFrame kicker="Task Orchestration" title="任务中枢" description="统一查看计划任务、周期调度和执行进度；复杂任务由对话生成并按用户隔离存储。" actions={<><button className="module-btn" onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCw size={15} />刷新状态</button><button className="module-btn primary" onClick={() => navigate(conversationUrl('请根据我的目标创建一份任务计划'))}><Send size={15} />通过对话创建</button></>}>
    {query.isError && <ModuleError />}
    <section className={styles.stats}><MetricCard label="活动计划" value={plans.length} detail="总创建计划" symbol={<ClipboardList size={16} />} /><MetricCard label="执行中" value={plans.filter((plan) => plan.status === 'running').length} detail="正在执行" symbol={<Play size={16} />} /><MetricCard label="已完成" value={plans.filter((plan) => plan.status === 'completed').length} detail="已完成计划" symbol={<CheckCircle2 size={16} />} tone="success" /></section>
    <div className="module-toolbar"><div className="module-tabs">{(['plans', 'cron', 'history'] as const).map((item) => <button key={item} className={`module-tab-btn ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'plans' ? '任务计划' : item === 'cron' ? '定时任务' : '执行记录'}</button>)}</div><div className="toolbar-spacer" /></div>
    {tab === 'plans' && <div className={styles.planLayout}><main className={styles.planList}>{plans.length ? plans.map((plan) => <PlanCard key={plan.plan_id} plan={plan} selected={plan.plan_id === selectedPlanId} onSelect={() => setSelectedPlanId(plan.plan_id)} onModify={() => modify(plan)} onPause={() => planUpdate.mutate({ id: plan.plan_id, status: 'paused', revision: plan.revision })} onDelete={() => removePlan(plan)} />) : <EmptyPanel title="暂无任务计划" description="在对话中描述目标，kemo-agent 会生成计划草案并等待确认。" icon={<ClipboardList size={21} />} />}</main><PlanDetailPanel plan={selectedPlan} /></div>}
    {tab === 'cron' && <div className={styles.planLayout}><main className={styles.planList}>{cronTasks.length ? cronTasks.map((task) => <CronCard key={task.task_id} task={task} selected={task.task_id === selectedCronId} onSelect={() => setSelectedCronId(task.task_id)} onTogglePause={() => cronUpdate.mutate({ id: task.task_id, body: { status: task.status === 'paused' ? 'enabled' : 'paused' } })} onDelete={() => removeCron(task)} />) : <EmptyPanel title="暂无定时任务" description="当前用户没有可显示的定时任务。" icon={<TimerReset size={21} />} />}</main><CronDetailPanel task={selectedCron} /></div>}
    {tab === 'history' && <div className={styles.planLayout}><main className={styles.planList}>{history.length ? history.map((record) => <ExecutionCard key={record.key} record={record} selected={record.key === selectedHistoryKey} onSelect={() => setSelectedHistoryKey(record.key)} onDelete={() => removeHistory(record)} />) : <EmptyPanel title="暂无执行记录" description="已完成、失败或取消的任务计划和定时任务会出现在这里。" icon={<CheckCircle2 size={21} />} />}</main><ExecutionDetailPanel record={selectedHistory} /></div>}
  </ModuleFrame>
}
