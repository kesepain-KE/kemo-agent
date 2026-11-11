import { useId, useState } from 'react'
import { ArrowDownLeft, ArrowUpRight, CheckCircle2, ChevronDown, CircleAlert, Download, FileText, MessageSquare, Terminal } from 'lucide-react'
import { messageFileDownloadUrl } from '../api/client'
import { DocumentMarkdown } from '../components/DocumentMarkdown'
import { t } from '../i18n'
import type { MessageLogEntry, MessageTransportSummary } from '../types/api'
import styles from './Messages.module.css'

export function formatMessageTime(value: string | number | null) {
  if (value === null || value === '') return '—'
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString()
}

export function formatMessageBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function MessagePager({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  if (total <= 1) return null
  return <nav className={styles.pager} aria-label={t('分页')}>
    <button type="button" aria-label={t('上一页')} disabled={page <= 1} onClick={() => onChange(page - 1)}>‹</button>
    <span>{page} / {total}</span>
    <button type="button" aria-label={t('下一页')} disabled={page >= total} onClick={() => onChange(page + 1)}>›</button>
  </nav>
}

function Facts({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return <dl className={styles.facts}>{rows.map(([label, value]) => <div key={label}><dt>{t(label)}</dt><dd>{value}</dd></div>)}</dl>
}

export function MessageStatus({ transport: item, bindings }: {
  transport: MessageTransportSummary
  bindings: Array<{ platform: string; external_user_id: string; external_chat_id: string | null; chat_type: string | null; match_priority: number }>
}) {
  const matched = bindings.filter(binding => binding.platform === item.platform)
  return <div className={styles.content}>
    <section><h3>{t('连接概况')}</h3><Facts rows={[
      ['绑定用户', item.bound_user], ['连接状态', t(item.connection_status === 'connected' ? '已连接' : '未连接')],
      ['最后心跳', formatMessageTime(item.last_check)], ['最后消息', formatMessageTime(item.last_message_at)],
      ['响应延迟', item.latency_ms === null ? '—' : `${item.latency_ms} ms`],
      ['今日收发', `${t('接收')} ${item.messages_received_today} / ${t('发送')} ${item.messages_sent_today}`],
    ]} /></section>
    <section><h3>{t('模块说明')}</h3><DocumentMarkdown content={item.description || t('暂无模块说明')} /></section>
    <section><h3>{t('路由与能力')}</h3><Facts rows={[
      ['平台', item.platform], ['模块标识', item.name],
      ['支持能力', item.capabilities.join(' / ')],
      ['允许工具', item.allowed_tools === null ? t('不限制') : item.allowed_tools.join('、') || t('无')],
      ['文件中转', t(item.file_relay_enabled ? '开启' : '关闭')],
      ['轮询间隔', item.polling_interval], ['健康检测间隔', item.health_interval],
    ]} /></section>
    <section><h3>{t('身份映射')}</h3>{matched.length ? <div className={styles.tableScroll}><table className={styles.table}>
      <thead><tr>{['外部用户', '会话类型', '外部会话', '匹配优先级'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead>
      <tbody>{matched.map((binding, index) => <tr key={index}><td>{binding.external_user_id}</td><td>{binding.chat_type || t('全部')}</td>
        <td>{binding.external_chat_id || t('全部')}</td><td>{binding.match_priority}</td></tr>)}</tbody>
    </table></div> : <p className={styles.muted}>{t('无')}</p>}</section>
  </div>
}

export function MessageFiles({ user, transport: item }: { user: string; transport: MessageTransportSummary }) {
  const [requestedPage, setPage] = useState(1)
  const files = item.temporary_files
  const total = Math.max(1, Math.ceil((files?.length || 0) / 12))
  const page = Math.min(requestedPage, total)
  return <div className={styles.content}>
    <div className={styles.sectionHead}><div><h3>{t('临时文件')}</h3><p className={styles.muted}>{item.files_path}</p></div>
      <span className={styles.muted}>{item.temporary_file_count} {t('个')} · {formatMessageBytes(item.temporary_file_bytes)}</span></div>
    {files === undefined ? <div className={styles.empty}>{t('当前服务未提供文件清单，请更新后端后重试。')}</div>
      : !files.length ? <div className={styles.empty}><FileText size={30} /><h3>{t('暂无临时文件')}</h3></div>
        : <><div className={styles.tableScroll}><table className={styles.table}>
          <thead><tr>{['文件名', '大小', '更新时间', '操作'].map(label => <th key={label}>{t(label)}</th>)}</tr></thead>
          <tbody>{files.slice((page - 1) * 12, page * 12).map(file => <tr key={file.relative_path}>
            <td><strong>{file.name}</strong><small>{file.relative_path}</small></td><td>{formatMessageBytes(file.size)}</td>
            <td>{formatMessageTime(file.updated_at)}</td><td><a className={styles.download}
              href={messageFileDownloadUrl(user, item.id, file.relative_path)} download={file.name}
              title={t('下载')} aria-label={t('下载')}><Download size={16} /></a></td>
          </tr>)}</tbody>
        </table></div><MessagePager page={page} total={total} onChange={setPage} /></>}
  </div>
}

interface MessageExchange {
  id: string
  entries: MessageLogEntry[]
}

function messageExchanges(entries: MessageLogEntry[]) {
  const groups = new Map<string, MessageExchange>()
  for (const entry of entries) {
    if (entry.kind === 'system') continue
    // An absent correlation ID is a standalone record, not a guessed reply.
    const id = JSON.stringify([entry.chat_type, entry.chat_id,
      entry.exchange_id ? ['exchange', entry.exchange_id] : ['record', entry.id]])
    let group = groups.get(id)
    if (!group) { group = { id, entries: [] }; groups.set(id, group) }
    group.entries.push(entry)
  }
  return [...groups.values()]
}

function MessageRecord({ entry, log = false }: { entry: MessageLogEntry; log?: boolean }) {
  return <article className={`${styles.message} ${log && entry.direction === 'send' ? styles.outgoing : ''}`}>
    <header>{log ? <span>{entry.direction === 'send' ? <ArrowUpRight size={14} /> : <ArrowDownLeft size={14} />}
      {t(entry.direction === 'send' ? '发送' : '接收')}</span> : null}
      <span>{t(entry.kind === 'file' ? '文件' : entry.kind === 'system' ? '系统' : '文本')}</span>
      <time>{formatMessageTime(entry.timestamp)}</time>
      <span className={entry.success ? styles.ok : styles.failed}>{t(entry.success ? '成功' : '失败')}</span></header>
    <pre>{entry.content || t('（无文本正文）')}</pre>
    {entry.file_path ? <div className={styles.filePath}><FileText size={13} />{entry.file_path}{entry.size != null ? ` · ${formatMessageBytes(entry.size)}` : ''}</div> : null}
    {log ? <footer><span>{entry.chat_type || '—'} · {entry.chat_id || '—'}</span><span>{entry.source}</span></footer> : null}
  </article>
}

function MessageExchangeCard({ exchange, direction }: { exchange: MessageExchange; direction?: MessageLogEntry['direction'] }) {
  const [expanded, setExpanded] = useState(false)
  const contentId = useId()
  const latest = exchange.entries[0]
  const directions = [
    { direction: 'receive', label: '接收', short: '收', Icon: ArrowDownLeft },
    { direction: 'send', label: '发送', short: '发', Icon: ArrowUpRight },
  ].filter(side => !direction || side.direction === direction).map(side => {
    const entries = exchange.entries.filter(entry => entry.direction === side.direction).reverse()
    const text = entries.map(entry => entry.content || entry.file_path || '').join(' · ').replace(/\s+/g, ' ')
    return { ...side, entries, preview: text.length > 240 ? `${text.slice(0, 240)}…` : text }
  })
  return <article className={styles.exchange}>
    <button type="button" className={styles.exchangeSummary} aria-expanded={expanded} aria-controls={contentId}
      aria-label={t(expanded ? '收起消息' : '展开消息')} onClick={() => setExpanded(value => !value)}>
      <span className={styles.exchangeMeta}><span>{latest.chat_type} · {latest.chat_id}</span>
        <time>{formatMessageTime(latest.timestamp)}</time>
        {exchange.entries.some(entry => !entry.success) ? <CircleAlert size={14} className={styles.failed} aria-label={t('失败')} /> : null}
        <ChevronDown size={15} className={styles.chevron} /></span>
      {directions.map(({ direction, label, short, Icon, preview }) => <span key={direction} className={styles.exchangePreview}>
        <span className={styles.previewDirection} title={t(label)}><Icon size={14} />{t(short)}</span>
        <span className={styles.previewText}>{preview}</span>
      </span>)}
    </button>
    {expanded ? <div id={contentId} className={styles.exchangeBody}>
      {directions.map(({ direction, label, Icon, entries }) => <section key={direction} className={styles.exchangeSide} aria-label={t(label)}>
        <header><Icon size={15} />{t(label)}</header>
        <div className={styles.exchangeEntries}>{entries.map(entry => <MessageRecord key={entry.id} entry={entry} />)}</div>
      </section>)}
    </div> : null}
  </article>
}

export function MessageHistory({ transport: item, mode }: { transport: MessageTransportSummary; mode: 'messages' | 'logs' }) {
  const [filter, setFilter] = useState('all')
  const [requestedPage, setPage] = useState(1)
  const entries = item.logs.filter(entry => (
    (mode === 'logs' || entry.kind !== 'system')
    && (filter === 'all' || entry.direction === filter || entry.kind === filter)
  ))
  const exchanges = mode === 'messages' ? messageExchanges(entries) : []
  const count = mode === 'messages' ? exchanges.length : entries.length
  const total = Math.max(1, Math.ceil(count / 10))
  const page = Math.min(requestedPage, total)
  const visible = entries.slice((page - 1) * 10, page * 10)
  return <div className={styles.content}>
    <div className={styles.sectionHead}>
      <div className={styles.filters} role="group" aria-label={t('日志分类')}>
        {(['all', 'receive', 'send', 'file', ...(mode === 'logs' ? ['system'] : [])]).map(value => <button type="button" key={value}
          aria-pressed={value === filter} onClick={() => { setFilter(value); setPage(1) }}>
          {t(({ all: '全部', receive: '接收', send: '发送', file: '文件', system: '系统' } as Record<string, string>)[value])}
        </button>)}
      </div>
    </div>
    {item.logs_truncated ? <div className={styles.notice} role="status">{t('日志已截断：每个模块最多返回 500 条记录，这里不代表完整历史。')}</div> : null}
    {!count ? <div className={styles.empty}>{mode === 'messages' ? <MessageSquare size={30} /> : <Terminal size={30} />}
      <h3>{t('当前分类暂无记录')}</h3></div> : <div className={styles.history}>
      {mode === 'messages' ? exchanges.slice((page - 1) * 10, page * 10).map(exchange => <MessageExchangeCard key={exchange.id}
        exchange={exchange} direction={filter === 'receive' || filter === 'send' ? filter : undefined} />)
        : visible.map(entry => <MessageRecord key={entry.id} entry={entry} log />)}
    </div>}
    <MessagePager page={page} total={total} onChange={setPage} />
  </div>
}

export function MessageDiagnostics({ transport: item, issues, uiError }: {
  transport: MessageTransportSummary; issues: Array<{ name: string; error: string }>; uiError?: string
}) {
  const ownIssues = issues.filter(issue => issue.name === item.id)
  return <div className={styles.content}>
    <section><h3>{t('运行诊断')}</h3>
      {!item.last_error && !ownIssues.length && !uiError ? <p className={styles.healthNote}><CheckCircle2 size={17} />{t('当前快照未报告异常')}</p> : null}
      {item.last_error ? <div className={styles.error} role="alert">{String(typeof item.last_error === 'object' ? JSON.stringify(item.last_error) : item.last_error)}</div> : null}
      {ownIssues.map((issue, index) => <div key={index} className={styles.error} role="alert">{issue.error}</div>)}
      {uiError ? <div className={styles.error} role="alert">{uiError}</div> : null}
      <Facts rows={[
        ['健康状态', item.health], ['状态 API 已导入', t(item.api_imported ? '是' : '否')],
        ['日志轮转', item.log_rotation], ['今日日志', item.today_log_count],
        ['模块路径', item.path], ['临时文件目录', item.files_path], ['日志数据库', item.log_path],
        ['消息缓冲', item.message_buffer],
        ...Object.entries(item.modules).map(([key, value]): [string, string] => [key, value]),
      ]} />
    </section>
  </div>
}
