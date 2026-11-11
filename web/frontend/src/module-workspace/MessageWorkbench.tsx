import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useBlocker, useOutletContext, useSearchParams } from 'react-router-dom'
import { ArrowDownLeft, ArrowLeft, ArrowUpRight, Copy, Expand, Globe, MessageSquare, Minimize2, Plug, RefreshCw, ShieldCheck, Trash2 } from 'lucide-react'
import * as core from '../api/client'
import { GlobalConfirmDialog } from '../components/GlobalConfirmDialog'
import type { ShellOutletContext } from '../components/appShellTypes'
import { t, useLanguage } from '../i18n'
import { copyText } from '../utils/clipboard'
import * as api from './api'
import { MessageDiagnostics, MessageFiles, MessageHistory, MessagePager, MessageStatus } from './MessageViews'
import { WorkspaceContent } from './WorkspaceContent'
import styles from './Messages.module.css'

const builtinPages = [
  { id: 'status', label: '状态' }, { id: 'files', label: '文件' }, { id: 'messages', label: '消息' },
  { id: 'logs', label: '日志' }, { id: 'diagnostics', label: '诊断' },
]
const permissionLabels: Record<string, string> = {
  'module.status.read': '读取本模块状态与采集数据', 'module.config.read': '读取本模块配置（密钥仅显示是否已设置）',
  'module.config.write': '修改本模块配置', 'module.action.invoke': '执行本模块声明的操作',
}

export function MessageWorkbench() {
  const { user } = useOutletContext<ShellOutletContext>()
  return <MessageWorkspace key={user} user={user} />
}

function MessageWorkspace({ user }: { user: string }) {
  useLanguage()
  const [params, setParams] = useSearchParams()
  const storageKey = `kemo-message-workspace:${user}`
  const [remembered] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') as { module?: string; page?: string } }
    catch { return {} }
  })
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null)
  const [dialog, setDialog] = useState<'delete' | 'grant' | 'revoke' | null>(null)
  const blocker = useBlocker(() => dirtyRef.current)
  const status = useQuery({
    queryKey: ['message-workspace-status', user],
    queryFn: ({ signal }) => core.getMessageStatus(user, signal),
    enabled: Boolean(user), refetchInterval: 30000, refetchOnWindowFocus: false, retry: false,
  })
  const inventory = useQuery({
    queryKey: ['module-workspaces', user, 'message'],
    queryFn: ({ signal }) => api.inventory(user, 'message', signal),
    enabled: Boolean(user), refetchInterval: 30000, refetchOnWindowFocus: false, retry: false,
  })
  const transports = status.data?.transports.filter(item => item.bound_user === user) || []
  const selectedId = params.get('module') || remembered.module
  const transport = transports.find(item => item.id === selectedId) || transports[0]
  const item = inventory.data?.items.find(item => item.module === transport?.id)
  const customPages = item?.pages.filter(page => page.type !== 'builtin' || page.view === 'docs')
    .map(page => ({ id: `custom:${page.id}`, label: page.label })) || []
  const pages = [...builtinPages, ...customPages]
  const requestedPage = params.get('page') || remembered.page || 'status'
  // Keep a requested custom tab while its independent inventory request is loading.
  const activeId = pages.some(page => page.id === requestedPage) || inventory.isLoading && requestedPage.startsWith('custom:')
    ? requestedPage : 'status'
  const customPage = item?.pages.find(page => `custom:${page.id}` === activeId)
  const totalListPages = Math.max(1, Math.ceil(transports.length / 6))
  const page = Math.min(Math.floor(Math.max(0, transports.findIndex(item => item.id === transport?.id)) / 6) + 1, totalListPages)
  const visible = transports.slice((page - 1) * 6, page * 6)
  const needsPermission = customPage?.type === 'web' && customPage.permissions?.some(value => !item?.granted_permissions.includes(value))
  const refresh = useCallback(async () => {
    await Promise.all([status.refetch({ throwOnError: true }), inventory.refetch({ throwOnError: true })])
  }, [status.refetch, inventory.refetch])
  const changeDirty = useCallback((value: boolean) => { dirtyRef.current = value; setDirty(value) }, [])

  useEffect(() => {
    if (!transport || inventory.isLoading) return
    try { localStorage.setItem(storageKey, JSON.stringify({ module: transport.id, page: activeId })) }
    catch { /* Browser storage is optional; URL state still works. */ }
  }, [transport?.id, activeId, inventory.isLoading, storageKey])
  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) { event.preventDefault(); event.returnValue = '' }
    }
    window.addEventListener('beforeunload', beforeUnload)
    return () => window.removeEventListener('beforeunload', beforeUnload)
  }, [])

  const navigate = (action: () => void) => {
    if (dirtyRef.current) setPendingNavigation(() => action)
    else { action(); changeDirty(false); setError(''); setFeedback('') }
  }
  const select = (module: string, page: string) => {
    if (module === transport?.id && page === activeId) return
    navigate(() => {
      const next = new URLSearchParams(params)
      next.set('module', module); next.set('page', page)
      setParams(next, { replace: true })
    })
  }
  const operation = async (action: () => Promise<unknown>, message: string) => {
    if (busy) return
    setBusy(true); setError(''); setFeedback('')
    try { await action(); setDialog(null); await refresh(); setFeedback(t(message)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('模块操作失败')) }
    finally { setBusy(false) }
  }

  return <main className={`${styles.workbench} ${expanded ? styles.expanded : ''}`} aria-label={t('外部消息工作区')}>
    <header className={styles.pageHeader}><h1>{t('外部消息')}</h1>
      <button type="button" className={styles.iconButton} title={t('刷新')} aria-label={t('刷新')} disabled={busy || status.isFetching}
        onClick={() => { void refresh().catch(cause => setError(cause.message)) }}><RefreshCw size={16} /></button>
    </header>
    {status.error ? <div className={styles.error} role="alert">{status.data ? t('刷新失败，正在显示上次成功的数据。') : t('消息模块加载失败')}
      {' '}{status.error.message}<button type="button" onClick={() => { void status.refetch() }}>{t('重试')}</button></div> : null}
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    {feedback ? <div className={styles.notice} role="status">{feedback}</div> : null}
    <div className={styles.columns}>
      <aside className={styles.sidebar} aria-label={t('已绑定消息模块')}>
        <div className={styles.listHeading}><span><MessageSquare size={15} />{t('已绑定消息模块')}</span>
          <small>{!status.data ? '—' : transports.length}</small></div>
        <div className={styles.moduleList}>
          {visible.map(entry => <button type="button" key={entry.id} className={styles.moduleButton}
            aria-label={entry.display_name} aria-pressed={entry.id === transport?.id} onClick={() => select(entry.id, activeId)}>
            <div className={styles.moduleTitle}><Globe size={18} /><strong>{entry.display_name}</strong>
              <span role="img" title={t(entry.connection_status === 'connected' ? '已连接' : '未连接')}
                aria-label={t(entry.connection_status === 'connected' ? '已连接' : '未连接')}
                className={`${styles.dot} ${entry.connection_status === 'connected' ? styles.connected : ''}`} /></div>
            <div className={styles.moduleMeta}><span>{entry.platform}</span>
              <span title={t('响应延迟')}>{entry.latency_ms === null ? '—' : `${entry.latency_ms} ms`}</span></div>
            <div className={styles.moduleCounters}>
              <span title={t('接收')} aria-label={`${t('接收')} ${entry.messages_received_today}`}><ArrowDownLeft size={13} />{entry.messages_received_today}</span>
              <span title={t('发送')} aria-label={`${t('发送')} ${entry.messages_sent_today}`}><ArrowUpRight size={13} />{entry.messages_sent_today}</span></div>
          </button>)}
          {!transports.length ? <div className={styles.empty}>{status.isLoading ? t('正在加载模块…') : status.error ? t('无法读取消息模块') : t('当前用户未绑定消息模块')}</div> : null}
        </div>
        <MessagePager page={page} total={totalListPages} onChange={next => select(transports[(next - 1) * 6].id, activeId)} />
      </aside>
        <section className={styles.details} aria-label={t('消息模块详情')}>
          {transport ? <>
            <header className={styles.moduleHeader}><div><h2>{transport.display_name}</h2><p>{transport.path}</p></div>
              <div className={styles.headerActions}>
                {dirty ? <small className={styles.unsaved} role="status">{t('未保存')}</small> : null}
                <span className={styles.state}><i className={`${styles.dot} ${transport.state === 'running' ? styles.connected : ''}`} />
                  {t(transport.state === 'running' ? '运行中' : transport.state === 'error' ? '异常' : '已停止')}</span>
                {customPage?.type === 'web' && item?.granted_permissions.length ? <button type="button" className={styles.iconButton}
                  title={t('撤销页面权限')} aria-label={t('撤销页面权限')} onClick={() => navigate(() => setDialog('revoke'))}><ShieldCheck size={15} /></button> : null}
                <button type="button" className={styles.iconButton} title={t('复制路径')} aria-label={t('复制路径')} onClick={() => {
                  void copyText(transport.path).then(() => setFeedback(t('路径已复制')), cause => setError(cause.message))
                }}><Copy size={15} /></button>
                <button type="button" className={styles.iconButton} title={expanded ? t('恢复布局') : t('放大工作区')} aria-label={expanded ? t('恢复布局') : t('放大工作区')}
                  onClick={() => setExpanded(value => !value)}>{expanded ? <Minimize2 size={16} /> : <Expand size={16} />}</button>
              </div></header>
            <nav className={styles.tabs} role="tablist" aria-label={t('消息模块功能')} onKeyDown={event => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
              const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role=tab]'))
              const current = tabs.indexOf(event.target as HTMLButtonElement)
              if (current === -1) return
              event.preventDefault()
              const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1
                : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
              tabs[next].focus(); select(transport.id, pages[next].id)
            }}>
              {pages.map(entry => <button key={entry.id} type="button" role="tab" aria-selected={activeId === entry.id}
                tabIndex={activeId === entry.id ? 0 : -1}
                id={`message-tab-${entry.id}`} aria-controls="message-tab-content" onClick={() => select(transport.id, entry.id)}>{t(entry.label)}</button>)}
            </nav>
            {inventory.error ? <div className={styles.notice} role="alert">{t('模块自定义功能加载失败，内置查看功能仍可使用。')}
              {' '}{inventory.error.message}<button type="button" onClick={() => { void inventory.refetch() }}>{t('重试')}</button></div> : null}
            <div className={styles.workspaceBody} role="tabpanel" id="message-tab-content" aria-labelledby={`message-tab-${activeId}`}>
              {activeId === 'status' ? <MessageStatus transport={transport} bindings={status.data?.bindings || []} /> : null}
              {activeId === 'files' ? <MessageFiles key={transport.id} user={user} transport={transport} /> : null}
              {activeId === 'messages' || activeId === 'logs' ? <MessageHistory key={`${transport.id}:${activeId}`} transport={transport} mode={activeId} /> : null}
              {activeId === 'diagnostics' ? <MessageDiagnostics transport={transport} issues={status.data?.issues || []} uiError={item?.ui_error || item?.panel_error} /> : null}
              {item && customPage ? needsPermission ? <div className={styles.permissionArea}><ShieldCheck size={24} /><div><h3>{t('插件页面需要授权')}</h3>
                <p>{t('由实例管理员审查后授予本模块能力。')}</p><ul>{customPage.permissions?.map(value => <li key={value}>{t(permissionLabels[value] || value)}</li>)}</ul>
                <button type="button" onClick={() => setDialog('grant')}>{t('审查并授权')}</button></div></div>
                : <WorkspaceContent key={`${item.key}:${customPage.id}`} user={user} item={item} page={customPage} refresh={refresh}
                  onDirtyChange={changeDirty} onExpand={() => setExpanded(true)} /> : null}
            </div>
            <footer className={styles.footer}>
              <button type="button" className={styles.iconButton} title={t('检测连接')} aria-label={t('检测连接')} disabled={busy}
                onClick={() => { void operation(() => core.checkMessageModule(user, transport.id), '消息模块连接状态已更新') }}><Plug size={16} /></button>
              <button type="button" className={`${styles.iconButton} ${styles.dangerButton}`} title={t('删除模块')} aria-label={t('删除模块')}
                disabled={busy} onClick={() => navigate(() => setDialog('delete'))}><Trash2 size={16} /></button>
            </footer>
          </> : <div className={styles.empty}><MessageSquare size={36} /><h2>{t('暂无可查看的消息模块')}</h2>
            <p>{t('在 message/out 的模块配置中设置 bound_user 后会显示在这里。')}</p></div>}
        </section>
    </div>
    <GlobalConfirmDialog open={Boolean(pendingNavigation) || blocker.state === 'blocked'} title={t('放弃未保存的修改？')}
      description={t('离开当前页面会丢失未保存的输入。')} icon={<ArrowLeft />} confirmLabel={t('放弃并继续')}
      onCancel={() => { setPendingNavigation(null); if (blocker.state === 'blocked') blocker.reset() }}
      onConfirm={() => { changeDirty(false); if (blocker.state === 'blocked') blocker.proceed(); else pendingNavigation?.(); setPendingNavigation(null) }} />
    <GlobalConfirmDialog open={Boolean(dialog)} title={dialog === 'delete' ? t('删除消息模块') : dialog === 'grant' ? t('授予插件页面权限？') : t('撤销插件页面权限？')}
      description={dialog === 'delete' ? t('这会删除实际的 message/out 模块包目录，不是单纯解绑。日志与临时文件也会一并移除。')
        : dialog === 'grant' ? t('页面将能够使用列出的本模块能力。后端插件代码仍按现有本机执行模型运行。') : t('当前页面实例将失效，后续操作会被拒绝。')}
      detail={dialog === 'grant' ? item?.requested_permissions.map(value => t(permissionLabels[value] || value)).join('\n') : transport?.path}
      icon={dialog === 'delete' ? <Trash2 /> : <ShieldCheck />} tone={dialog === 'delete' ? 'danger' : 'warning'}
      confirmLabel={dialog === 'delete' ? t('删除模块') : dialog === 'grant' ? t('确认授权') : t('确认撤销')} pending={busy} error={error}
      onCancel={() => { if (!busy) setDialog(null) }} onConfirm={() => {
        if (dialog === 'delete' && transport) void operation(() => core.deleteMessageModule(user, transport.id), '消息模块已删除')
        else if (item) void operation(() => api.permissions(user, item, dialog === 'grant' ? item.requested_permissions : []), '页面权限已更新')
      }} />
  </main>
}
