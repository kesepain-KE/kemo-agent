import { useEffect, useMemo, useState } from 'react'
import { Check, CheckCircle2, CirclePause, ClipboardList, Info, Pencil, Play, RefreshCw, Send, TimerReset, Trash2 } from 'lucide-react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { deleteCron, deletePlan, getTasks, updateCron, updatePlan } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatDateTime, MetricCard, ModuleError, ModuleFrame, StatusChip, statusLabel } from '../components/ModuleUi'
import type { CronTaskSummary, PlanSummary, SessionsResponse } from '../types/api'
import styles from './TasksPage.module.css'

type TaskTab = 'plans' | 'cron' | 'history'
const inspectable = (plan: PlanSummary) => ['pending', 'approved', 'completed'].includes(plan.status)
const planStatus = (status: string) => status

function Progress({ plan }: { plan: PlanSummary }) {
  const completed = plan.progress.completed
  return <div className={styles.progress}><div className={styles.progressHead}><strong>整体进度</strong><span>{completed} / {plan.progress.total}{plan.status !== 'pending' && <b>{plan.progress.percent}%</b>}</span></div>{plan.status !== 'pending' && <div className={styles.progressTrack}><i style={{ width: `${plan.progress.percent}%` }} /></div>}</div>
}

function PlanCard({ plan, selected, onSelect, onModify, onPause, onDelete }: { plan: PlanSummary; selected: boolean; onSelect: () => void; onModify: () => void; onPause: () => void; onDelete: () => void }) {
  const canInspect = inspectable(plan)
  const status = planStatus(plan.status)
  return <article className={`${styles.planCard} ${selected ? styles.selected : ''} ${!canInspect ? styles.runningCard : ''}`} onClick={() => canInspect && onSelect()} role={canInspect ? 'button' : undefined} tabIndex={canInspect ? 0 : -1}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${status === 'running' ? styles.runningAvatar : ''} ${status === 'completed' ? styles.completedAvatar : ''}`}>{status === 'running' ? 'R' : 'P'}</span><div><h3>{plan.title}</h3><p>{plan.plan_id}<span>·</span>更新于 {formatDateTime(plan.updated_at)}</p></div></div><div className={styles.planActions}><StatusChip status={status} />{['pending', 'approved', 'paused'].includes(plan.status) && <><button type="button" onClick={(event) => { event.stopPropagation(); onModify() }}><Pencil size={14} />修改</button><button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button></>}{plan.status === 'running' && <button type="button" onClick={(event) => { event.stopPropagation(); onPause() }}><CirclePause size={15} />暂停</button>}{plan.status === 'completed' && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <Progress plan={plan} />
    <div className={styles.stepChips}>{plan.steps.map((step, index) => <span key={step.step_id} className={`${styles.stepChip} ${step.status === 'running' ? styles.activeStep : ''} ${step.status === 'completed' ? styles.doneStep : ''}`}><b>{step.status === 'completed' ? <Check size={12} /> : index + 1}</b>{step.title}</span>)}</div>
  </article>
}

function DetailPanel({ plan }: { plan?: PlanSummary }) {
  return <aside className={styles.detailPanel}><header><div><h2>计划查看</h2><p>可查看未运行 / 已完成计划</p></div></header>{!plan ? <div className={styles.detailEmpty}><ClipboardList size={25} /><strong>选择一个计划</strong><span>点击未运行或已完成的计划查看详情。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={styles.planAvatar}>P</span><strong>{plan.title}</strong><StatusChip status={planStatus(plan.status)} /></div><dl><div><dt>计划 ID</dt><dd>{plan.plan_id}</dd></div><div><dt>状态</dt><dd><StatusChip status={planStatus(plan.status)} /></dd></div><div><dt>步骤总数</dt><dd>{plan.steps.length}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(plan.created_at)}</dd></div><div><dt>更新时间</dt><dd>{formatDateTime(plan.updated_at)}</dd></div></dl><section><h3>计划描述</h3><p>{plan.description || '暂无描述'}</p></section><section><h3>步骤概览</h3><div className={styles.detailSteps}>{plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span>{step.status === 'completed' && <Check size={14} />}</div>)}</div></section></div>}<footer><Info size={15} />提示：选择未运行或已完成的计划查看详情</footer></aside>
}

function scheduleLabel(task: CronTaskSummary) {
  if (task.type === 'daily') return `每天 ${task.time || '—'}`
  if (task.type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (task.type === 'recurring') { const seconds = Number(task.interval_seconds || 0); return seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.max(1, Math.round(seconds / 60))} 分钟` }
  return '未配置调度'
}

export function TasksPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate(); const client = useQueryClient(); const [tab, setTab] = useState<TaskTab>('plans'); const [selectedId, setSelectedId] = useState('')
  const query = useQuery({ queryKey: ['tasks', user], queryFn: () => getTasks(user), enabled: Boolean(user) }); const data = query.data
  const refresh = () => client.invalidateQueries({ queryKey: ['tasks', user] })
  const planUpdate = useMutation({ mutationFn: ({ id, status, revision }: { id: string; status: string; revision: number }) => updatePlan(user, id, { status, revision }), onSuccess: refresh })
  const planDelete = useMutation({ mutationFn: (id: string) => deletePlan(user, id), onSuccess: refresh })
  const cronUpdate = useMutation({ mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => updateCron(user, id, body), onSuccess: refresh })
  const cronDelete = useMutation({ mutationFn: (id: string) => deleteCron(user, id), onSuccess: refresh })
  const plans = data?.plans || []; const inspectablePlans = plans.filter(inspectable)
  const latestSessionId = client.getQueryData<SessionsResponse>(['sessions', user])?.sessions[0]?.session_id || ''
  useEffect(() => { if (!inspectablePlans.some((plan) => plan.plan_id === selectedId)) setSelectedId(inspectablePlans[0]?.plan_id || '') }, [data, selectedId])
  const selectedPlan = inspectablePlans.find((plan) => plan.plan_id === selectedId)
  const history = [...plans.filter((item) => ['completed', 'failed', 'cancelled'].includes(item.status)).map((item) => ({ id: item.plan_id, title: item.title, kind: '任务计划', status: item.status, updatedAt: item.updated_at })), ...(data?.cron_tasks.filter((item) => item.last_state !== 'never').map((item) => ({ id: item.task_id, title: item.title, kind: '定时任务', status: item.last_state, updatedAt: item.latest_run_at || item.created_at })) || [])].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
  const conversationUrl = (prompt: string, sessionId = latestSessionId) => `/chat?user=${encodeURIComponent(user)}${sessionId ? `&session=${encodeURIComponent(sessionId)}` : ''}&prompt=${encodeURIComponent(prompt)}`
  const modify = (plan: PlanSummary) => navigate(conversationUrl(`请修改任务计划：${plan.title}（${plan.plan_id}）`, plan.session_id || latestSessionId))
  const remove = (plan: PlanSummary) => { if (window.confirm(`删除任务计划“${plan.title}”？`)) planDelete.mutate(plan.plan_id) }
  return <ModuleFrame kicker="Task Orchestration" title="任务中枢" description="统一查看计划任务、周期调度和执行进度；复杂任务由对话生成并按用户隔离存储。" actions={<><button className="module-btn" onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCw size={15} />刷新状态</button><button className="module-btn primary" onClick={() => navigate(conversationUrl('请根据我的目标创建一份任务计划'))}><Send size={15} />通过对话创建</button></>}>
    {query.isError && <ModuleError />}
    <section className={styles.stats}><MetricCard label="活动计划" value={plans.length} detail="总创建计划" symbol={<ClipboardList size={16} />} /><MetricCard label="执行中" value={plans.filter((plan) => plan.status === 'running').length} detail="正在执行" symbol={<Play size={16} />} /><MetricCard label="已完成" value={plans.filter((plan) => plan.status === 'completed').length} detail="已完成计划" symbol={<CheckCircle2 size={16} />} tone="success" /></section>
    <div className="module-toolbar"><div className="module-tabs">{(['plans', 'cron', 'history'] as const).map((item) => <button key={item} className={`module-tab-btn ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'plans' ? '任务计划' : item === 'cron' ? '定时任务' : '执行记录'}</button>)}</div><div className="toolbar-spacer" /></div>
    {tab === 'plans' && <div className={styles.planLayout}><main className={styles.planList}>{plans.length ? plans.map((plan) => <PlanCard key={plan.plan_id} plan={plan} selected={plan.plan_id === selectedId} onSelect={() => setSelectedId(plan.plan_id)} onModify={() => modify(plan)} onPause={() => planUpdate.mutate({ id: plan.plan_id, status: 'paused', revision: plan.revision })} onDelete={() => remove(plan)} />) : <EmptyPanel title="暂无任务计划" description="在对话中描述目标，kemo-agent 会生成计划草案并等待确认。" icon={<ClipboardList size={21} />} />}</main><DetailPanel plan={selectedPlan} /></div>}
    {tab === 'cron' && <article className="panel table-panel"><div className="panel-head"><div className="panel-title"><span className="panel-title-icon">C</span><span><strong>周期调度</strong><span>由 RuntimeHost 调度</span></span></div><span className="panel-count">{data?.cron_tasks.length || 0}</span></div>{data?.cron_tasks.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>任务</th><th>调度</th><th>状态</th><th>最近运行</th><th>下次运行</th><th>操作</th></tr></thead><tbody>{data.cron_tasks.map((task) => <tr key={task.task_id}><td><span className="table-main"><span className="table-icon">C</span><span><strong>{task.title}</strong><span>{task.task_id}</span></span></span></td><td>{scheduleLabel(task)}</td><td><StatusChip status={task.status} /></td><td>{formatDateTime(task.latest_run_at)}</td><td>{formatDateTime(task.next_run_at)}</td><td><span className="module-actions"><button className="module-btn" onClick={() => cronUpdate.mutate({ id: task.task_id, body: { status: task.status === 'paused' ? 'enabled' : 'paused' } })}>{task.status === 'paused' ? <Play size={14} /> : <CirclePause size={14} />}</button><button className="module-btn danger" onClick={() => { if (window.confirm('删除此定时任务？')) cronDelete.mutate(task.task_id) }}><Trash2 size={14} /></button></span></td></tr>)}</tbody></table></div> : <EmptyPanel title="暂无定时任务" description="当前用户没有可显示的定时任务。" icon={<TimerReset size={21} />} />}</article>}
    {tab === 'history' && <article className="panel table-panel"><div className="panel-head"><div className="panel-title"><span className="panel-title-icon">H</span><span><strong>执行记录</strong><span>来自计划终态和 Cron 最近一次运行</span></span></div><span className="panel-count">{history.length}</span></div>{history.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>执行项</th><th>类型</th><th>状态</th><th>更新时间</th></tr></thead><tbody>{history.map((item) => <tr key={item.id}><td>{item.title}</td><td>{item.kind}</td><td><StatusChip status={item.status} /></td><td>{formatDateTime(item.updatedAt)}</td></tr>)}</tbody></table></div> : <EmptyPanel title="暂无执行记录" description="计划或定时任务产生终态后会在这里出现。" />}</article>}
  </ModuleFrame>
}
