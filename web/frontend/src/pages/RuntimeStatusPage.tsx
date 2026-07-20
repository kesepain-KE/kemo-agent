import { Activity, Gauge, Server, Timer } from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import type { ShellOutletContext } from '../components/AppShell'
import { MetricCard, ModuleFrame, StatusChip } from '../components/ModuleUi'

export function RuntimeStatusPage() {
  const { overview, refreshOverview } = useOutletContext<ShellOutletContext>()
  const runtime = overview?.runtime_host
  const context = overview?.context
  return <ModuleFrame
    kicker="Runtime Diagnostics"
    title="运行状态"
    description="只读查看 Provider、RuntimeHost、上下文窗口、后台组件和最近活动。"
    actions={<button className="module-btn" onClick={refreshOverview}><Activity size={15} />刷新状态</button>}
  >
    <section className="metric-strip">
      <MetricCard label="Provider" value={overview?.provider.model || '—'} detail={overview?.provider.type || '未读取'} symbol={<Server size={16} />} />
      <MetricCard label="上下文占用" value={context ? `${context.percent}%` : '—'} detail={context ? `${context.usage.total_tokens} / ${context.limit} tokens` : '未读取'} symbol={<Gauge size={16} />} />
      <MetricCard label="当前轮次" value={context?.rounds ?? '—'} detail={context ? `上限 ${context.round_limit}` : '未读取'} symbol={<Timer size={16} />} />
      <MetricCard label="后台宿主" value={runtime?.state || '—'} detail={`${Object.keys(runtime?.components || {}).length} 个组件`} symbol={<Activity size={16} />} />
    </section>
    <div className="page-grid">
      <article className="panel">
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><Server size={15} /></span><span><strong>RuntimeHost 组件</strong><span>后台调度、Cron、记忆维护和外部消息</span></span></div></div>
        <div className="compact-list">{Object.entries(runtime?.components || {}).map(([key, component]) => <div className="compact-row" key={key}><span><strong>{component.name || key}</strong><small>{component.kind || 'runtime component'}</small></span><StatusChip status={component.state === 'running' ? 'enabled' : component.state === 'error' ? 'warning' : 'gray'}>{component.state || 'unknown'}</StatusChip></div>)}{!runtime && <span className="drawer-empty">正在读取 RuntimeHost…</span>}</div>
      </article>
      <article className="panel">
        <div className="panel-head"><div className="panel-title"><span className="panel-title-icon"><Activity size={15} /></span><span><strong>最近活动</strong><span>来自会话、任务和 Cron 的状态汇总</span></span></div></div>
        <div className="compact-list">{overview?.activities.map((item, index) => <div className="compact-row" key={`${item.type}:${item.updated_at}:${index}`}><span><strong>{item.title}</strong><small>{item.detail}</small></span><span><StatusChip status={item.status === 'failed' ? 'warning' : 'enabled'}>{item.status}</StatusChip><small>{item.updated_at}</small></span></div>)}{overview && !overview.activities.length && <span className="drawer-empty">暂无活动记录。</span>}</div>
      </article>
    </div>
  </ModuleFrame>
}
