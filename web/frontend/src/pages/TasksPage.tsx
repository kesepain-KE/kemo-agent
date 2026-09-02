import { useEffect, useMemo, useState } from 'react'
import { Check, CheckCircle2, CirclePause, ClipboardList, Eye, History, Pencil, Play, RotateCcw, Send, TimerReset, Trash2 } from 'lucide-react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { commandPlan, deleteCron, deletePlan, editPlan, getPlanRevision, getPlanRevisions, getTasks, retryPlanStep, rollbackPlan, updateCron } from '../api/client'
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
}

const TERMINAL_STATUSES = new Set(['completed', 'failed', 'cancelled'])
const inspectable = (plan: PlanSummary) => ['pending', 'approved', 'paused', 'completed', 'failed', 'cancelled'].includes(plan.status)

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
    <header><div><h2>计划查看</h2><p>可查看未运行 / 已完成计划</p></div>{plan && <button type="button" className={styles.historyButton} aria-pressed={historyOpen} onClick={onToggleHistory}><History size={14} />历史版本</button>}</header>
    {!plan ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><ClipboardList size={25} /><strong>选择一个计划</strong><span>点击未运行或已完成的计划查看详情。</span></div> : <div className={styles.detailBody}>
      <div className={styles.detailTitle}><span className={styles.planAvatar}>P</span><strong>{plan.title}</strong><StatusChip status={plan.status} /></div>
      <dl><div><dt>计划 ID</dt><dd>{plan.plan_id}</dd></div><div><dt>状态</dt><dd><StatusChip status={plan.status} /></dd></div><div><dt>步骤总数</dt><dd>{plan.steps.length}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(plan.created_at)}</dd></div><div><dt>更新时间</dt><dd>{formatDateTime(plan.updated_at)}</dd></div></dl>
      <section><h3>计划描述</h3><p>{plan.description || '暂无描述'}</p></section>
      <section><h3>步骤概览</h3><div className={styles.detailSteps}>{plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span>{step.status === 'completed' && <Check size={14} />}</div>)}</div></section>
      <PlanRevisionPanel open={historyOpen} revisions={revisions} selectedRevision={selectedRevision} snapshot={snapshot} loading={historyLoading} error={historyError} feedback={historyFeedback} rollbackPending={rollbackPending} rollbackAllowed={['pending', 'paused', 'failed'].includes(plan.status)} onSelect={onSelectRevision} onRollback={onRollback} />
    </div>}
  </aside>
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
  return <article className={`${styles.planCard} ${themeStyles.surface} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${running ? styles.runningAvatar : ''} ${terminal ? styles.completedAvatar : ''}`}>C</span><div><h3>{task.title}</h3><p>{task.task_id}<span>·</span>创建于 {formatDateTime(task.created_at)}</p></div></div><div className={styles.planActions}><StatusChip status={task.status} />{!terminal && <button type="button" onClick={(event) => { event.stopPropagation(); onTogglePause() }}>{paused ? <><RotateCcw size={14} />恢复</> : <><CirclePause size={14} />暂停</>}</button>}{!running && <button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>}</div></header>
    <div className={styles.cronFacts}><div><span>调度规则</span><strong>{scheduleLabel(task)}</strong></div><div><span>最近运行</span><strong>{formatDateTime(task.latest_run_at)}</strong></div><div><span>下次运行</span><strong>{formatDateTime(task.next_run_at)}</strong></div></div>
  </article>
}

function CronDetailPanel({ task }: { task?: CronTaskSummary }) {
  return <aside className={`${styles.detailPanel} ${themeStyles.surface} ${themeStyles.detail} ${!task ? themeStyles.emptyDetailPanel : ''}`}><header><div><h2>定时任务查看</h2><p>查看调度配置和运行时间</p></div>{task && <span className={styles.readonlyBadge}><Eye size={13} />查看</span>}</header>{!task ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><TimerReset size={25} /><strong>选择一个定时任务</strong><span>点击左侧任务查看完整调度信息。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={styles.planAvatar}>C</span><strong>{task.title}</strong><StatusChip status={task.status} /></div><dl><div><dt>任务 ID</dt><dd>{task.task_id}</dd></div><div><dt>状态</dt><dd><StatusChip status={task.status} /></dd></div><div><dt>调度规则</dt><dd>{scheduleLabel(task)}</dd></div><div><dt>最近运行</dt><dd>{formatDateTime(task.latest_run_at)}</dd></div><div><dt>下次运行</dt><dd>{formatDateTime(task.next_run_at)}</dd></div><div><dt>创建时间</dt><dd>{formatDateTime(task.created_at)}</dd></div></dl><section><h3>任务来源</h3><p>{task.user_defined ? '当前用户创建的定时任务' : '系统内置定时任务'}</p></section></div>}<footer><Eye size={15} />只读查看，不在此处修改定时任务内容</footer></aside>
}

function ExecutionCard({ record, selected, onSelect, onDelete }: { record: ExecutionRecord; selected: boolean; onSelect: () => void; onDelete: () => void }) {
  return <article className={`${styles.planCard} ${themeStyles.surface} ${styles.executionCard} ${selected ? styles.selected : ''}`} onClick={onSelect} role="button" tabIndex={0}>
    <header className={styles.planHeader}><div className={styles.planIdentity}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><div><h3>{record.title}</h3><p>{record.kindLabel}<span>·</span>{record.id}</p></div></div><div className={styles.planActions}><StatusChip status={record.status} /><button type="button" onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button></div></header>
    <div className={styles.executionSummary}><span>完成时间</span><strong>{formatDateTime(record.updatedAt)}</strong></div>
  </article>
}

function ExecutionDetailPanel({ record }: { record?: ExecutionRecord }) {
  return <aside className={`${styles.detailPanel} ${themeStyles.surface} ${themeStyles.detail} ${!record ? themeStyles.emptyDetailPanel : ''}`}><header><div><h2>执行记录查看</h2><p>按完成时间查看任务终态</p></div>{record && <span className={styles.readonlyBadge}><Eye size={13} />查看</span>}</header>{!record ? <div className={`${styles.detailEmpty} ${themeStyles.expandedEmpty}`}><CheckCircle2 size={25} /><strong>选择一条执行记录</strong><span>点击左侧记录查看只读详情。</span></div> : <div className={styles.detailBody}><div className={styles.detailTitle}><span className={`${styles.planAvatar} ${styles.completedAvatar}`}>{record.kind === 'plan' ? 'P' : 'C'}</span><strong>{record.title}</strong><StatusChip status={record.status} /></div><dl><div><dt>记录类型</dt><dd>{record.kindLabel}</dd></div><div><dt>任务 ID</dt><dd>{record.id}</dd></div><div><dt>最终状态</dt><dd><StatusChip status={record.status} /></dd></div><div><dt>完成时间</dt><dd>{formatDateTime(record.updatedAt)}</dd></div></dl>{record.plan && <><section><h3>任务描述</h3><p>{record.plan.description || '暂无描述'}</p></section><section><h3>步骤结果</h3><div className={styles.detailSteps}>{record.plan.steps.map((step, index) => <div key={step.step_id}><b>{index + 1}</b><span>{step.title}</span><StatusChip status={step.status} /></div>)}</div></section></>}{record.cron && <section><h3>调度信息</h3><p>{scheduleLabel(record.cron)} · 最近运行 {formatDateTime(record.cron.latest_run_at)}</p></section>}</div>}<footer><Eye size={15} />只读记录，不提供编辑操作</footer></aside>
}

export function TasksPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const client = useQueryClient()
  const [tab, setTab] = useState<TaskTab>('plans')
  const [selectedPlanId, setSelectedPlanId] = useState('')
  const [selectedCronId, setSelectedCronId] = useState('')
  const [selectedHistoryKey, setSelectedHistoryKey] = useState('')
  const [historyPlanId, setHistoryPlanId] = useState('')
  const [selectedRevision, setSelectedRevision] = useState(0)
  const [historyFeedback, setHistoryFeedback] = useState('')
  const [planMutationFeedback, setPlanMutationFeedback] = useState('')
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
  const cronUpdate = useMutation({ mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => updateCron(user, id, body), onSuccess: refresh })
  const cronDelete = useMutation({ mutationFn: (id: string) => deleteCron(user, id), onSuccess: refresh })
  const plans = data?.plans || []
  const cronTasks = data?.cron_tasks || []
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
    ...cronTasks.filter((item) => TERMINAL_STATUSES.has(item.status)).map((item) => ({ key: `cron:${item.task_id}`, id: item.task_id, title: item.title, kind: 'cron' as const, kindLabel: '定时任务', status: item.status, updatedAt: item.latest_run_at || item.created_at, cron: item })),
  ].sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0)), [cronTasks, plans])

  useEffect(() => { if (!inspectablePlans.some((plan) => plan.plan_id === selectedPlanId)) setSelectedPlanId(inspectablePlans[0]?.plan_id || '') }, [inspectablePlans, selectedPlanId])
  useEffect(() => { if (!cronTasks.some((task) => task.task_id === selectedCronId)) setSelectedCronId(cronTasks[0]?.task_id || '') }, [cronTasks, selectedCronId])
  useEffect(() => { if (!history.some((record) => record.key === selectedHistoryKey)) setSelectedHistoryKey(history[0]?.key || '') }, [history, selectedHistoryKey])
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
  const selectedCron = cronTasks.find((task) => task.task_id === selectedCronId)
  const selectedHistory = history.find((record) => record.key === selectedHistoryKey)
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
  const removeHistory = (record: ExecutionRecord) => {
    if (!window.confirm(`删除执行记录“${record.title}”？`)) return
    if (record.kind === 'plan' && record.plan) planDelete.mutate(record.plan)
    else cronDelete.mutate(record.id)
  }

  return <ModuleFrame kicker="Task Orchestration" title="任务中枢" description="统一查看计划任务、周期调度和执行进度；复杂任务由对话生成并按用户隔离存储。" actions={<><RefreshActionButton pending={query.isFetching} label="刷新状态" pendingLabel="刷新中…" onClick={() => { void query.refetch() }} /><button className="module-btn primary" onClick={() => navigate(conversationUrl('请根据我的目标创建一份任务计划'))}><Send size={15} />通过对话创建</button></>}>
    {query.isError && <ModuleError />}
    <section className={styles.stats}><MetricCard label="活动计划" value={plans.length} detail="总创建计划" symbol={<ClipboardList size={16} />} /><MetricCard label="执行中" value={plans.filter((plan) => plan.status === 'running').length} detail="正在执行" symbol={<Play size={16} />} /><MetricCard label="已完成" value={plans.filter((plan) => plan.status === 'completed').length} detail="已完成计划" symbol={<CheckCircle2 size={16} />} tone="success" /></section>
    <div className="module-toolbar"><div className="module-tabs">{(['plans', 'cron', 'history'] as const).map((item) => <button key={item} className={`module-tab-btn ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'plans' ? '任务计划' : item === 'cron' ? '定时任务' : '执行记录'}</button>)}</div><div className="toolbar-spacer" /></div>
    {tab === 'plans' && planMutationFeedback && <div className={styles.mutationFeedback} role="status">{planMutationFeedback}</div>}
    {tab === 'plans' && <div className={styles.planLayout}><main className={`${styles.planList} ${plans.length ? styles.populatedList : ''}`}>{plans.length ? plans.map((plan) => <PlanCard key={plan.plan_id} plan={plan} selected={plan.plan_id === selectedPlanId} onSelect={() => setSelectedPlanId(plan.plan_id)} onModify={() => { void modify(plan) }} onPause={() => planPause.mutate(plan)} onRetryStep={(stepId) => { void retryStep(plan, stepId) }} onDelete={() => removePlan(plan)} />) : <EmptyPanel title="暂无任务计划" description="在对话中描述目标，kemo-agent 会生成计划草案并等待确认。" icon={<ClipboardList size={21} />} />}</main><PlanDetailPanel plan={selectedPlan} historyOpen={Boolean(selectedPlan && historyPlanId === selectedPlan.plan_id)} revisions={revisionsQuery.data?.revisions || []} selectedRevision={selectedRevision} snapshot={revisionQuery.data?.plan} historyLoading={revisionsQuery.isLoading || revisionQuery.isLoading} historyError={(revisionsQuery.error || revisionQuery.error) instanceof Error ? String((revisionsQuery.error || revisionQuery.error)?.message || '') : ''} historyFeedback={historyFeedback} rollbackPending={rollbackMutation.isPending} onToggleHistory={() => selectedPlan && toggleHistory(selectedPlan)} onSelectRevision={(revision) => { setSelectedRevision(revision); setHistoryFeedback('') }} onRollback={(revision) => selectedPlan && rollbackRevision(selectedPlan, revision)} /></div>}
    {tab === 'cron' && <div className={styles.planLayout}><main className={`${styles.planList} ${cronTasks.length ? styles.populatedList : ''}`}>{cronTasks.length ? cronTasks.map((task) => <CronCard key={task.task_id} task={task} selected={task.task_id === selectedCronId} onSelect={() => setSelectedCronId(task.task_id)} onTogglePause={() => cronUpdate.mutate({ id: task.task_id, body: { status: task.status === 'paused' ? 'enabled' : 'paused' } })} onDelete={() => removeCron(task)} />) : <EmptyPanel title="暂无定时任务" description="当前用户没有可显示的定时任务。" icon={<TimerReset size={21} />} />}</main><CronDetailPanel task={selectedCron} /></div>}
    {tab === 'history' && <div className={styles.planLayout}><main className={`${styles.planList} ${history.length ? styles.populatedList : ''}`}>{history.length ? history.map((record) => <ExecutionCard key={record.key} record={record} selected={record.key === selectedHistoryKey} onSelect={() => setSelectedHistoryKey(record.key)} onDelete={() => removeHistory(record)} />) : <EmptyPanel title="暂无执行记录" description="已完成、失败或取消的任务计划和定时任务会出现在这里。" icon={<CheckCircle2 size={21} />} />}</main><ExecutionDetailPanel record={selectedHistory} /></div>}
  </ModuleFrame>
}
