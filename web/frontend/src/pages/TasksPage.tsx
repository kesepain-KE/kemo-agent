import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CheckCircle2, Clock3, ListChecks, RefreshCw, Send, TimerReset } from 'lucide-react'
import { useNavigate, useOutletContext } from 'react-router-dom'
import { getTasks } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import {
  EmptyPanel,
  formatDateTime,
  MetricCard,
  ModuleError,
  ModuleFrame,
  StatusChip,
  statusLabel,
} from '../components/ModuleUi'
import type { CronTaskSummary, PlanSummary } from '../types/api'

type TaskTab = 'plans' | 'cron' | 'history'

function scheduleLabel(task: CronTaskSummary) {
  const type = task.type
  if (type === 'daily') return `每天 ${task.time || '—'}`
  if (type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (type === 'recurring') {
    const seconds = Number(task.interval_seconds || 0)
    return seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.round(seconds / 60)} 分钟`
  }
  return '未配置调度'
}

function PlanPanel({ plan }: { plan: PlanSummary }) {
  return (
    <article className="panel">
      <div className="panel-head">
        <div className="panel-title">
          <span className="panel-title-icon">P</span>
          <span><strong>当前执行计划</strong><span>{plan.title}</span></span>
        </div>
        <StatusChip status={plan.status} />
      </div>
      <div className="panel-body">
        <div className="progress-row">
          <strong>整体进度</strong>
          <span>{plan.progress.completed} / {plan.progress.total} 节点 · {plan.progress.percent}%</span>
        </div>
        <div className="progress-line"><i style={{ width: `${plan.progress.percent}%` }} /></div>
        <div className="plan-meta">
          <span>{plan.plan_id}</span><span>修订 {plan.revision}</span><span>更新于 {formatDateTime(plan.updated_at)}</span>
        </div>
        <div className="task-flow">
          {plan.steps.map((step, index) => (
            <div className={`flow-row ${step.status}`} key={step.step_id}>
              <span className="flow-index">{step.status === 'completed' ? '✓' : index + 1}</span>
              <span className="flow-copy"><strong>{step.title}</strong><span>{step.description}</span></span>
              <span className="flow-time">{statusLabel(step.status)}</span>
            </div>
          ))}
        </div>
      </div>
    </article>
  )
}

export function TasksPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const [tab, setTab] = useState<TaskTab>('plans')
  const query = useQuery({ queryKey: ['tasks', user], queryFn: () => getTasks(user), enabled: Boolean(user) })
  const data = query.data
  const activePlan = data?.plans.find((plan) => ['running', 'approved', 'paused'].includes(plan.status)) || data?.plans[0]
  const queuedPlans = data?.plans.filter((plan) => plan.plan_id !== activePlan?.plan_id) || []
  const history = [
    ...(data?.plans.filter((item) => ['completed', 'failed', 'cancelled'].includes(item.status)).map((item) => ({
      id: item.plan_id, title: item.title, kind: '任务计划', status: item.status, updatedAt: item.updated_at,
    })) || []),
    ...(data?.cron_tasks.filter((item) => item.last_state !== 'never').map((item) => ({
      id: item.task_id, title: item.title, kind: '定时任务', status: item.last_state, updatedAt: item.latest_run_at || item.created_at,
    })) || []),
  ].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))

  return (
    <ModuleFrame
      kicker="Task Orchestration"
      title="任务中枢"
      description="统一查看计划任务、周期调度和执行链路；复杂任务仍由对话生成并按用户隔离存储。"
      actions={<>
        <button className="module-btn" onClick={() => void query.refetch()}><RefreshCw size={15} />刷新状态</button>
        <button className="module-btn primary" onClick={() => navigate(`/chat?user=${encodeURIComponent(user)}&prompt=${encodeURIComponent('请根据我的目标创建一份任务计划')}`)}><Send size={15} />通过对话创建</button>
      </>}
    >
      {query.isError && <ModuleError />}
      <section className="metric-strip">
        <MetricCard label="活动计划" value={data?.summary.active_plans ?? '—'} detail="运行 / 已批准 / 暂停" symbol={<ListChecks size={16} />} />
        <MetricCard label="等待队列" value={data?.summary.waiting_plans ?? '—'} detail="待确认或可恢复" symbol={<Clock3 size={16} />} />
        <MetricCard label="定时任务" value={data?.summary.enabled_crons ?? '—'} detail="当前已启用" symbol={<TimerReset size={16} />} />
        <MetricCard label="已完成" value={data?.summary.completed_plans ?? '—'} detail="已提交的计划" symbol={<CheckCircle2 size={16} />} tone="success" />
      </section>

      <div className="module-toolbar">
        <div className="module-tabs">
          <button className={`module-tab-btn ${tab === 'plans' ? 'active' : ''}`} onClick={() => setTab('plans')}>任务计划</button>
          <button className={`module-tab-btn ${tab === 'cron' ? 'active' : ''}`} onClick={() => setTab('cron')}>定时任务</button>
          <button className={`module-tab-btn ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>执行记录</button>
        </div>
        <div className="toolbar-spacer" />
        <span className="toolbar-note">只读运行态 · 数据来自当前用户目录</span>
      </div>

      {tab === 'plans' && (
        activePlan ? (
          <div className="module-grid">
            <div><PlanPanel plan={activePlan} /></div>
            <aside>
              <article className="panel">
                <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">Q</span><span><strong>等待队列</strong><span>其余计划按更新时间排列</span></span></div><span className="panel-count">{queuedPlans.length}</span></div>
                <div className="panel-body compact-list">
                  {queuedPlans.length ? queuedPlans.slice(0, 5).map((plan, index) => (
                    <div className="compact-row" key={plan.plan_id}>
                      <span className="compact-icon">{String(index + 1).padStart(2, '0')}</span>
                      <span className="compact-copy"><strong>{plan.title}</strong><span>{plan.description}</span></span>
                      <span className="compact-value"><b>{statusLabel(plan.status)}</b>{formatDateTime(plan.updated_at)}</span>
                    </div>
                  )) : <EmptyPanel title="没有其他计划" description="当前只有这一项计划。" />}
                </div>
              </article>
              <article className="panel">
                <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">R</span><span><strong>运行资源</strong><span>磁盘权威状态</span></span></div></div>
                <div className="panel-body metric-list">
                  <div className="metric"><span>计划总数</span><b>{data?.plans.length || 0}</b></div>
                  <div className="metric"><span>定时任务</span><b>{data?.cron_tasks.length || 0}</b></div>
                  <div className="metric"><span>当前用户</span><b>{user}</b></div>
                </div>
              </article>
            </aside>
          </div>
        ) : <EmptyPanel title="暂无任务计划" description="在对话中描述目标，kemo-agent 会生成计划草案并等待确认。" icon={<ListChecks size={21} />} />
      )}

      {tab === 'cron' && (
        <article className="panel table-panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">C</span><span><strong>周期调度</strong><span>Web 只读展示，不启动第二个调度器</span></span></div><span className="panel-count">{data?.cron_tasks.length || 0}</span></div>
          {data?.cron_tasks.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>任务</th><th>调度</th><th>状态</th><th>最近运行</th><th>下次运行</th></tr></thead><tbody>
            {data.cron_tasks.map((task) => <tr key={task.task_id}><td><span className="table-main"><span className="table-icon">C</span><span><strong>{task.title}</strong><span>{task.task_id}</span></span></span></td><td>{scheduleLabel(task)}</td><td><StatusChip status={task.status} /></td><td>{formatDateTime(task.latest_run_at)}</td><td>{formatDateTime(task.next_run_at)}</td></tr>)}
          </tbody></table></div> : <EmptyPanel title="暂无定时任务" description="定时任务需要由任务系统创建；Web 页面目前保持只读。" icon={<TimerReset size={21} />} />}
        </article>
      )}

      {tab === 'history' && (
        <article className="panel table-panel">
          <div className="panel-head"><div className="panel-title"><span className="panel-title-icon">H</span><span><strong>执行记录</strong><span>来自计划终态和 Cron 最近一次运行</span></span></div><span className="panel-count">{history.length}</span></div>
          {history.length ? <div className="panel-body table-wrap"><table className="module-table"><thead><tr><th>执行项</th><th>类型</th><th>状态</th><th>更新时间</th></tr></thead><tbody>
            {history.map((item) => <tr key={item.id}><td>{item.title}</td><td>{item.kind}</td><td><StatusChip status={item.status} /></td><td>{formatDateTime(item.updatedAt)}</td></tr>)}
          </tbody></table></div> : <EmptyPanel title="暂无执行记录" description="计划或定时任务产生终态后会在这里出现。" />}
        </article>
      )}
    </ModuleFrame>
  )
}
