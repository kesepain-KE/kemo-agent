import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useBlocker, useLocation, useOutletContext, useSearchParams } from 'react-router-dom'
import { ArrowLeft, Boxes, Expand, Globe, Layers, Maximize2, Minimize2, Plus, RefreshCw, Search, ShieldCheck, Trash2, UserRound, Users } from 'lucide-react'
import * as core from '../api/client'
import { GlobalConfirmDialog } from '../components/GlobalConfirmDialog'
import type { ShellOutletContext } from '../components/appShellTypes'
import { t, useLanguage } from '../i18n'
import * as api from './api'
import type { ImportPreview, ModuleKind, Scope, WorkspaceItem } from './types'
import { WorkspaceContent } from './WorkspaceContent'
import styles from './Workbench.module.css'

const scopes: Scope[] = ['global', 'shared', 'user']
const scopeLabels = { global: '全局', shared: '共享', user: '用户' }
const scopeIcons = { all: Layers, global: Globe, shared: Users, user: UserRound }
const permissionLabels: Record<string, string> = {
  'module.status.read': '读取本模块状态与采集数据',
  'module.config.read': '读取本模块配置（密钥仅显示是否已设置）',
  'module.config.write': '修改本模块配置',
  'module.action.invoke': '执行本模块声明的操作',
}

export function ModuleWorkbench() {
  const { user } = useOutletContext<ShellOutletContext>()
  const location = useLocation()
  const kind: ModuleKind = location.pathname.startsWith('/sense') ? 'sense' : 'expand'
  return <Workbench key={`${user}:${kind}`} user={user} kind={kind} />
}

function Workbench({ user, kind }: { user: string; kind: ModuleKind }) {
  useLanguage()
  const [params, setParams] = useSearchParams()
  const storageKey = `kemo-module-workspace:${user}:${kind}`
  const [remembered] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') as { module?: string; page?: string; scope?: string; widths?: number[] } }
    catch { return {} }
  })
  const requestedScope = params.get('scope') || remembered.scope || 'all'
  const scope = kind === 'sense' ? 'global' : (['all', ...scopes].includes(requestedScope) ? requestedScope : 'all')
  const [search, setSearch] = useState('')
  const selected = params.get('module') || remembered.module || ''
  const pageId = params.get('page') || remembered.page || ''
  const [expanded, setExpanded] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState('')
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null)
  const [dialog, setDialog] = useState<'delete' | 'grant' | 'revoke' | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [installScope, setInstallScope] = useState<Scope>(kind === 'sense' ? 'global' : 'user')
  const [preview, setPreview] = useState<ImportPreview | null>(null)
  const [widths, setWidths] = useState(remembered.widths || [168, 270])
  const workspace = useRef<HTMLDivElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const dirtyRef = useRef(false)
  const [fullscreen, setFullscreen] = useState(false)
  const blocker = useBlocker(() => dirtyRef.current)
  const query = useQuery({
    queryKey: ['module-workspaces', user, kind],
    queryFn: ({ signal }) => api.inventory(user, kind, signal),
    enabled: Boolean(user), refetchInterval: 5000, refetchOnWindowFocus: false,
    retry: false,
  })
  const rows = query.data?.items || []
  const scoped = useMemo(() => rows.filter(item => scope === 'all' || item.scope === scope), [rows, scope])
  const filtered = useMemo(() => rows.filter(item => (
    (scope === 'all' || item.scope === scope)
    && `${item.title} ${item.module} ${item.description}`.toLowerCase().includes(search.toLowerCase())
  )), [rows, scope, search])
  const item = scoped.find(row => row.key === selected) || scoped[0]
  const page = item?.pages.find(entry => entry.id === pageId) || item?.pages.find(entry => entry.id === item.default_page) || item?.pages[0]
  const refresh = useCallback(async () => { await query.refetch() }, [query.refetch])
  const changeDirty = useCallback((value: boolean) => { dirtyRef.current = value; setDirty(value) }, [])

  useEffect(() => {
    if (query.data) {
      try { localStorage.setItem(storageKey, JSON.stringify({ module: item?.key || '', page: page?.id || '', scope, widths })) }
      catch { /* The workspace remains usable when browser storage is unavailable. */ }
      if (params.get('module') !== (item?.key || '') || params.get('page') !== (page?.id || '') || params.get('scope') !== scope) {
        const next = new URLSearchParams(params)
        next.set('module', item?.key || ''); next.set('page', page?.id || ''); next.set('scope', scope)
        setParams(next, { replace: true })
      }
    }
  }, [item?.key, page?.id, scope, widths, storageKey, params, setParams, query.data])

  useEffect(() => {
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) { event.preventDefault(); event.returnValue = '' }
    }
    const onFullscreen = () => setFullscreen(document.fullscreenElement === workspace.current)
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('fullscreenchange', onFullscreen)
    return () => { window.removeEventListener('beforeunload', beforeUnload); document.removeEventListener('fullscreenchange', onFullscreen) }
  }, [])

  const navigate = (action: () => void) => {
    if (dirtyRef.current) setPendingNavigation(() => action)
    else { action(); changeDirty(false); setError(''); setFeedback('') }
  }
  const select = (nextScope: string, nextItem?: WorkspaceItem, nextPage = nextItem?.default_page || '') => {
    const next = new URLSearchParams(params)
    next.set('scope', nextScope); next.set('module', nextItem?.key || ''); next.set('page', nextPage)
    setParams(next, { replace: true })
  }
  const operation = async (action: () => Promise<unknown>, message: string) => {
    if (busy) return
    setBusy(true); setError(''); setFeedback('')
    try {
      await action(); setDialog(null); setPreview(null); setShowImport(false)
      await refresh(); setFeedback(t(message))
    } catch (cause) { setError(cause instanceof Error ? cause.message : t('模块操作失败')) }
    finally { setBusy(false) }
  }
  const collect = () => item && operation(
    () => kind === 'sense' ? core.refreshSenseModule(user, item.module) : core.refreshExpandModule(user, item.scope, item.module),
    '采集已完成',
  )
  const toggle = () => item && navigate(() => { void operation(
    () => kind === 'sense' ? core.setSenseModuleEnabled(user, item.module, !item.enabled) : core.setExpandModuleEnabled(user, item.scope, item.module, !item.enabled),
    '模块启用状态已更新',
  ) })
  const destroy = () => item && operation(
    () => kind === 'sense' ? core.deleteSenseModule(user, item.module) : core.deleteExpandModule(user, item.module),
    '模块已删除',
  )
  const requestFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else if (workspace.current?.requestFullscreen) await workspace.current.requestFullscreen()
      else setExpanded(true)
    } catch (cause) {
      setExpanded(true)
      setFeedback(t('浏览器未允许原生全屏，已放大工作区：{reason}', { reason: cause instanceof Error ? cause.message : t('无法进入全屏') }))
    }
  }
  const resize = (index: number, event: React.PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId)
    const start = event.clientX
    const original = widths[index]
    const move = (next: PointerEvent) => setWidths(current => current.map((value, i) => i === index ? Math.max(index === 0 ? 140 : 220, Math.min(index === 0 ? 240 : 420, original + next.clientX - start)) : value))
    const stop = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop, { once: true })
  }
  const upload = async (file: File) => {
    if (file.size > 16 * 1024 * 1024) { setError(t('安装包不能超过 16 MiB')); return }
    setBusy(true); setError('')
    try { setPreview(await api.previewImport(user, kind, installScope, file)) }
    catch (cause) { setError(cause instanceof Error ? cause.message : t('安装包校验失败')) }
    finally { setBusy(false) }
  }

  return <main className={`${styles.workbench} ${expanded ? styles.expanded : ''}`}>
    <header className={styles.pageHeader}>
      <div><h1>{kind === 'sense' ? t('感知') : t('拓展')}</h1><p>{t('选择模块与功能，在工作区中完成配置、监控与管理。')}</p></div>
      <div className={styles.headerActions}>
        <label className={styles.search}><Search size={16} /><input aria-label={t('搜索模块')} placeholder={t('搜索模块')} value={search}
          onChange={event => setSearch(event.target.value)} /></label>
        <button type="button" onClick={() => { void refresh() }} disabled={query.isFetching}><RefreshCw size={15} className={query.isFetching ? styles.spinning : ''} />{t('刷新')}</button>
        <button type="button" onClick={() => navigate(() => setShowImport(true))}><Plus size={15} />{t('导入模块')}</button>
      </div>
    </header>
    {(error || query.error) ? <div className={styles.error} role="alert">{error || query.error?.message}</div> : null}
    {feedback ? <div className={styles.notice} role="status">{feedback}</div> : null}
    {showImport ? <section className={styles.importArea}>
      <div><h2>{t('导入本地模块')}</h2><p>{t('安装包包含可执行后端代码，请审查来源、入口与权限。更新保留配置和运行数据。')}</p></div>
      <div className={styles.headerActions}>
        {kind === 'expand' ? <select aria-label={t('安装作用域')} value={installScope} disabled={busy || !!preview} onChange={event => setInstallScope(event.target.value as Scope)}>{scopes.map(value => <option key={value} value={value}>{t(scopeLabels[value])}</option>)}</select> : <span>{t('全局感知')}</span>}
        <input ref={fileInput} type="file" accept=".zip" hidden onChange={event => { const file = event.target.files?.[0]; if (file) void upload(file); event.target.value = '' }} />
        <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}>{t('选择 ZIP')}</button>
        <button type="button" disabled={busy} onClick={() => { setShowImport(false); setPreview(null) }}>{t('关闭')}</button>
      </div>
      {preview ? <div className={styles.importPreview}>
        <strong>{preview.title} · {t(scopeLabels[preview.scope])} · {preview.updating ? t('更新现有模块') : t('新增模块')}</strong>
        <p>{t('后端入口')}：{preview.entrypoints.join('、')}</p>
        <p>{t('申请权限')}：{preview.permissions.map(value => t(permissionLabels[value] || value)).join('、') || t('无')}</p>
        <details><summary>{t('文件清单')}（{preview.files.length}）</summary><pre className={styles.code}>{preview.files.join('\n')}</pre></details>
        <button type="button" disabled={busy} onClick={() => { void operation(() => api.installImport(user, preview), '模块安装完成，请审查新页面权限') }}>{busy ? t('正在安装…') : t('确认安装')}</button>
      </div> : null}
    </section> : null}
    <div className={styles.columns} style={{ '--scope-width': `${widths[0]}px`, '--module-list-width': `${widths[1]}px` } as React.CSSProperties}>
      <aside className={styles.scopeNav} aria-label={t('模块作用域')}>
        <div className={styles.listHeader}><strong>{t('模块')} <span>{query.data?.summary.total ?? 0}</span></strong><small>{query.data?.summary.enabled ?? 0} {t('已启用')} · {query.data?.summary.invalid ?? 0} {t('异常')}</small></div>
        <div className={styles.scopes} role="group" aria-label={t('作用域筛选')}>
          {(kind === 'sense' ? ['global'] as const : ['all', ...scopes] as const).map(value => {
            const Icon = scopeIcons[value]
            return <button type="button" key={value} aria-pressed={scope === value}
              onClick={() => navigate(() => {
                const next = rows.find(row => value === 'all' || row.scope === value)
                select(value, next)
              })}>
              <Icon size={16} /><span>{value === 'all' ? t('全部') : t(scopeLabels[value])}</span><small>{rows.filter(row => value === 'all' || row.scope === value).length}</small>
            </button>
          })}
        </div>
      </aside>
      <div className={styles.separator} role="separator" aria-label={t('调整作用域栏宽度')} aria-orientation="vertical" tabIndex={0}
        onPointerDown={event => resize(0, event)} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') setWidths(([a, b]) => [Math.max(140, Math.min(240, a + (event.key === 'ArrowRight' ? 10 : -10))), b]) }} />
      <aside className={styles.moduleList} aria-label={t('模块列表')}>
        <div className={styles.listHeader}><strong>{scope === 'all' ? t('全部模块') : t(scopeLabels[scope as Scope])} <span>{filtered.length}</span></strong><small>{t('选择模块查看详情')}</small></div>
        <div className={styles.listBody}>
          {query.isLoading ? <p className={styles.empty}>{t('正在读取模块…')}</p> : !filtered.length ? <p className={styles.empty}>{t('没有匹配的模块')}</p> : filtered.map(row => <button
            type="button" key={row.key} className={`${styles.moduleButton} ${item?.key === row.key ? styles.selected : ''}`} aria-pressed={item?.key === row.key}
            onClick={() => navigate(() => select(scope, row))}>
            <span className={styles.moduleTitle}><Boxes size={17} /><strong>{row.title}</strong><span className={row.valid && row.health === '正常' ? styles.dot : styles.badDot} /></span>
            <small>{t(scopeLabels[row.scope])} · {row.module}</small><span className={styles.moduleDescription}>{row.description}</span>
            <span className={styles.tags}><span>{row.enabled ? t('已启用') : t('未启用')}</span><span>{row.injected_tokens || 0} tok</span>{row.ui_error ? <span>{t('UI 异常')}</span> : null}</span>
          </button>)}
        </div>
      </aside>
      <div className={styles.separator} role="separator" aria-label={t('调整模块栏宽度')} aria-orientation="vertical" tabIndex={0}
        onPointerDown={event => resize(1, event)} onKeyDown={event => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') setWidths(([a, b]) => [a, Math.max(220, Math.min(420, b + (event.key === 'ArrowRight' ? 10 : -10)))]) }} />
      <section className={styles.details} aria-label={t('模块详情')}>
        {item && page ? <>
          <header className={styles.moduleHeader}>
            <div><div className={styles.breadcrumb}>{t(scopeLabels[item.scope])} / {item.module}</div><h2>{item.title}</h2><p>{item.description}</p>
              <div className={styles.tags}><span>{item.health}</span><span>{item.active_for_main_agent ? t('已注入') : t('未注入')}</span><span>{item.injected_tokens || 0} tok</span>{item.recent_update ? <span>{item.recent_update}</span> : null}</div>
            </div>
            <div className={styles.headerActions}>
              <button type="button" disabled={busy || !item.valid} onClick={() => { void collect() }}><RefreshCw size={14} />{t('刷新采集')}</button>
              {(kind === 'sense' || item.scope !== 'user') ? <button type="button" disabled={busy || !item.valid} onClick={toggle}>{item.enabled ? t('停用') : t('启用')}</button> : null}
              {(kind === 'sense' || item.scope === 'user') ? <button type="button" disabled={busy} onClick={() => navigate(() => setDialog('delete'))}><Trash2 size={14} />{t('删除')}</button> : null}
            </div>
          </header>
          <nav className={styles.functionTabs} aria-label={t('模块功能')}>
            {item.pages.map(entry => <button type="button" key={entry.id} aria-current={page.id === entry.id ? 'page' : undefined}
              onClick={() => navigate(() => select(scope, item, entry.id))}>{t(entry.label)}</button>)}
          </nav>
          {item.ui_error ? <div className={styles.error} role="alert">{item.ui_error}</div> : null}
          {page.type === 'web' && page.permissions?.some(value => !item.granted_permissions.includes(value)) ? <div className={styles.permissionArea}>
            <ShieldCheck size={20} /><div><strong>{t('插件页面需要授权')}</strong><p>{t('由实例管理员审查后授予本模块能力。')}</p>
              <ul>{page.permissions.map(value => <li key={value}>{t(permissionLabels[value] || value)}</li>)}</ul>
              <button type="button" disabled={busy} onClick={() => setDialog('grant')}>{t('审查并授权')}</button>
            </div>
          </div> : <div ref={workspace} className={styles.workspace}>
            <div className={styles.workspaceToolbar}><strong>{t(page.label)}{dirty ? <span className={styles.unsaved}> · {t('未保存')}</span> : null}</strong>
              <div className={styles.headerActions}>
                {page.type === 'web' && item.granted_permissions.length ? <button type="button" onClick={() => setDialog('revoke')} title={t('撤销页面权限')}><ShieldCheck size={14} /></button> : null}
                <button type="button" onClick={() => setExpanded(value => !value)} title={expanded ? t('恢复布局') : t('放大工作区')} aria-label={expanded ? t('恢复布局') : t('放大工作区')}>{expanded ? <><ArrowLeft size={14} />{t('恢复')}</> : <Expand size={14} />}</button>
                <button type="button" onClick={() => { void requestFullscreen() }} aria-label={fullscreen ? t('退出全屏') : t('全屏')} title={fullscreen ? t('退出全屏') : t('全屏')}>{fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
              </div>
            </div>
            <div className={styles.workspaceBody}><WorkspaceContent key={`${item.key}:${page.id}`} user={user} item={item} page={page}
              refresh={refresh} onDirtyChange={changeDirty} onExpand={() => setExpanded(true)} /></div>
          </div>}
        </> : <div className={styles.empty}><Boxes size={36} /><h2>{t('选择一个模块开始管理')}</h2><p>{t('支持通用配置面板和模块专属网页。')}</p></div>}
      </section>
    </div>
    <GlobalConfirmDialog open={Boolean(pendingNavigation) || blocker.state === 'blocked'}
      title={t('放弃未保存的修改？')} description={t('离开当前页面会丢失未保存的输入。')} icon={<ArrowLeft />}
      confirmLabel={t('放弃并继续')} onCancel={() => { setPendingNavigation(null); if (blocker.state === 'blocked') blocker.reset() }}
      onConfirm={() => { changeDirty(false); if (blocker.state === 'blocked') blocker.proceed(); else pendingNavigation?.(); setPendingNavigation(null) }} />
    <GlobalConfirmDialog open={Boolean(dialog)} title={dialog === 'delete' ? t('删除模块？') : dialog === 'grant' ? t('授予插件页面权限？') : t('撤销插件页面权限？')}
      description={dialog === 'delete' ? t('将删除模块目录及其中的配置和数据，此操作无法撤销。') : dialog === 'grant'
        ? t('页面将能够使用列出的本模块能力。后端插件代码仍按现有本机执行模型运行。') : t('当前页面实例将失效，后续操作会被拒绝。')}
      detail={dialog === 'grant' ? item?.requested_permissions.map(value => t(permissionLabels[value] || value)).join('\n') : item?.title}
      icon={dialog === 'delete' ? <Trash2 /> : <ShieldCheck />} tone={dialog === 'delete' ? 'danger' : 'warning'}
      confirmLabel={dialog === 'delete' ? t('确认删除') : dialog === 'grant' ? t('确认授权') : t('确认撤销')} pending={busy} error={error}
      onCancel={() => { if (!busy) setDialog(null) }} onConfirm={() => {
        if (dialog === 'delete') void destroy()
        else if (item) void operation(() => api.permissions(user, item, dialog === 'grant' ? item.requested_permissions : []), dialog === 'grant' ? '页面权限已更新' : '页面权限已撤销')
      }} />
  </main>
}
