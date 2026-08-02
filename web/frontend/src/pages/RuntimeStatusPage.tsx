import { useMemo, useState, type ReactNode } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  Activity,
  Braces,
  Check,
  Clock3,
  Copy,
  Database,
  Gauge,
  MessagesSquare,
  RefreshCw,
  Router,
  ServerCog,
  TimerReset,
  Zap,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { getRuntimeStatus } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { EmptyPanel, formatDateTime, ModuleError, ModuleFrame } from '../components/ModuleUi'
import type { RuntimeHealth, RuntimeStatusResponse } from '../types/api'
import styles from './RuntimeStatusPage.module.css'

const sectionLabels: Record<string, string> = {
  user_soul: '用户人格',
  global_soul: '全局人格',
  agents_manual: '智能体运行手册',
  global_subagent_registry: '全局子代理注册表',
  user_subagent_registry: '用户子代理注册表',
  plugins: '基础插件',
  skills: '技能指令',
  knowledge_index: '知识库索引',
  kemo_graph: 'Kemo Graph',
  permanent_memory: '永久记忆',
  important_memory: '临时重要记忆',
  'temporary_memory:half_year': '半年记忆',
  'temporary_memory:one_month': '月记忆',
  'temporary_memory:seven_days': '周记忆',
  task_plan: '任务计划',
  expand_data: '拓展数据',
  perception: '感知数据',
}

const tierLabels: Record<string, string> = {
  seven_days: '周记忆',
  one_month: '月记忆',
  half_year: '半年记忆',
  permanent: '长期记忆',
  important: '临时重要记忆',
}

type RuntimeTab = 'prompt' | 'tokens' | 'api' | 'external' | 'maintenance'
type SummaryKey = 'api' | 'context' | 'rounds' | 'routes'

const runtimeTabs: Array<{ id: RuntimeTab; label: string }> = [
  { id: 'prompt', label: '系统提示词上下文预览' },
  { id: 'tokens', label: '今日 Token 情况' },
  { id: 'api', label: '用户 API 配置' },
  { id: 'external', label: '外部组件与消息路由' },
  { id: 'maintenance', label: '调度与维护' },
]

function formatNumber(value: number) {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatCompact(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`
  return String(value)
}

function providerLabel(type: string) {
  if (type === 'kemo') return 'Kemo Provider'
  if (type === 'chat') return 'Chat Completions'
  return type || '未配置'
}

function thinkingLabel(value: string) {
  if (!value || value === 'provider_default') return 'Provider 默认'
  return value
}

function stateLabel(state: string) {
  const labels: Record<string, string> = {
    injected: '已注入',
    loaded: '已加载',
    disabled: '未启用',
    empty: '空段',
    truncated: '已截断',
    running: '运行中',
    enabled: '已启用',
    approved: '已批准',
    pending: '等待确认',
    paused: '已暂停',
    success: '成功',
    failed: '失败',
    cancelled: '已取消',
    recorded: '已执行',
    stopped: '已停止',
    error: '异常',
  }
  return labels[state] || state || '未知'
}

function healthLabel(health: RuntimeHealth) {
  return health === 'healthy' ? '正常' : health === 'warning' ? '警告' : health === 'error' ? '异常' : '离线'
}

function statusTone(state: string) {
  if (['healthy', 'success', 'running', 'enabled', 'approved', 'injected', 'loaded'].includes(state)) return 'success'
  if (['warning', 'paused', 'pending', 'truncated', 'recorded'].includes(state)) return 'warning'
  if (['error', 'failed'].includes(state)) return 'danger'
  return 'muted'
}

async function copyText(value: string) {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value)
    return
  }
  const textarea = document.createElement('textarea')
  textarea.value = value
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  textarea.remove()
}

export function RuntimeStatusPage() {
  const { user, sessionId } = useOutletContext<ShellOutletContext>()
  const [activeTab, setActiveTab] = useState<RuntimeTab>('prompt')
  const query = useQuery({
    queryKey: ['runtime-status', user, sessionId, activeTab],
    queryFn: () => getRuntimeStatus(user, sessionId, ['summary', activeTab]),
    enabled: Boolean(user),
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  })

  return (
    <ModuleFrame
      className={styles.runtimeView}
      kicker="Runtime Diagnostics"
      title="运行状态"
      description="统一查看当前用户的上下文、系统提示词、Token、组件健康、记忆、任务、系统 Cron 与外部消息路由。"
      actions={(
        <button className="module-btn primary" disabled={query.isFetching} onClick={() => void query.refetch()}>
          <RefreshCw size={15} className={query.isFetching ? styles.spinning : undefined} />
          刷新运行状态
        </button>
      )}
    >
      {query.isError ? <ModuleError message="运行状态读取失败，请检查用户配置或 RuntimeHost。" /> : null}
      {query.data ? (
        <RuntimeDashboard
          data={query.data}
          activeTab={activeTab}
          onActiveTabChange={setActiveTab}
          sectionPending={query.isPlaceholderData}
        />
      ) : !query.isError ? <LoadingState /> : null}
    </ModuleFrame>
  )
}

function LoadingState() {
  return <div className={styles.loading} aria-label="正在读取运行状态">
    <Activity size={20} />
    <span>正在聚合运行状态…</span>
  </div>
}

function RuntimeDashboard({
  data,
  activeTab,
  onActiveTabChange,
  sectionPending,
}: {
  data: RuntimeStatusResponse
  activeTab: RuntimeTab
  onActiveTabChange: (tab: RuntimeTab) => void
  sectionPending: boolean
}) {
  const [activeSummary, setActiveSummary] = useState<SummaryKey | null>('context')
  const [promptView, setPromptView] = useState<'preview' | 'components'>('preview')
  const [copied, setCopied] = useState(false)
  const connected = data.message_routes.summary.connected_transports
  const routes = data.message_routes.summary.total_transports

  const copyPrompt = async () => {
    await copyText(data.prompt.content)
    setCopied(true)
    window.setTimeout(() => setCopied(false), 1600)
  }

  const revealTab = (tab: RuntimeTab) => {
    window.requestAnimationFrame(() => {
      document.getElementById(`runtime-tab-${tab}`)?.scrollIntoView?.({ block: 'nearest', inline: 'nearest' })
    })
  }

  const openFromSummary = (summary: SummaryKey, tab: RuntimeTab) => {
    setActiveSummary(summary)
    onActiveTabChange(tab)
    revealTab(tab)
  }

  const openTab = (tab: RuntimeTab) => {
    onActiveTabChange(tab)
    setActiveSummary(tab === 'prompt' ? 'context' : tab === 'api' ? 'api' : tab === 'external' ? 'routes' : null)
    revealTab(tab)
  }

  return <div className={styles.dashboard}>
    <div className={styles.generatedAt}>
      <span className={styles.userBadge}>{data.user}</span>
      <span>{data.session_id ? `当前会话 ${data.session_id}` : '未选择当前会话'}</span>
      <time dateTime={data.generated_at}>更新于 {formatDateTime(data.generated_at)}</time>
    </div>

    <section className={styles.summaryGrid} aria-label="运行状态摘要">
      <SummaryCard title="API 配置" target="用户 API 配置" icon={<ServerCog size={18} />} active={activeSummary === 'api'} onClick={() => openFromSummary('api', 'api')} badge={data.api.configured ? '已配置' : '待配置'} tone={data.api.configured ? 'success' : 'danger'}>
        <strong className={styles.modelValue}>{data.api.model || '未配置模型'}</strong>
        <small>{providerLabel(data.api.type)}</small>
      </SummaryCard>
      <SummaryCard title="上下文占用情况" target="系统提示词上下文预览" icon={<Gauge size={18} />} active={activeSummary === 'context'} onClick={() => openFromSummary('context', 'prompt')}>
        <div className={styles.contextValue}><strong>{formatCompact(data.context.used_tokens)} / {formatCompact(data.context.max_tokens)}</strong><b>{data.context.percent.toFixed(1)}%</b></div>
        <Progress value={data.context.percent} />
      </SummaryCard>
      <SummaryCard title="对话轮次" target="系统提示词上下文预览" icon={<MessagesSquare size={18} />} active={activeSummary === 'rounds'} onClick={() => openFromSummary('rounds', 'prompt')}>
        <strong>{data.context.rounds}</strong>
        <small>{data.context.selected ? `当前会话 · 上限 ${data.context.round_limit}` : '未选择会话'}</small>
      </SummaryCard>
      <SummaryCard title="外部消息路由" target="外部组件与消息路由" icon={<Router size={18} />} active={activeSummary === 'routes'} onClick={() => openFromSummary('routes', 'external')} badge={routes > 0 && connected === routes ? '全部健康' : routes ? '部分异常' : '未绑定'} tone={routes > 0 && connected === routes ? 'success' : routes ? 'warning' : 'muted'}>
        <strong>{connected} / {routes}</strong>
        <small>{routes ? `连接健康率 ${Math.round(connected * 100 / routes)}%` : '当前用户无绑定模块'}</small>
      </SummaryCard>
    </section>

    <nav className={styles.sectionTabs} role="tablist" aria-label="运行状态栏目">
      {runtimeTabs.map((tab) => <button
        type="button"
        role="tab"
        id={`runtime-tab-${tab.id}`}
        aria-controls={`runtime-panel-${tab.id}`}
        aria-selected={activeTab === tab.id}
        className={activeTab === tab.id ? styles.activeSectionTab : undefined}
        key={tab.id}
        onClick={() => openTab(tab.id)}
      >{tab.label}</button>)}
    </nav>

    <section
      className={styles.tabPanel}
      role="tabpanel"
      id={`runtime-panel-${activeTab}`}
      aria-labelledby={`runtime-tab-${activeTab}`}
      aria-busy={sectionPending}
    >
      {sectionPending ? <SectionLoading /> : null}
      {!sectionPending && activeTab === 'prompt' ? <article className={`${styles.panel} ${styles.promptPanel}`}>
          <PanelHeader icon={<Braces size={16} />} title="系统提示词上下文预览" detail={`${formatNumber(data.prompt.total_chars)} 字符 · 约 ${formatNumber(data.prompt.estimated_tokens)} Tokens`} />
          <div className={styles.promptTabs} role="tablist" aria-label="系统提示词查看方式">
            <button role="tab" aria-selected={promptView === 'preview'} className={promptView === 'preview' ? styles.activeTab : ''} onClick={() => setPromptView('preview')}>完整上下文预览</button>
            <button role="tab" aria-selected={promptView === 'components'} className={promptView === 'components' ? styles.activeTab : ''} onClick={() => setPromptView('components')}>拼接组件状态</button>
          </div>
          {promptView === 'preview'
            ? <PromptPreview content={data.prompt.content} components={data.prompt.components} />
            : <PromptComponents components={data.prompt.components} />}
          <footer className={styles.promptFooter}>
            <span>{data.prompt.components.filter((item) => item.state === 'injected' || item.state === 'truncated').length} / {data.prompt.components.length} 个段已进入上下文</span>
            <button onClick={() => void copyPrompt()}>{copied ? <Check size={14} /> : <Copy size={14} />}{copied ? '已复制' : '复制上下文'}</button>
          </footer>
        </article> : null}
      {!sectionPending && activeTab === 'tokens' ? <TokenPanel data={data} /> : null}
      {!sectionPending && activeTab === 'api' ? <ApiPanel data={data} /> : null}
      {!sectionPending && activeTab === 'external' ? <div className={styles.externalGrid}>
        <HealthPanel data={data} />
        <MessageRoutePanel data={data} />
      </div> : null}
      {!sectionPending && activeTab === 'maintenance' ? <div className={styles.maintenanceGrid}>
        <MemoryPanel data={data} />
        <TaskPanel data={data} />
        <SystemCronPanel data={data} />
      </div> : null}
    </section>
  </div>
}

function SectionLoading() {
  return <div className={styles.loading} aria-label="正在读取当前栏目">
    <Activity size={18} />
    <span>正在读取当前栏目…</span>
  </div>
}

function SummaryCard({ title, target, icon, badge, tone = 'muted', active, onClick, children }: { title: string; target: string; icon: ReactNode; badge?: string; tone?: string; active: boolean; onClick: () => void; children: ReactNode }) {
  return <button type="button" className={`${styles.summaryCard} ${active ? styles.activeSummaryCard : ''}`} aria-pressed={active} aria-label={`${title}，打开${target}栏目`} onClick={onClick}>
    <header><span className={styles.iconBox}>{icon}</span><h3>{title}</h3>{badge ? <StatusPill tone={tone}>{badge}</StatusPill> : null}</header>
    <div className={styles.summaryBody}>{children}</div>
  </button>
}

function StatusPill({ tone, children }: { tone: string; children: ReactNode }) {
  return <span className={`${styles.statusPill} ${styles[`tone_${tone}`] || ''}`}><i />{children}</span>
}

function Progress({ value }: { value: number }) {
  return <span className={styles.progress}><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></span>
}

function Sparkline({ values }: { values: number[] }) {
  const points = useMemo(() => {
    if (!values.length) return ''
    const max = Math.max(...values, 1)
    return values.map((value, index) => `${(index / Math.max(1, values.length - 1)) * 94 + 3},${35 - (value / max) * 30}`).join(' ')
  }, [values])
  return <svg className={styles.sparkline} viewBox="0 0 100 40" aria-hidden="true"><polyline points={points} /></svg>
}

function PanelHeader({ icon, title, detail, count }: { icon: ReactNode; title: string; detail?: string; count?: ReactNode }) {
  return <header className={styles.panelHeader}><span className={styles.iconBox}>{icon}</span><span><h3>{title}</h3>{detail ? <small>{detail}</small> : null}</span>{count !== undefined ? <b>{count}</b> : null}</header>
}

function PromptPreview({ content, components }: { content: string; components: RuntimeStatusResponse['prompt']['components'] }) {
  return <div className={styles.promptLayout}>
    <div className={styles.codePreview} aria-label="完整系统提示词">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
    <div className={styles.componentSidebar}>
      <div className={styles.tableHead}><span>模块</span><span>状态</span><span>Tokens</span></div>
      {components.map((item) => <div className={styles.componentRow} key={item.id}><strong title={item.id}>{sectionLabels[item.name] || item.name}</strong><StatusPill tone={statusTone(item.state)}>{stateLabel(item.state)}</StatusPill><span>{formatNumber(item.tokens)}</span></div>)}
    </div>
  </div>
}

function PromptComponents({ components }: { components: RuntimeStatusResponse['prompt']['components'] }) {
  return <div className={styles.componentCards}>
    {components.map((item) => <article key={item.id}>
      <span className={styles.iconBox}><Braces size={15} /></span>
      <span><strong>{sectionLabels[item.name] || item.name}</strong></span>
      <StatusPill tone={statusTone(item.state)}>{stateLabel(item.state)}</StatusPill>
      <code>{formatNumber(item.tokens)} Tokens</code>
    </article>)}
  </div>
}

function TokenPanel({ data }: { data: RuntimeStatusResponse }) {
  const items = [
    ['发送 Tokens', data.tokens.sent_tokens],
    ['接收 Tokens', data.tokens.received_tokens],
    ['缓存 Tokens', data.tokens.cached_tokens],
    ['Token 缓存率', `${data.tokens.cache_rate.toFixed(1)}%`],
    ['API 请求数量', data.tokens.request_count],
  ] as const
  return <article className={`${styles.panel} ${styles.tokenDetailPanel}`}>
    <PanelHeader icon={<Zap size={16} />} title="今日 Token 情况" detail={`${data.tokens.date} · ${data.tokens.timezone}${data.tokens.estimated ? ' · 含估算值' : ''}`} />
    <div className={styles.tokenOverview}>
      <span><small>今日 Token 总量</small><strong>{formatNumber(data.tokens.total_tokens)}</strong><em>发送与接收合计</em></span>
      <Sparkline values={data.tokens.trend} />
    </div>
    <div className={styles.tokenMetrics}>{items.map(([label, value]) => <span key={label}><small>{label}</small><strong>{typeof value === 'number' ? formatNumber(value) : value}</strong></span>)}</div>
  </article>
}

function ApiPanel({ data }: { data: RuntimeStatusResponse }) {
  const fields = [
    ['API 类型', providerLabel(data.api.type)],
    ['model', data.api.model || '—'],
    ['base_url', data.api.base_url || '—'],
    ['思考程度', thinkingLabel(data.api.thinking_effort)],
  ]
  return <article className={`${styles.panel} ${styles.apiDetailPanel}`}>
    <PanelHeader icon={<ServerCog size={16} />} title="用户 API 配置" detail={data.api.configured ? '凭据与基础字段已配置' : '配置尚不完整'} />
    <div className={styles.apiGrid}>{fields.map(([label, value]) => <span key={label}><small>{label}</small><strong title={value}>{value}</strong></span>)}</div>
  </article>
}

function HealthPanel({ data }: { data: RuntimeStatusResponse }) {
  return <article className={`${styles.panel} ${styles.healthPanel}`}>
    <PanelHeader icon={<Activity size={16} />} title="感知组件与拓展组件健康状况" detail={`RuntimeHost ${data.runtime_host.state}`} />
    <div className={styles.healthColumns}>
      <HealthGroup title="感知组件" items={data.components.sense} />
      <HealthGroup title="拓展组件" items={data.components.expand} />
    </div>
  </article>
}

function HealthGroup({ title, items }: { title: string; items: Array<{ id: string; name: string; health: RuntimeHealth; state: string; description: string }> }) {
  return <section className={styles.healthGroup}><header><h4>{title}</h4><span>{items.length} 个组件</span></header><div className={styles.healthRows}>
    {items.map((item) => <article className={styles.healthCard} key={item.id} title={item.description}>
      <span className={styles.healthIdentity}><span className={styles.healthIcon}><Activity size={17} /></span><span><strong>{item.name}</strong><small>{item.description || '暂无组件说明'}</small></span></span>
      <span className={styles.healthValue}><small>健康状态</small><strong><i className={styles[`health_${item.health}`]} />{healthLabel(item.health)}</strong></span>
      <StatusPill tone={statusTone(item.state)}>{stateLabel(item.state)}</StatusPill>
    </article>)}
    {!items.length ? <p>当前没有可见组件</p> : null}
  </div></section>
}

function CompactPanel({ icon, title, detail, children }: { icon: ReactNode; title: string; detail: string; children: ReactNode }) {
  return <article className={`${styles.panel} ${styles.compactPanel}`}><PanelHeader icon={icon} title={title} detail={detail} /><div className={styles.compactBody}>{children}</div></article>
}

function CompactEmpty({ title, description, icon }: { title: string; description: string; icon: ReactNode }) {
  return <div className={styles.compactEmpty}><EmptyPanel title={title} description={description} icon={icon} /></div>
}

function MemoryPanel({ data }: { data: RuntimeStatusResponse }) {
  return <CompactPanel icon={<Database size={16} />} title="今日记忆更新与升级" detail={`${data.memory.updated_today} 个文件 · ${data.memory.upgraded_today} 个已升级`}>
    {data.memory.updates.map((item) => <div className={styles.memoryRow} key={item.id}><span><strong>{item.filename}</strong><small>{tierLabels[item.tier] || item.tier} · 权重 {item.weight}</small></span><time>{formatDateTime(item.updated_at)}</time><StatusPill tone={item.upgraded ? 'success' : item.upgraded === false ? 'muted' : 'warning'}>{item.upgraded ? '已升级' : item.upgraded === false ? '未升级' : '未记录'}</StatusPill></div>)}
    {!data.memory.updates.length ? <CompactEmpty title="今日暂无记忆更新" description="今天没有检测到新增或修改的记忆文件。" icon={<Database size={18} />} /> : null}
  </CompactPanel>
}

function TaskPanel({ data }: { data: RuntimeStatusResponse }) {
  return <CompactPanel icon={<TimerReset size={16} />} title="当前定时任务与任务计划" detail={`${data.tasks.items.length} 个当前项目`}>
    {data.tasks.items.map((item) => <div className={styles.taskRow} key={`${item.kind}:${item.id}`}><span><strong>{item.title}</strong><small>{item.kind === 'plan' ? '任务计划' : '定时任务'} · {item.trigger}</small></span><time>{item.next_run_at ? formatDateTime(item.next_run_at) : '—'}</time><StatusPill tone={statusTone(item.status)}>{stateLabel(item.status)}</StatusPill></div>)}
    {!data.tasks.items.length ? <CompactEmpty title="没有当前任务" description="当前没有等待、运行或暂停的计划与定时任务。" icon={<TimerReset size={18} />} /> : null}
  </CompactPanel>
}

function SystemCronPanel({ data }: { data: RuntimeStatusResponse }) {
  return <CompactPanel icon={<Clock3 size={16} />} title="系统及定时任务执行记录" detail={`cron/task_cron_system · ${data.system_cron.tracking === 'execution_log' ? '精确日志' : '任务状态记录'}`}>
    {data.system_cron.executions.map((item) => <div className={styles.cronRow} key={item.id}><span><strong>{item.title}</strong><small>{item.task_id}</small></span><time>{formatDateTime(item.executed_at)}</time><span>{item.duration_ms ? `${(item.duration_ms / 1000).toFixed(2)}s` : '—'}</span><StatusPill tone={statusTone(item.status)}>{stateLabel(item.status)}</StatusPill></div>)}
    {!data.system_cron.executions.length ? <CompactEmpty title="尚无系统任务记录" description="系统任务执行后会在这里显示执行时间与结果。" icon={<Clock3 size={18} />} /> : null}
  </CompactPanel>
}

function MessageRoutePanel({ data }: { data: RuntimeStatusResponse }) {
  return <CompactPanel icon={<Router size={16} />} title="外部消息路由连接状态" detail={`${data.message_routes.summary.connected_transports} / ${data.message_routes.summary.total_transports} 个已连接`}>
    {data.message_routes.routes.map((item) => <article className={styles.routeRow} key={item.id}>
      <span className={styles.routeIdentity}><span className={styles.routeIcon}><Router size={17} /></span><span><strong>{item.name}</strong><small>{item.platform} · {item.description}</small></span></span>
      <span className={styles.routeMetric}><small>响应延迟</small><strong>{item.latency_ms == null ? '—' : `${item.latency_ms}ms`}</strong></span>
      <StatusPill tone={statusTone(item.health)}>{healthLabel(item.health)}</StatusPill>
    </article>)}
    {!data.message_routes.routes.length ? <CompactEmpty title="当前用户未绑定外部消息" description="绑定消息模块后会显示连接和延迟状态。" icon={<Router size={18} />} /> : null}
  </CompactPanel>
}
