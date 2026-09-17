import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  AlertTriangle,
  Clock3,
  Database,
  Layers3,
  LockKeyhole,
  MemoryStick,
  MessageSquareText,
  RadioTower,
  RefreshCw,
  ScanLine,
  ServerCog,
  TerminalSquare,
  Waypoints,
  type LucideIcon,
} from 'lucide-react'
import { getRuntimeLogs } from '../api/client'
import { formatDateTime } from '../components/ModuleUi'
import type { RuntimeLogCategory, RuntimeLogEntry } from '../types/api'
import styles from './RuntimeLogsPanel.module.css'

const categories: Array<{ key: RuntimeLogCategory; label: string; icon: LucideIcon }> = [
  { key: 'all', label: '全部', icon: Layers3 },
  { key: 'backend', label: '后端日志', icon: ServerCog },
  { key: 'threads', label: '后端线程', icon: Waypoints },
  { key: 'terminal', label: '终端日志', icon: TerminalSquare },
  { key: 'message', label: '消息日志', icon: MessageSquareText },
]

const categoryLabels: Record<Exclude<RuntimeLogCategory, 'all'>, string> = {
  backend: '后端日志',
  threads: '后端线程',
  terminal: '终端日志',
  message: '消息日志',
}

const categoryIcons: Record<Exclude<RuntimeLogCategory, 'all'>, LucideIcon> = {
  backend: ServerCog,
  threads: Waypoints,
  terminal: TerminalSquare,
  message: MessageSquareText,
}

const states: Record<string, string> = {
  success: '成功', completed: '完成', failed: '失败', error: '失败', cancelled: '已取消',
  running: '运行中', starting: '启动中', stopping: '停止中', stopped: '已停止', recorded: '已记录',
}

const sources: Record<RuntimeLogEntry['source'], { label: string; icon: LucideIcon }> = {
  sqlite: { label: '持久化记录', icon: Database },
  memory: { label: '本进程内存', icon: MemoryStick },
  snapshot: { label: '实时快照', icon: ScanLine },
}

const categoryHints: Record<RuntimeLogCategory, string> = {
  all: '汇总持久化记录、进程内诊断摘要与线程快照；这里是有界的近期视图，不代表完整历史。',
  backend: '系统及当前用户的定时任务、工具调用与 RuntimeHost 组件生命周期摘要。',
  threads: '当前后端进程线程快照，不是历史执行轨迹；不会展示线程私有数据。',
  terminal: '展示启动 kemo-agent 时可见终端的标准输出与标准错误；内容只保留当前进程内的安全快照，重启后重新开始记录。',
  message: '仅展示当前用户的消息收发元数据，不复制消息正文、会话标识及附件路径。',
}

function statusTone(status: string) {
  if (status === 'failed' || status === 'error') return 'danger'
  if (status === 'running' || status === 'starting' || status === 'stopping') return 'active'
  if (status === 'success' || status === 'completed') return 'success'
  return 'muted'
}

function formatTerminalTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(date)
}

function StatCard({ icon, label, value, detail, tone = 'default' }: {
  icon: ReactNode
  label: string
  value: ReactNode
  detail: string
  tone?: 'default' | 'terminal' | 'danger' | 'live'
}) {
  return <article className={`${styles.statCard} ${styles[`stat_${tone}`] || ''}`}>
    <span className={styles.statIcon}>{icon}</span>
    <span className={styles.statCopy}><small>{label}</small><strong>{value}</strong><em>{detail}</em></span>
  </article>
}

function TerminalOutput({ entries, summary }: {
  entries: RuntimeLogEntry[]
  summary?: {
    window_size: number
    loaded_items: number
    stdout_items: number
    stderr_items: number
    process_local: boolean
    retention_seconds: number
  } | null
}) {
  const screenRef = useRef<HTMLDivElement>(null)
  const previousLastId = useRef('')
  const [following, setFollowing] = useState(true)
  const [unseenCount, setUnseenCount] = useState(0)
  const stderrCount = summary?.stderr_items ?? entries.filter((entry) => entry.stream === 'stderr' || entry.status === 'error' || entry.status === 'failed').length
  const stdoutCount = summary?.stdout_items ?? Math.max(0, entries.length - stderrCount)
  const lastEntryId = entries.at(-1)?.id || ''

  useEffect(() => {
    const screen = screenRef.current
    const hasNewOutput = Boolean(lastEntryId && lastEntryId !== previousLastId.current)
    const isFirstLoad = !previousLastId.current
    if (screen && (following || isFirstLoad)) {
      screen.scrollTop = screen.scrollHeight
      setUnseenCount(0)
    } else if (hasNewOutput) {
      setUnseenCount((count) => count + 1)
    }
    previousLastId.current = lastEntryId
  }, [following, lastEntryId])

  const handleScroll = () => {
    const screen = screenRef.current
    if (!screen) return
    const atBottom = screen.scrollTop + screen.clientHeight >= screen.scrollHeight - 18
    setFollowing(atBottom)
    if (atBottom) setUnseenCount(0)
  }

  return <section className={styles.terminalWorkspace} aria-label="kemo-agent 启动终端只读输出">
    <div className={styles.terminalSummary} aria-label="终端采集摘要">
      <article><span className={styles.terminalMetricIcon}><RadioTower size={17} /></span><span><small>采集状态</small><strong>实时接收</strong><em>仅当前 Web 进程</em></span></article>
      <article><span className={styles.terminalMetricIcon}><TerminalSquare size={17} /></span><span><small>载入窗口</small><strong>{summary?.loaded_items ?? entries.length} / {summary?.window_size ?? 300}</strong><em>按原始顺序展示</em></span></article>
      <article><span className={`${styles.terminalMetricIcon} ${styles.stdoutMetric}`}><ScanLine size={17} /></span><span><small>标准输出</small><strong>{stdoutCount}</strong><em>启动与运行信息</em></span></article>
      <article><span className={`${styles.terminalMetricIcon} ${styles.stderrMetric}`}><AlertTriangle size={17} /></span><span><small>标准错误</small><strong>{stderrCount}</strong><em>错误与警告输出</em></span></article>
    </div>
    <div className={styles.terminalConsole}>
    <header className={styles.terminalHeader}>
      <div><span className={styles.terminalIcon}><TerminalSquare size={18} /></span><span><strong>kemo-agent 启动终端</strong><small>最近 {summary?.window_size ?? 300} 行内存窗口 · 新输出从底部出现</small></span></div>
      <span className={styles.readOnlyBadge}><LockKeyhole size={12} />只读</span>
    </header>
    <div className={styles.terminalScreen} ref={screenRef} role="log" aria-label="启动终端输出内容" onScroll={handleScroll}>
      {entries.map((entry) => {
        const isError = entry.stream === 'stderr' || entry.status === 'error' || entry.status === 'failed'
        return <div className={`${styles.terminalLine} ${isError ? styles.terminalErrorLine : ''}`} key={entry.id}>
          <time dateTime={entry.occurred_at}>{formatTerminalTime(entry.occurred_at)}</time>
          <span className={isError ? styles.stderr : styles.stdout}>{isError ? 'ERR' : 'OUT'}</span>
          <code>{entry.title}</code>
        </div>
      })}
      {!entries.length ? <div className={styles.terminalEmpty}><TerminalSquare size={26} /><strong>当前启动终端暂无输出</strong><span>重启 kemo-agent 后，新产生的终端输出会显示在这里。</span></div> : null}
    </div>
    <footer className={styles.terminalFooter}>
      <span><i />{following ? '实时跟随最新输出' : `已暂停跟随${unseenCount ? ` · 底部有 ${unseenCount} 条新输出` : ''}`}</span>
      <span>当前载入 {entries.length} 行 · {stderrCount} 行标准错误</span>
    </footer>
    </div>
  </section>
}

export function RuntimeLogsPanel({ user, className = '' }: { user: string; className?: string }) {
  return <LogView key={user} user={user} className={className} />
}

function LogView({ user, className }: { user: string; className: string }) {
  const [category, setCategory] = useState<RuntimeLogCategory>('all')
  const [page, setPage] = useState(1)
  const forceRefresh = useRef(false)
  const query = useQuery({
    queryKey: ['runtime-logs', user, category, page],
    queryFn: () => {
      const refresh = forceRefresh.current
      forceRefresh.current = false
      return getRuntimeLogs(user, category, page, refresh)
    },
    enabled: Boolean(user), staleTime: 5_000, gcTime: 60_000,
    refetchInterval: category === 'terminal' ? 2_000 : 10_000, refetchIntervalInBackground: false,
  })
  const data = query.data
  const selectedCategory = categories.find((item) => item.key === category) || categories[0]
  const SelectedIcon = selectedCategory.icon
  const abnormalCount = data?.entries.filter((entry) => entry.status === 'failed' || entry.status === 'error').length ?? 0

  return <section className={`${styles.panel} ${className}`} aria-label="执行记录日志">
    <header className={styles.header}>
      <div className={styles.headerIdentity}>
        <span className={styles.headerIcon}><Activity size={20} /></span>
        <div><h3>执行记录日志</h3><p>分类追踪后端、终端与消息链路 · 前台每 10 秒自动更新</p></div>
      </div>
      <div className={styles.headerActions}>
        <span className={styles.liveBadge}><i />{query.isFetching ? '正在同步' : '实时监测'}</span>
        <button type="button" disabled={query.isFetching} onClick={() => { forceRefresh.current = true; void query.refetch() }}>
          <RefreshCw size={14} className={query.isFetching ? styles.spinning : undefined} />刷新日志
        </button>
      </div>
    </header>

    <div className={styles.stats} aria-label="日志摘要">
      <StatCard icon={<Layers3 size={17} />} label="当前分类" value={data?.pagination.total_items ?? 0} detail={`${selectedCategory.label}近期记录`} />
      <StatCard icon={<TerminalSquare size={17} />} label="启动终端" value={data?.counts.terminal ?? 0} detail="标准输出与标准错误" tone="terminal" />
      <StatCard icon={<AlertTriangle size={17} />} label="本页异常" value={abnormalCount} detail="失败或错误状态" tone={abnormalCount ? 'danger' : 'default'} />
      <StatCard icon={<Clock3 size={17} />} label="数据状态" value={query.isFetching ? '同步中' : data?.cache.hit ? '缓存' : '最新'} detail={data ? `更新于 ${formatDateTime(data.generated_at)}` : '等待首次读取'} tone="live" />
    </div>

    <div className={styles.tabs} role="tablist" aria-label="执行记录分类">
      {categories.map(({ key, label, icon: Icon }) => <button type="button" role="tab" key={key}
        aria-label={label} aria-selected={category === key} aria-controls="runtime-log-records"
        onClick={() => { setCategory(key); setPage(1) }}>
        <Icon size={14} /><span>{label}</span><b>{data?.counts[key] ?? 0}</b>
      </button>)}
    </div>

    {category !== 'terminal' ? <div className={`${styles.categoryNotice} ${styles[`notice_${category}`] || ''}`}>
      <SelectedIcon size={16} />
      <p><strong>{selectedCategory.label}</strong><span>{categoryHints[category]}</span></p>
    </div> : null}

    {query.isError ? <p role="alert" className={styles.error}>日志读取失败，请刷新重试。{data ? '下方为上次读取的记录。' : ''}</p> : null}
    {data?.source_errors.map((error) => <p role="alert" className={styles.error} key={error}>{error}</p>)}

    <div id="runtime-log-records" role="tabpanel" aria-label={selectedCategory.label} aria-busy={query.isFetching} className={`${styles.body} ${category === 'terminal' ? styles.terminalBody : ''}`}>
      {query.isPending ? <p className={styles.empty}>正在读取日志…</p> : null}
      {data && category === 'terminal' ? <TerminalOutput entries={data.entries} summary={data.terminal} /> : null}
      {data?.entries.map((entry) => {
        if (category === 'terminal') return null
        const EntryIcon = categoryIcons[entry.category]
        const SourceIcon = sources[entry.source].icon
        return <article key={entry.id} className={`${styles.logCard} ${styles[`category_${entry.category}`] || ''}`}>
          <span className={styles.entryIcon}><EntryIcon size={17} /></span>
          <span className={styles.identity}><strong title={entry.title}>{entry.title}</strong><small>{categoryLabels[entry.category]}</small></span>
          <div className={styles.metadata}>
            <span><Clock3 size={13} /><small>发生时间</small><strong>{formatDateTime(entry.occurred_at)}</strong></span>
            <span><Activity size={13} /><small>执行信息</small><strong>{entry.duration_ms == null ? '无耗时数据' : `${entry.duration_ms} ms`}{entry.exit_code != null ? ` · 退出码 ${entry.exit_code}` : ''}</strong></span>
            <span><SourceIcon size={13} /><small>记录来源</small><strong>{sources[entry.source].label}</strong></span>
          </div>
          <span className={styles.detail} title={entry.detail || undefined}>{entry.detail}</span>
          <span className={`${styles.status} ${styles[`status_${statusTone(entry.status)}`] || ''}`}><i />{states[entry.status] || entry.status}</span>
        </article>
      })}
      {data && category !== 'terminal' && !data.entries.length ? <p className={styles.empty}>{data.source_errors.length ? '部分日志源不可用，暂时没有可显示的记录。' : '当前分类暂无记录。'}</p> : null}
    </div>

    {category !== 'terminal' ? <footer className={styles.footer}>
      <div><strong>近期 {data?.pagination.total_items ?? 0} 条</strong><span>第 {data?.pagination.page ?? page} / {data?.pagination.total_pages ?? 1} 页 · 每页最多 {data?.pagination.page_size ?? 25} 条</span></div>
      <nav aria-label="日志分页">
        <button type="button" disabled={!data?.pagination.has_previous || query.isFetching} onClick={() => setPage((data?.pagination.page ?? page) - 1)}>上一页</button>
        <button type="button" disabled={!data?.pagination.has_next || query.isFetching} onClick={() => setPage((data?.pagination.page ?? page) + 1)}>下一页</button>
      </nav>
    </footer> : null}
    {category !== 'terminal' ? <p className={styles.retention}>持久化源各取最近 200 条，读取缓存 5 秒；内存摘要全进程最多 2048 条 / 1 小时，重启清空；线程快照最多 200 条。</p> : null}
  </section>
}
