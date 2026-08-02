import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  Copy,
  File,
  FileText,
  FolderOpen,
  Inbox,
  RadioTower,
  RefreshCw,
  Send,
  Trash2,
  Wifi,
  WifiOff,
} from 'lucide-react'
import { useOutletContext } from 'react-router-dom'
import { checkMessageModule, deleteMessageModule, getMessageStatus } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { ModuleError, ModuleFrame } from '../components/ModuleUi'
import type { MessageLogEntry, MessageTransportSummary } from '../types/api'
import { copyText } from '../utils/clipboard'
import styles from './MessagesPage.module.css'

type LogFilter = 'all' | 'send' | 'receive' | 'file'
const LOG_PAGE_SIZE = 10

function SummaryCard({
  icon,
  label,
  value,
  detail,
  tone = 'purple',
}: {
  icon: ReactNode
  label: string
  value: ReactNode
  detail: string
  tone?: 'purple' | 'green' | 'blue'
}) {
  return <article className={styles.summaryCard}>
    <span className={`${styles.summaryIcon} ${styles[tone]}`}>{icon}</span>
    <span className={styles.summaryCopy}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </span>
  </article>
}

function statusMeta(module: MessageTransportSummary) {
  if (module.connection_status === 'connected') {
    return { label: '连接正常', short: '已连接', tone: styles.success, icon: <Wifi size={14} /> }
  }
  if (module.connection_status === 'error') {
    return { label: '连接异常', short: '连接异常', tone: styles.danger, icon: <AlertCircle size={14} /> }
  }
  return { label: '未连接', short: '未连接', tone: styles.muted, icon: <WifiOff size={14} /> }
}

function StatusBadge({ module, compact = false }: { module: MessageTransportSummary; compact?: boolean }) {
  const meta = statusMeta(module)
  return <span className={`${styles.statusBadge} ${meta.tone}`}>
    {compact ? <i /> : meta.icon}
    {compact ? meta.short : meta.label}
  </span>
}

function displayDateTime(value: string | null) {
  if (!value) return '—'
  const normalized = value.includes('T') ? value : value.replace(' ', 'T')
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).format(date).replaceAll('/', '-')
}

function timePart(value: string) {
  const match = value.match(/(?:T|\s)(\d{2}:\d{2}:\d{2})/)
  return match?.[1] || value
}

function logLabel(log: MessageLogEntry) {
  if (log.kind === 'file') return log.direction === 'send' ? '发送文件' : '接收文件'
  if (log.kind === 'system') return '系统'
  return log.direction === 'send' ? '发送文本' : '接收文本'
}

function ModuleCard({
  module,
  selected,
  checking,
  deleting,
  onSelect,
  onCheck,
  onDelete,
}: {
  module: MessageTransportSummary
  selected: boolean
  checking: boolean
  deleting: boolean
  onSelect: () => void
  onCheck: () => void
  onDelete: () => void
}) {
  const handleKey = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onSelect()
    }
  }
  return <article
    className={`${styles.moduleCard} ${selected ? styles.selected : ''}`}
    role="button"
    tabIndex={0}
    aria-pressed={selected}
    onClick={onSelect}
    onKeyDown={handleKey}
  >
    <header className={styles.cardHeader}>
      <span className={`${styles.moduleIcon} ${module.connection_status === 'error' ? styles.warningIcon : ''}`}><RadioTower size={20} /></span>
      <span className={styles.cardIdentity}>
        <strong>{module.display_name || module.name}</strong>
        <code>{module.path}</code>
      </span>
      <StatusBadge module={module} compact />
    </header>
    <div className={styles.capabilityList}>
      {module.capabilities.map((capability) => <span key={capability}>{capability}</span>)}
    </div>
    <div className={styles.cardMeta}>
      <span>用户：{module.bound_user}</span>
      <span>临时文件 {module.temporary_file_count}</span>
      <span>今日日志 {module.today_log_count}</span>
    </div>
    <div className={styles.cardActions}>
      <button type="button" disabled={checking} aria-busy={checking} onClick={(event) => { event.stopPropagation(); onCheck() }}><RefreshCw className={checking ? styles.spinning : ''} size={14} />{checking ? '检测中…' : '检测连接'}</button>
      <button className={styles.dangerButton} type="button" disabled={deleting} onClick={(event) => { event.stopPropagation(); onDelete() }}><Trash2 size={14} />删除</button>
    </div>
  </article>
}

export function MessagesPage() {
  const { user } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [selectedId, setSelectedId] = useState('')
  const [filter, setFilter] = useState<LogFilter>('all')
  const [logPage, setLogPage] = useState(1)
  const [copiedId, setCopiedId] = useState('')
  const [notice, setNotice] = useState('')
  const [actionError, setActionError] = useState('')
  const [manualRefreshing, setManualRefreshing] = useState(false)

  const query = useQuery({
    queryKey: ['message-status', user],
    queryFn: () => getMessageStatus(user),
    enabled: Boolean(user),
    refetchInterval: 30_000,
  })
  const modules = query.data?.transports || []
  const selected = modules.find((module) => module.id === selectedId) || modules[0] || null

  useEffect(() => {
    if (!modules.length) {
      setSelectedId('')
    } else if (!selectedId || !modules.some((module) => module.id === selectedId)) {
      setSelectedId(modules[0].id)
    }
  }, [modules, selectedId])

  const checkMutation = useMutation({
    mutationFn: (moduleId: string) => checkMessageModule(user, moduleId),
    onMutate: () => { setNotice(''); setActionError('') },
    onSuccess: async (_, moduleId) => {
      await queryClient.invalidateQueries({ queryKey: ['message-status', user] })
      setNotice(`${moduleId} 连接状态已更新`)
    },
    onError: (error: Error) => setActionError(error.message || '连接检测失败'),
  })
  const deleteMutation = useMutation({
    mutationFn: (moduleId: string) => deleteMessageModule(user, moduleId),
    onMutate: () => { setNotice(''); setActionError('') },
    onSuccess: async (_, moduleId) => {
      if (selectedId === moduleId) setSelectedId('')
      await queryClient.invalidateQueries({ queryKey: ['message-status', user] })
      setNotice(`${moduleId} 消息模块已删除`)
    },
    onError: (error: Error) => setActionError(error.message || '消息模块删除失败'),
  })

  const filteredLogs = useMemo(() => {
    const logs = selected?.logs || []
    return logs.filter((log) => {
      if (filter === 'send') return log.direction === 'send'
      if (filter === 'receive') return log.direction === 'receive'
      if (filter === 'file') return log.kind === 'file'
      return true
    })
  }, [filter, selected])
  const logPageCount = Math.max(1, Math.ceil(filteredLogs.length / LOG_PAGE_SIZE))
  const currentLogPage = Math.min(logPage, logPageCount)
  const visibleLogs = useMemo(() => {
    const start = (currentLogPage - 1) * LOG_PAGE_SIZE
    return filteredLogs.slice(start, start + LOG_PAGE_SIZE)
  }, [currentLogPage, filteredLogs])

  useEffect(() => {
    setLogPage((current) => Math.min(current, logPageCount))
  }, [logPageCount])

  useEffect(() => {
    setLogPage(1)
  }, [selected?.id])

  const chooseModule = (module: MessageTransportSummary) => {
    setSelectedId(module.id)
    setFilter('all')
    setLogPage(1)
  }
  const confirmDelete = (module: MessageTransportSummary) => {
    if (module.bound_user !== user) return
    if (window.confirm(`确定删除与当前用户绑定的消息模块“${module.display_name || module.name}”吗？`)) {
      deleteMutation.mutate(module.id)
    }
  }
  const copyModulePath = async (module: MessageTransportSummary) => {
    try {
      await copyText(module.path)
      setCopiedId(module.id)
      window.setTimeout(() => setCopiedId((current) => current === module.id ? '' : current), 1500)
    } catch (error) {
      setActionError(error instanceof Error ? error.message : '复制路径失败')
    }
  }

  const refreshMessageModules = async () => {
    if (manualRefreshing || query.isFetching) return
    setManualRefreshing(true)
    setNotice('')
    setActionError('')
    try {
      const result = await query.refetch()
      if (result.isError) {
        setActionError(result.error instanceof Error ? result.error.message : '消息模块刷新失败')
        return
      }
      setNotice(`消息模块已刷新，共发现 ${result.data?.summary.total_transports ?? 0} 个绑定模块`)
    } finally {
      setManualRefreshing(false)
    }
  }

  return <ModuleFrame
    kicker="External Messaging"
    title="外部消息"
    description="仅显示与当前用户身份绑定的外部消息模块，可查看连接状态、参数配置与收发日志。"
    actions={<button className={`module-btn ${styles.refreshButton}`} type="button" onClick={() => void refreshMessageModules()} disabled={query.isFetching || manualRefreshing} aria-busy={manualRefreshing}>
      <RefreshCw className={manualRefreshing ? styles.spinning : ''} size={15} />{manualRefreshing ? '正在刷新…' : '刷新消息模块'}
    </button>}
  >
    {query.isError && <ModuleError message="外部消息模块读取失败，请检查 message/out 配置和 RuntimeHost 状态。" />}
    {actionError && <div className={styles.actionMessage} role="alert"><AlertCircle size={16} />{actionError}</div>}
    {notice && <div className={`${styles.actionMessage} ${styles.successMessage}`}><CircleCheck size={16} />{notice}</div>}

    <section className={styles.summaryGrid}>
      <SummaryCard icon={<RadioTower size={20} />} label="已绑定模块" value={query.data?.summary.total_transports ?? '—'} detail="仅当前用户可见" />
      <SummaryCard icon={<CircleCheck size={20} />} label="已连接" value={query.data?.summary.connected_transports ?? '—'} detail="平台健康检测结果" tone="green" />
      <SummaryCard icon={<FolderOpen size={20} />} label="临时文件" value={query.data?.summary.temporary_files ?? '—'} detail="message/out/*/files" tone="blue" />
      <SummaryCard icon={<Activity size={20} />} label="今日日志" value={query.data?.summary.today_logs ?? '—'} detail="发送、接收与文件记录" />
    </section>

    <section className={styles.workspace}>
      <article className={styles.modulePanel}>
        <header className={styles.panelHead}>
          <span><strong>已绑定消息模块</strong><small>只展示配置文件中绑定当前用户的模块</small></span>
          <b>{modules.length}</b>
        </header>
        {modules.length ? <div className={styles.moduleList}>
          {modules.map((module) => <ModuleCard
            key={module.id}
            module={module}
            selected={selected?.id === module.id}
            checking={checkMutation.isPending && checkMutation.variables === module.id}
            deleting={deleteMutation.isPending && deleteMutation.variables === module.id}
            onSelect={() => chooseModule(module)}
            onCheck={() => { chooseModule(module); checkMutation.mutate(module.id) }}
            onDelete={() => confirmDelete(module)}
          />)}
        </div> : <div className={styles.emptyState}><RadioTower size={26} /><strong>当前用户未绑定消息模块</strong><p>在 message/out 的模块配置中设置 bound_user 后会显示在这里。</p></div>}
      </article>

      {selected ? <div className={styles.rightColumn}>
        <article className={styles.detailPanel}>
          <header className={styles.detailTitle}><strong>模块详情</strong></header>
          <div className={styles.detailBody}>
            <div className={styles.detailMain}>
              <div className={styles.detailIdentity}>
                <span className={styles.moduleIcon}><RadioTower size={21} /></span>
                <span><span className={styles.detailNameRow}><h3>{selected.display_name || selected.name}</h3><StatusBadge module={selected} /></span><p>{selected.description}</p></span>
              </div>
              <div className={styles.infoGrid}>
                <span><small>绑定用户</small><strong>{selected.bound_user}</strong></span>
                <span><small>最后心跳</small><strong>{displayDateTime(selected.last_check)}</strong></span>
                <span><small>模块路径</small><code>{selected.path}</code></span>
                <span><small>连接平台</small><strong>{selected.platform}</strong></span>
                <span><small>临时文件目录</small><code>{selected.files_path}</code></span>
                <span><small>支持能力</small><strong>{selected.capabilities.join(' / ')}</strong></span>
                <span><small>日志数据库</small><code>{selected.log_path}</code></span>
                <span><small>运行时状态</small><strong>{selected.state}</strong></span>
              </div>
            </div>
            <div className={styles.detailActions}>
              <button type="button" disabled={checkMutation.isPending} onClick={() => checkMutation.mutate(selected.id)}><Activity size={15} />{checkMutation.isPending ? '检测中…' : '检测连接'}</button>
              <button type="button" onClick={() => void copyModulePath(selected)}>{copiedId === selected.id ? <Check size={15} /> : <Copy size={15} />}{copiedId === selected.id ? '已复制' : '复制路径'}</button>
              <button className={styles.dangerButton} type="button" disabled={deleteMutation.isPending} onClick={() => confirmDelete(selected)}><Trash2 size={15} />删除模块</button>
            </div>
          </div>
          {selected.last_error ? <div className={styles.moduleError}><AlertCircle size={14} />{String(typeof selected.last_error === 'object' && selected.last_error && 'message' in selected.last_error ? selected.last_error.message : selected.last_error)}</div> : null}
          <div className={styles.parameterSummary}>
            <strong>参数摘要</strong>
            <div>
              <span><small>状态 API</small><b className={selected.api_imported ? styles.activeParameter : ''}>{selected.api_imported ? '已导入' : '未导入'}</b></span>
              <span><small>轮询间隔</small><b>{selected.polling_interval}</b></span>
              <span><small>健康检测</small><b>{selected.health_interval}</b></span>
              <span><small>文件中转</small><b className={selected.file_relay_enabled ? styles.activeParameter : ''}>{selected.file_relay_enabled ? '已启用' : '未启用'}</b></span>
              <span><small>日志写入</small><b>{selected.log_rotation}</b></span>
            </div>
          </div>
        </article>

        <article className={styles.logPanel}>
          <header className={styles.logHeader}>
            <span><strong>消息日志</strong><small>按时间倒序显示，可用于排查消息接收与发送流程</small></span>
            <div className={styles.logFilters} role="tablist" aria-label="日志筛选">
              {([['all', '全部'], ['send', '发送'], ['receive', '接收'], ['file', '文件']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={filter === value} className={filter === value ? styles.activeFilter : ''} onClick={() => { setFilter(value); setLogPage(1) }}>{label}</button>)}
            </div>
          </header>
          {filteredLogs.length ? <div className={styles.logList}>
            {visibleLogs.map((log) => <div key={log.id} className={`${styles.logRow} ${log.direction === 'send' ? styles.sendLog : styles.receiveLog} ${!log.success ? styles.failedLog : ''}`}>
              <span className={styles.logIcon}>{log.direction === 'send' ? <Send size={13} /> : <Inbox size={13} />}</span>
              <b className={log.direction === 'send' ? styles.sendDirection : styles.receiveDirection}>{log.direction === 'send' ? '发送' : '接收'}</b>
              <time dateTime={log.timestamp}>{timePart(log.timestamp)}</time>
              <span className={styles.logContent}>{log.kind === 'file' ? <File size={14} /> : <FileText size={14} />}<span><small>{logLabel(log)}</small><strong>{log.content}</strong>{log.file_path && <code>{log.file_path}</code>}</span></span>
              {!log.success && <em>失败</em>}
            </div>)}
          </div> : <div className={styles.logEmpty}><FileText size={24} /><strong>没有符合筛选条件的日志</strong><p>模块处理消息后，记录会按时间显示在这里。</p></div>}
          <footer className={styles.logFooter}>
            <span className={styles.logSource}><Activity size={13} /><span>日志数据库：{selected.log_path}{selected.logs_truncated ? ' · 仅展示最近 500 条' : ''}</span></span>
            {filteredLogs.length ? <nav className={styles.logPagination} aria-label="消息日志分页">
              <button type="button" aria-label="上一页日志" disabled={currentLogPage <= 1} onClick={() => setLogPage((current) => Math.max(1, current - 1))}><ChevronLeft size={14} /></button>
              <b>{currentLogPage} / {logPageCount}</b>
              <button type="button" aria-label="下一页日志" disabled={currentLogPage >= logPageCount} onClick={() => setLogPage((current) => Math.min(logPageCount, current + 1))}><ChevronRight size={14} /></button>
            </nav> : null}
          </footer>
        </article>
      </div> : <div className={styles.detailEmpty}><RadioTower size={30} /><strong>选择一个消息模块</strong><p>选择后可查看参数、连接状态与收发日志。</p></div>}
    </section>
  </ModuleFrame>
}
